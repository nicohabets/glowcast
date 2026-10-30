"""HTTP clients for the bench: the local Home Assistant, the product owner's
Home Assistant (a short allow-list of calls only), the Hue bridge (GET only)
and the screen helper. Tokens and keys are never printed or logged.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from collections.abc import Callable
from datetime import datetime
from typing import Any

import aiohttp

TIMEOUT = aiohttp.ClientTimeout(total=10)


class NotAllowed(Exception):
    """A call outside the bench's rules; never sent."""


class LocalHA:
    """The bench's own Home Assistant (in WSL): REST calls and state events."""

    def __init__(self, session: aiohttp.ClientSession, url: str, token: str) -> None:
        self.session = session
        self.url = url
        self._headers = {"Authorization": f"Bearer {token}"}
        self._token = token
        self.entities: dict[str, str] = {}  # key (switch, status, ...) -> entity id
        self.events: list[tuple[float, str, str, dict[str, Any]]] = []
        # light.turn_on calls (loose lights, #9): (time fired, service data).
        self.calls: list[tuple[float, dict[str, Any]]] = []
        self._task: asyncio.Task[None] | None = None
        self._listeners: list[Callable[[str, str], None]] = []

    async def get(self, path: str) -> Any:
        async with self.session.get(
            self.url + path, headers=self._headers, timeout=TIMEOUT
        ) as r:
            r.raise_for_status()
            return await r.json()

    async def call(self, domain: str, service: str, data: dict[str, Any]) -> None:
        async with self.session.post(
            f"{self.url}/api/services/{domain}/{service}",
            headers=self._headers,
            json=data,
            timeout=TIMEOUT,
        ) as r:
            r.raise_for_status()

    async def state(self, key: str) -> dict[str, Any]:
        return await self.get(f"/api/states/{self.entities[key]}")

    async def diagnostics(self, entry_id: str) -> dict[str, Any]:
        """Glowcast's own part of the download (under "data")."""
        return (await self.get(f"/api/diagnostics/config_entry/{entry_id}"))["data"]

    async def _ws(self, messages: list[dict[str, Any]]) -> list[Any]:
        async with self.session.ws_connect(
            self.url + "/api/websocket", timeout=10
        ) as ws:
            await ws.receive_json()
            await ws.send_json({"type": "auth", "access_token": self._token})
            if (await ws.receive_json()).get("type") != "auth_ok":
                raise RuntimeError("the local Home Assistant refused the token")
            results = []
            for i, message in enumerate(messages, 1):
                await ws.send_json({"id": i, **message})
                while True:
                    reply = await ws.receive_json()
                    if reply.get("id") == i and reply.get("type") == "result":
                        results.append(reply.get("result"))
                        break
            return results

    async def reload_entry(self, entry_id: str) -> None:
        """Reload a config entry (one that waits to retry its setup)."""
        async with self.session.post(
            f"{self.url}/api/config/config_entries/entry/{entry_id}/reload",
            headers=self._headers,
            timeout=TIMEOUT,
        ) as r:
            r.raise_for_status()

    async def registry(self) -> list[dict[str, Any]]:
        """The entity registry's entries (with their registry `id`)."""
        (registry,) = await self._ws([{"type": "config/entity_registry/list"}])
        return registry

    async def find_entities(self, entry_id: str) -> dict[str, str]:
        """The Glowcast entry's entities by their key (the unique id's suffix)."""
        (registry,) = await self._ws([{"type": "config/entity_registry/list"}])
        found = {}
        for entity in registry:
            if entity.get("config_entry_id") != entry_id:
                continue
            unique = entity["unique_id"]
            key = (
                "switch" if unique == entry_id else unique.removeprefix(f"{entry_id}_")
            )
            found[key] = entity["entity_id"]
        self.entities = found
        return found

    async def follow(self) -> None:
        """Record state changes of the Glowcast entities (WSL time.time()) and
        the light.turn_on calls (the time they were fired, WSL clock)."""
        ids = {v: k for k, v in self.entities.items()}

        async def run() -> None:
            async with self.session.ws_connect(self.url + "/api/websocket") as ws:
                await ws.receive_json()
                await ws.send_json({"type": "auth", "access_token": self._token})
                await ws.receive_json()
                await ws.send_json(
                    {"id": 1, "type": "subscribe_events", "event_type": "state_changed"}
                )
                await ws.send_json(
                    {"id": 2, "type": "subscribe_events", "event_type": "call_service"}
                )
                async for message in ws:
                    if message.type != aiohttp.WSMsgType.TEXT:
                        continue
                    data = json.loads(message.data)
                    if data.get("id") == 2 and data.get("type") == "event":
                        self._record_call(data["event"])
                        continue
                    event = data.get("event", {}).get("data", {})
                    key = ids.get(event.get("entity_id", ""))
                    if key is None or event.get("new_state") is None:
                        continue
                    new = event["new_state"]
                    self.events.append(
                        (time.time(), key, new["state"], new["attributes"])
                    )
                    for listener in self._listeners:
                        listener(key, new["state"])

        self._task = asyncio.create_task(run())

    def _record_call(self, event: dict[str, Any]) -> None:
        data = event.get("data", {})
        if data.get("domain") != "light" or data.get("service") != "turn_on":
            return
        with contextlib.suppress(KeyError, ValueError, TypeError):
            fired = datetime.fromisoformat(
                event["time_fired"].replace("Z", "+00:00")
            ).timestamp()
            self.calls.append((fired, dict(data.get("service_data") or {})))

    def states_of(self, key: str, start: float, end: float) -> list[str]:
        """The states the entity had between two WSL times (also the one before)."""
        before = [s for t, k, s, _ in self.events if k == key and t < start]
        during = [s for t, k, s, _ in self.events if k == key and start <= t <= end]
        return before[-1:] + during

    def first(self, key: str, state: str, after: float) -> float | None:
        for t, k, s, _ in self.events:
            if k == key and s == state and t >= after:
                return t
        return None

    async def wait_state(
        self, key: str, states: set[str], within: float
    ) -> tuple[str, float] | None:
        """Wait until the entity is in one of the states; (state, WSL time) or None."""
        deadline = time.monotonic() + within
        while time.monotonic() < deadline:
            with contextlib.suppress(aiohttp.ClientError, TimeoutError):
                state = (await self.state(key))["state"]
                if state in states:
                    return state, time.time()
            await asyncio.sleep(0.2)
        return None

    async def close(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task


class OwnerHA:
    """The product owner's Home Assistant: only the calls the bench may make.

    light.turn_on / light.turn_off on the bench lights, switch.turn_on /
    switch.turn_off on the TV's Glowcast switch, and reading their states.
    """

    def __init__(
        self,
        session: aiohttp.ClientSession,
        url: str,
        token: str,
        lights: list[str],
        tv_switch: str | None,
    ) -> None:
        self.session = session
        self.url = url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {token}"}
        self.lights = list(lights)
        self.tv_switch = tv_switch
        # Polled states of lights (loose lights, #9): (WSL time, entity, state).
        self.samples: list[tuple[float, str, dict[str, Any]]] = []
        self._task: asyncio.Task[None] | None = None

    async def state(self, entity_id: str) -> dict[str, Any]:
        if entity_id not in (*self.lights, self.tv_switch):
            raise NotAllowed("the bench only reads its own lights and the TV switch")
        async with self.session.get(
            f"{self.url}/api/states/{entity_id}", headers=self._headers, timeout=TIMEOUT
        ) as r:
            r.raise_for_status()
            return await r.json()

    async def call(self, domain: str, service: str, data: dict[str, Any]) -> None:
        entity = data.get("entity_id")
        allowed = (
            domain == "light"
            and service in ("turn_on", "turn_off")
            and entity in self.lights
        ) or (
            domain == "switch"
            and service in ("turn_on", "turn_off")
            and self.tv_switch is not None
            and entity == self.tv_switch
        )
        if not allowed:
            raise NotAllowed(f"{domain}.{service} is not one of the bench's calls")
        async with self.session.post(
            f"{self.url}/api/services/{domain}/{service}",
            headers=self._headers,
            json=data,
            timeout=TIMEOUT,
        ) as r:
            r.raise_for_status()

    def follow(self, entity_ids: list[str], interval: float = 1.0) -> None:
        """Poll the lights' states (about once per `interval`), until close()."""

        async def one(entity: str) -> None:
            with contextlib.suppress(aiohttp.ClientError, TimeoutError, ValueError):
                state = await self.state(entity)
                self.samples.append((time.time(), entity, state))

        async def run() -> None:
            while True:
                await asyncio.gather(*(one(e) for e in entity_ids))
                await asyncio.sleep(interval)

        self._task = asyncio.create_task(run())

    async def close(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task
            self._task = None


def restore_data(state: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """The light.turn_on/turn_off call that puts a light back in this state."""
    entity = state["entity_id"]
    if state["state"] != "on":
        return "turn_off", {"entity_id": entity}
    a = state.get("attributes", {})
    data: dict[str, Any] = {"entity_id": entity, "transition": 0}
    if a.get("brightness") is not None:
        data["brightness"] = a["brightness"]
    if a.get("color_mode") == "color_temp" and a.get("color_temp_kelvin"):
        data["color_temp_kelvin"] = a["color_temp_kelvin"]
    elif a.get("xy_color"):
        data["xy_color"] = a["xy_color"]
    return "turn_on", data


class Bridge:
    """The Hue bridge's CLIP v2 API, GET only, with the bench's own key."""

    AREAS = "/clip/v2/resource/entertainment_configuration"

    def __init__(self, session: aiohttp.ClientSession, host: str, app_key: str) -> None:
        self.session = session
        self.host = host
        self._headers = {"hue-application-key": app_key}
        self.samples: list[tuple[float, dict[str, Any]]] = []  # WSL time, area status
        self._task: asyncio.Task[None] | None = None

    async def _get(self, path: str) -> tuple[Any, Any]:
        async with self.session.get(
            f"https://{self.host}{path}",
            headers=self._headers,
            ssl=False,
            timeout=TIMEOUT,
        ) as r:
            r.raise_for_status()
            return r.headers, await r.json(content_type=None)

    async def application_id(self) -> str:
        headers, _ = await self._get("/auth/v1")
        return headers.get("hue-application-id", "")

    async def areas(self) -> list[dict[str, Any]]:
        return (await self._get(self.AREAS))[1].get("data", [])

    async def entertainment(self) -> list[dict[str, Any]]:
        """The entertainment services (each renders one light)."""
        return (await self._get("/clip/v2/resource/entertainment"))[1].get("data", [])

    async def area(self, area_id: str) -> dict[str, Any] | None:
        return next((a for a in await self.areas() if a["id"] == area_id), None)

    async def light(self, rid: str) -> dict[str, Any]:
        (light,) = (await self._get(f"/clip/v2/resource/light/{rid}"))[1]["data"]
        return {
            "on": light["on"]["on"],
            "brightness": light.get("dimming", {}).get("brightness"),
            "xy": [light["color"]["xy"]["x"], light["color"]["xy"]["y"]]
            if light.get("color")
            else None,
            "mirek": (light.get("color_temperature") or {}).get("mirek"),
            "mode": light.get("mode"),
        }

    def follow(self, area_id: str, interval: float = 0.5) -> None:
        """Poll the area's status and streamer (WSL time.time())."""

        async def run() -> None:
            while True:
                with contextlib.suppress(aiohttp.ClientError, TimeoutError, KeyError):
                    area = await self.area(area_id)
                    if area is not None:
                        self.samples.append(
                            (
                                time.time(),
                                {
                                    "status": area.get("status"),
                                    "streamer": (area.get("active_streamer") or {}).get(
                                        "rid"
                                    ),
                                },
                            )
                        )
                await asyncio.sleep(interval)

        self._task = asyncio.create_task(run())

    def statuses(self, start: float, end: float) -> list[dict[str, Any]]:
        return [s for t, s in self.samples if start <= t <= end]

    def first(self, status: str, after: float) -> float | None:
        for t, s in self.samples:
            if t >= after and s["status"] == status:
                return t
        return None

    async def wait_status(
        self, area_id: str, status: str, within: float
    ) -> float | None:
        deadline = time.monotonic() + within
        while time.monotonic() < deadline:
            with contextlib.suppress(aiohttp.ClientError, TimeoutError):
                area = await self.area(area_id)
                if area is not None and area.get("status") == status:
                    return time.time()
            await asyncio.sleep(0.25)
        return None

    async def close(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task


class Helper:
    """The screen helper's HTTP API (protocol 1)."""

    def __init__(self, session: aiohttp.ClientSession, host: str, port: int) -> None:
        self.session = session
        self.base = f"http://{host}:{port}"
        self.token: str | None = None
        self.helper_id: str | None = None

    async def pair(self, code: str) -> None:
        async with self.session.post(
            self.base + "/v1/pair", json={"code": code}, timeout=TIMEOUT
        ) as r:
            r.raise_for_status()
            reply = await r.json()
        self.token, self.helper_id = reply["token"], reply["id"]

    async def info(self) -> dict[str, Any]:
        async with self.session.get(
            self.base + "/v1/info",
            headers={"Authorization": f"Bearer {self.token}"},
            timeout=TIMEOUT,
        ) as r:
            r.raise_for_status()
            return await r.json()

    async def frame(self) -> tuple[int, bytes]:
        async with self.session.get(
            self.base + "/v1/frame",
            headers={"Authorization": f"Bearer {self.token}"},
            timeout=TIMEOUT,
        ) as r:
            return r.status, await r.read()
