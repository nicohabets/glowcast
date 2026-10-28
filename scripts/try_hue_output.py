"""Try HueEntertainmentOutput against a real Hue bridge, outside Home Assistant.

Runs the real output inside a throwaway Home Assistant test instance. It shows
left red / centre green / right blue for a few seconds, then a very dark grey,
then stops; the lights should return to their previous state. It prints
timings and statuses only (no keys). Needs the test requirements from the CI
workflow plus hue-entertainment (see scripts/README.md).

Environment: GLOWCAST_HUE_HOST, GLOWCAST_HUE_APP_KEY, GLOWCAST_HUE_CLIENT_KEY
and, except with --list, GLOWCAST_HUE_AREA_ID.

    python scripts/try_hue_output.py --list      # areas with id and channel count
    python scripts/try_hue_output.py             # pattern, dark, stop
    python scripts/try_hue_output.py --pause 11  # dark, then 11 s without sends
    python scripts/try_hue_output.py --kill      # exit without stopping (stale test)
"""

import argparse
import asyncio
import os
import sys
import tempfile
import time
from pathlib import Path

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_test_home_assistant,
)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from custom_components.glowcast.core.models import ChannelColor  # noqa: E402
from custom_components.glowcast.outputs.hue_entertainment import (  # noqa: E402
    AREAS_PATH,
    HueEntertainmentOutput,
)

BRIDGE_ID = "bridge"
RED, GREEN, BLUE = (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)
VERY_DARK = (0.01, 0.01, 0.01)


async def _areas(hass: HomeAssistant, host: str, key: str) -> list[dict]:
    session = async_get_clientsession(hass)
    async with session.get(
        f"https://{host}{AREAS_PATH}", headers={"hue-application-key": key}, ssl=False
    ) as response:
        response.raise_for_status()
        return (await response.json())["data"]


async def _stream(
    output: HueEntertainmentOutput, colors: list[ChannelColor], seconds: float
) -> None:
    """Send at 25 Hz and check the stream, like the pipeline will."""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        output.send(colors)
        await output.check()
        await asyncio.sleep(1 / output.max_rate_hz)


async def main(args: argparse.Namespace) -> None:
    host = os.environ["GLOWCAST_HUE_HOST"]
    key = os.environ["GLOWCAST_HUE_APP_KEY"]
    client_key = os.environ["GLOWCAST_HUE_CLIENT_KEY"]

    with tempfile.TemporaryDirectory() as config_dir:
        async with async_test_home_assistant(config_dir=config_dir) as hass:
            # HA's shared HTTP session needs the network integration.
            await async_setup_component(hass, "network", {})
            MockConfigEntry(
                domain="hue",
                unique_id=BRIDGE_ID,
                data={"host": host, "api_version": 2},
                state=ConfigEntryState.LOADED,
            ).add_to_hass(hass)

            if args.list:
                for area in await _areas(hass, host, key):
                    print(
                        f"{area['id']}  {len(area['channels']):2d} channels  "
                        f"{area['status']:8s}  {area['metadata']['name']}"
                    )
                return

            area_id = os.environ["GLOWCAST_HUE_AREA_ID"]
            output = HueEntertainmentOutput(hass, BRIDGE_ID, key, client_key, area_id)
            try:
                begin = time.perf_counter()
                try:
                    channels = await output.start()
                except Exception as err:  # noqa: BLE001 - report and clean up
                    print(f"start(): {type(err).__name__}: {err}")
                    return
                print(
                    f"start(): {len(channels)} channels in "
                    f"{time.perf_counter() - begin:.2f}s"
                )

                pattern = [
                    ChannelColor(
                        c.id, RED if c.x < -0.33 else BLUE if c.x > 0.33 else GREEN
                    )
                    for c in channels
                ]
                await _stream(output, pattern, args.seconds)
                print(f"pattern: {args.seconds:.0f}s, check() ok")
                if args.kill:
                    print("exiting without stop() or close()", flush=True)
                    os._exit(0)

                dark = [ChannelColor(c.id, VERY_DARK) for c in channels]
                await _stream(output, dark, args.seconds)
                print(f"very dark: {args.seconds:.0f}s, check() ok")
                if args.pause:
                    await asyncio.sleep(args.pause)
                    await output.check()
                    print(f"after {args.pause:.0f}s without sends: check() ok")
            finally:
                await output.stop()
                await output.close()
                states = [
                    a["status"]
                    for a in await _areas(hass, host, key)
                    if a["id"] == area_id
                ]
                print(f"after stop: area {states[0] if states else 'missing'}")

            for entry in hass.config_entries.async_entries():
                entry.mock_state(hass, ConfigEntryState.NOT_LOADED)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--list", action="store_true", help="list the areas")
    parser.add_argument("--seconds", type=float, default=3, help="per colour step")
    parser.add_argument("--pause", type=float, default=0, help="seconds without sends")
    parser.add_argument("--kill", action="store_true", help="exit without stopping")
    asyncio.run(main(parser.parse_args()))
