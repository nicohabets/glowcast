"""Try WebOsFrameSource against a real LG webOS TV, outside Home Assistant.

Runs the real source inside a throwaway Home Assistant test instance and
prints timings, frame sizes and average colours only (no image data, no
credentials). Needs the test requirements from the CI workflow
(pytest-homeassistant-custom-component and aiowebostv).

Usage, with the TV's host and the client key from HA's webostv entry:

    GLOWCAST_TV_HOST=... GLOWCAST_TV_CLIENT_KEY=... python scripts/try_webos_source.py
"""

import argparse
import asyncio
import os
import statistics
import sys
import tempfile
import time
from pathlib import Path

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_test_home_assistant,
)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from custom_components.glowcast.core.models import Frame  # noqa: E402
from custom_components.glowcast.core.ports import SourceUnavailable  # noqa: E402
from custom_components.glowcast.sources.webos import WebOsFrameSource  # noqa: E402

UNREACHABLE_HOST = "192.0.2.1"  # documentation range, never answers


def _add_entry(hass: HomeAssistant, unique_id: str, host: str, key: str) -> None:
    MockConfigEntry(
        domain="webostv",
        unique_id=unique_id,
        data={"host": host, "client_secret": key},
        state=ConfigEntryState.LOADED,
    ).add_to_hass(hass)


def _thirds(frame: Frame) -> str:
    """Average colour of the left, middle and right third of the frame."""
    width = frame.width
    sums = [[0, 0, 0] for _ in range(3)]
    counts = [0, 0, 0]
    for i in range(frame.width * frame.height):
        part = min(3 * (i % width) // width, 2)
        for c in range(3):
            sums[part][c] += frame.pixels[3 * i + c]
        counts[part] += 1
    return " ".join(
        "rgb(" + ",".join(str(s // n) for s in rgb) + ")"
        for rgb, n in zip(sums, counts, strict=True)
    )


async def _timed_start(source: WebOsFrameSource) -> float:
    begin = time.perf_counter()
    await source.start()
    return time.perf_counter() - begin


async def main(args: argparse.Namespace) -> None:
    host = os.environ["GLOWCAST_TV_HOST"]
    key = os.environ["GLOWCAST_TV_CLIENT_KEY"]

    with tempfile.TemporaryDirectory() as config_dir:
        async with async_test_home_assistant(config_dir=config_dir) as hass:
            # HA's shared HTTP session needs the network integration.
            await async_setup_component(hass, "network", {})
            _add_entry(hass, "tv", host, key)
            _add_entry(hass, "unreachable", UNREACHABLE_HOST, "none")
            source = WebOsFrameSource(hass, "tv")

            starts = []
            for _ in range(args.starts):
                starts.append(await _timed_start(source))
                await source.close()
            print(
                f"start(): n={len(starts)} min={min(starts):.2f}s "
                f"median={statistics.median(starts):.2f}s max={max(starts):.2f}s"
            )

            begin = time.perf_counter()
            await source.start()
            await source.capture()
            print(f"start() + first frame: {time.perf_counter() - begin:.2f}s")
            # Diagnostics only: which app is on screen.
            app = source._client.tv_state.current_app_id
            print(f"is_on: {source.is_on()}, app: {app}")

            frame = None
            begin = time.perf_counter()
            for _ in range(args.captures):
                frame = await source.capture()
            elapsed = time.perf_counter() - begin
            print(
                f"captures: {args.captures} in {elapsed:.2f}s = "
                f"{args.captures / elapsed:.2f} per second"
            )
            print(f"frame: {frame.width}x{frame.height}, thirds: {_thirds(frame)}")
            if args.save:
                from PIL import Image

                Image.frombytes("RGB", (frame.width, frame.height), frame.pixels).save(
                    args.save
                )

            # Force a websocket drop and watch the source reconnect.
            old = source._client.connection
            begin = time.perf_counter()
            await old.close()
            on_during = []
            while source._client.connection in (None, old):
                on_during.append(source.is_on())
                await asyncio.sleep(0.05)
            await source.capture()
            print(
                f"drop: reconnected and captured after "
                f"{time.perf_counter() - begin:.2f}s, is_on during reconnect: "
                f"{all(on_during)} ({len(on_during)} samples)"
            )
            await source.close()

            unreachable = WebOsFrameSource(hass, "unreachable")
            begin = time.perf_counter()
            try:
                await unreachable.start()
            except SourceUnavailable as err:
                print(
                    f"unreachable TV: SourceUnavailable after "
                    f"{time.perf_counter() - begin:.2f}s ({err})"
                )
            await unreachable.close()

            for entry in hass.config_entries.async_entries():
                entry.mock_state(hass, ConfigEntryState.NOT_LOADED)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--starts", type=int, default=5)
    parser.add_argument("--captures", type=int, default=30)
    parser.add_argument("--save", help="save the last frame as a PNG")
    asyncio.run(main(parser.parse_args()))
