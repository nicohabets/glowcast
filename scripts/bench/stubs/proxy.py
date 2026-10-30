"""Bench stub: lights that mirror lights of another Home Assistant (#9).

Only for the test bench's local Home Assistant (scripts/bench/). Never install
this on a real Home Assistant: it replaces the integration it is copied into
(`hue` or `matter`).

The bench copies this file into its `hue` and `matter` stubs, so that Glowcast's
loose-lights output sees one Hue network and one Thread network, as in a real
home. Each proxy light mirrors one light of the product owner's Home Assistant:
its state is read there about once per second, and `light.turn_on` and
`light.turn_off` are forwarded there with the same service data. Only the
lights in the entry's data are read or called; the address and the token are
never logged.
"""

import asyncio
import logging
from typing import Any

import aiohttp
from homeassistant.components.light import ColorMode, LightEntity, LightEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady, HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity_platform import AddEntitiesCallback

_LOGGER = logging.getLogger(__name__)

# The entry's data: {"bench_proxy": {"url", "token", "lights": [{"unique_id",
# "name", "source"}]}}; "source" is the light's entity id in the owner's HA.
PROXY = "bench_proxy"
PLATFORMS = ["light"]
POLL_SECONDS = 1.0
TIMEOUT = aiohttp.ClientTimeout(total=5)
# The service data a light command carries on to the owner's Home Assistant.
FORWARDED = (
    "brightness",
    "transition",
    "rgb_color",
    "rgbw_color",
    "rgbww_color",
    "xy_color",
    "hs_color",
    "color_temp_kelvin",
    "white",
    "flash",
)
FEATURES = LightEntityFeature.TRANSITION | LightEntityFeature.FLASH
GONE = ("unavailable", "unknown")
PREFERRED = (ColorMode.XY, ColorMode.HS, ColorMode.RGB, ColorMode.COLOR_TEMP)


def forward_data(source: str, kwargs: dict[str, Any]) -> dict[str, Any]:
    """The service data for the owner's light: the known keys, JSON-ready."""
    data: dict[str, Any] = {"entity_id": source}
    for key in FORWARDED:
        if key in kwargs and kwargs[key] is not None:
            value = kwargs[key]
            data[key] = list(value) if isinstance(value, tuple) else value
    return data


def capabilities(attributes: dict[str, Any]) -> dict[str, Any]:
    """What the proxy can do, from the owner light's state attributes."""
    modes = {
        ColorMode(mode)
        for mode in attributes.get("supported_color_modes") or []
        if mode in {m.value for m in ColorMode}
    } or {ColorMode.ONOFF}
    return {
        "modes": modes,
        "min_kelvin": attributes.get("min_color_temp_kelvin"),
        "max_kelvin": attributes.get("max_color_temp_kelvin"),
        "features": LightEntityFeature(
            int(attributes.get("supported_features") or 0) & int(FEATURES)
        ),
    }


def describe(err: BaseException) -> str:
    """An error without its text: an HTTP error's text carries the address."""
    status = getattr(err, "status", None)
    return f"{type(err).__name__} (status {status})" if status else type(err).__name__


