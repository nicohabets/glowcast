"""Try the pipeline with the real TV source and Hue output, outside Home Assistant.

Runs WebOsFrameSource and HueEntertainmentOutput through the pipeline inside a
throwaway Home Assistant test instance for a while, then stops. It prints the
start time, capture and send rates and the stop reason (no keys, no image
data). Needs the requirements listed in scripts/README.md.

Environment: GLOWCAST_TV_HOST, GLOWCAST_TV_CLIENT_KEY (the webostv entry's
client_secret), GLOWCAST_HUE_HOST, GLOWCAST_HUE_APP_KEY, GLOWCAST_HUE_CLIENT_KEY
and GLOWCAST_HUE_AREA_ID.

    python scripts/try_pipeline.py --seconds 30
"""

import argparse
import asyncio
import itertools
import os
import sys
import tempfile
import time
from pathlib import Path

from homeassistant.config_entries import ConfigEntryState
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_test_home_assistant,
)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from custom_components.glowcast.core.models import (  # noqa: E402
    Channel,
    ChannelColor,
    Frame,
)
from custom_components.glowcast.core.pipeline import (  # noqa: E402
    MonotonicClock,
    Pipeline,
    PipelineStatus,
    Settings,
    UpdateRate,
)
from custom_components.glowcast.outputs.hue_entertainment import (  # noqa: E402
    HueEntertainmentOutput,
)
from custom_components.glowcast.sources.webos import WebOsFrameSource  # noqa: E402


class CountingSource:
    """Counts captures around the real source."""

    def __init__(self, source: WebOsFrameSource) -> None:
        self._source = source
        self.max_rate_hz = source.max_rate_hz
        self.good = 0
        self.failed = 0

    async def start(self) -> None:
        await self._source.start()

    def is_on(self) -> bool:
        return self._source.is_on()

    async def capture(self) -> Frame:
        try:
            frame = await self._source.capture()
        except Exception:
            self.failed += 1
            raise
        self.good += 1
        return frame

    async def close(self) -> None:
        await self._source.close()


class CountingOutput:
    """Counts sends around the real output."""

    def __init__(self, output: HueEntertainmentOutput) -> None:
        self._output = output
        self.max_rate_hz = output.max_rate_hz
        self.sends = 0
        self.first_send: float | None = None

    async def start(self) -> list[Channel]:
        return await self._output.start()

    def send(self, colors: list[ChannelColor]) -> None:
        if self.first_send is None:
            self.first_send = time.perf_counter()
        self.sends += 1
        self._output.send(colors)

    async def check(self) -> None:
        await self._output.check()

    async def stop(self) -> None:
        await self._output.stop()

    async def close(self) -> None:
        await self._output.close()


async def main(args: argparse.Namespace) -> None:
    env = os.environ
    with tempfile.TemporaryDirectory() as config_dir:
        async with async_test_home_assistant(config_dir=config_dir) as hass:
            # HA's shared HTTP session needs the network integration.
            await async_setup_component(hass, "network", {})
            entries = [
                MockConfigEntry(
                    domain="webostv",
                    unique_id="tv",
                    data={
                        "host": env["GLOWCAST_TV_HOST"],
                        "client_secret": env["GLOWCAST_TV_CLIENT_KEY"],
                    },
                    state=ConfigEntryState.LOADED,
                ),
                MockConfigEntry(
                    domain="hue",
                    unique_id="bridge",
                    data={"host": env["GLOWCAST_HUE_HOST"], "api_version": 2},
                    state=ConfigEntryState.LOADED,
                ),
            ]
            for entry in entries:
                entry.add_to_hass(hass)

            source = CountingSource(WebOsFrameSource(hass, "tv"))
            output = CountingOutput(
                HueEntertainmentOutput(
                    hass,
                    "bridge",
                    env["GLOWCAST_HUE_APP_KEY"],
                    env["GLOWCAST_HUE_CLIENT_KEY"],
                    env["GLOWCAST_HUE_AREA_ID"],
                )
            )
            statuses: list[PipelineStatus] = []
            pipeline = Pipeline(
                source,
                output,
                Settings(args.brightness, args.intensity, UpdateRate(args.rate)),
                hass.async_add_executor_job,
                MonotonicClock(),
                statuses.append,
            )

            begin = time.perf_counter()
            task = asyncio.ensure_future(pipeline.run())
            done, _ = await asyncio.wait({task}, timeout=args.seconds)
            if not done:
                pipeline.stop()
                await task
            end = time.perf_counter()

            # Streaming is reported again every second with a new rate.
            phases = [
                phase for phase, _ in itertools.groupby(s.phase for s in statuses)
            ]
            print(f"phases: {', '.join(phases)}")
            last = statuses[-1] if statuses else None
            print(f"stop reason: {last.stop_reason if last else 'none'}")
            if last is not None and last.captures_per_second is not None:
                print(f"last measured: {last.captures_per_second} captures per second")
            if output.first_send is not None:
                streamed = end - output.first_send
                print(f"start to first send: {output.first_send - begin:.2f}s")
                print(
                    f"streamed {streamed:.1f}s: "
                    f"{output.sends / streamed:.1f} sends per second, "
                    f"{source.good / streamed:.2f} captures per second "
                    f"({source.failed} failed)"
                )
            else:
                print(f"no sends; ran {end - begin:.1f}s")

            for entry in entries:
                entry.mock_state(hass, ConfigEntryState.NOT_LOADED)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--seconds", type=float, default=30)
    parser.add_argument("--brightness", type=int, default=80)
    parser.add_argument("--intensity", type=int, default=100)
    parser.add_argument(
        "--rate", choices=[r.value for r in UpdateRate], default=UpdateRate.HIGH.value
    )
    asyncio.run(main(parser.parse_args()))