class Owner:
    """The owner's Home Assistant, for the configured lights only."""

    def __init__(self, hass: HomeAssistant, url: str, token: str, lights: list[str]):
        self._session = async_get_clientsession(hass)
        self._url = url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {token}"}
        self._lights = set(lights)

    async def state(self, source: str) -> dict[str, Any]:
        if source not in self._lights:
            raise HomeAssistantError("not one of the bench's lights")
        async with self._session.get(
            f"{self._url}/api/states/{source}", headers=self._headers, timeout=TIMEOUT
        ) as response:
            response.raise_for_status()
            return await response.json()

    async def call(self, service: str, data: dict[str, Any]) -> None:
        if service not in ("turn_on", "turn_off") or data["entity_id"] not in (
            self._lights
        ):
            raise HomeAssistantError("not one of the bench's calls")
        async with self._session.post(
            f"{self._url}/api/services/light/{service}",
            headers=self._headers,
            json=data,
            timeout=TIMEOUT,
        ) as response:
            response.raise_for_status()


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """With proxy lights in the entry's data: set them up; else only load.

    The owner's lights are read here, before the platform: an owner that
    cannot be reached yet makes the entry retry (ConfigEntryNotReady).
    """
    if PROXY not in entry.data:
        return True
    block = entry.data[PROXY]
    owner = Owner(
        hass, block["url"], block["token"], [li["source"] for li in block["lights"]]
    )
    states = {}
    for light in block["lights"]:
        try:
            states[light["source"]] = await owner.state(light["source"])
        except (aiohttp.ClientError, TimeoutError, ValueError) as err:
            raise ConfigEntryNotReady(
                f"the owner's light could not be read: {describe(err)}"
            ) from None
    entry.runtime_data = (owner, states)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    if PROXY in entry.data:
        return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    return True


async def async_setup_lights(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """The proxy lights, each with its owner light's capabilities."""
    owner, states = entry.runtime_data
    lights = [
        ProxyLight(owner, light, states[light["source"]])
        for light in entry.data[PROXY]["lights"]
    ]
    async_add_entities(lights)

    async def poll() -> None:
        while True:
            await asyncio.sleep(POLL_SECONDS)
            await asyncio.gather(*(light.async_refresh() for light in lights))

    entry.async_create_background_task(hass, poll(), "bench proxy poll")


class ProxyLight(LightEntity):
    """One owner light, mirrored."""

    _attr_should_poll = False

    def __init__(
        self, owner: Owner, light: dict[str, str], state: dict[str, Any]
    ) -> None:
        self._owner = owner
        self._source = light["source"]
        self._attr_unique_id = light["unique_id"]
        self._attr_name = light["name"]
        caps = capabilities(state.get("attributes", {}))
        self._attr_supported_color_modes = caps["modes"]
        self._attr_supported_features = caps["features"]
        if caps["min_kelvin"]:
            self._attr_min_color_temp_kelvin = caps["min_kelvin"]
        if caps["max_kelvin"]:
            self._attr_max_color_temp_kelvin = caps["max_kelvin"]
        self._apply(state)

    def _apply(self, state: dict[str, Any]) -> None:
        a = state.get("attributes", {})
        self._attr_available = state.get("state") not in GONE
        self._attr_is_on = state.get("state") == "on"
        self._attr_brightness = a.get("brightness")
        mode = a.get("color_mode")
        modes = self._attr_supported_color_modes or {ColorMode.ONOFF}
        if mode in {m.value for m in modes}:
            self._attr_color_mode = ColorMode(mode)
        else:  # off, or a mode the proxy does not know: a supported one
            self._attr_color_mode = next(
                (m for m in PREFERRED if m in modes), sorted(modes)[0]
            )
        for key in ("hs_color", "xy_color", "rgb_color", "rgbw_color", "rgbww_color"):
            value = a.get(key)
            setattr(self, f"_attr_{key}", tuple(value) if value else None)
        self._attr_color_temp_kelvin = a.get("color_temp_kelvin")

    async def async_refresh(self) -> None:
        try:
            state = await self._owner.state(self._source)
        except (aiohttp.ClientError, TimeoutError, ValueError) as err:
            if self._attr_available:
                _LOGGER.warning("A proxy light lost its light: %s", describe(err))
                self._attr_available = False
                self.async_write_ha_state()
            return
        self._apply(state)
        self.async_write_ha_state()

    async def _forward(self, service: str, kwargs: dict[str, Any]) -> None:
        try:
            await self._owner.call(service, forward_data(self._source, kwargs))
        except (aiohttp.ClientError, TimeoutError) as err:
            raise HomeAssistantError(
                f"the owner's light did not take the command: {describe(err)}"
            ) from None

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._forward("turn_on", kwargs)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._forward("turn_off", kwargs)
