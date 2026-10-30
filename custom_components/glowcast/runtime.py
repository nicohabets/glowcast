"""The runtime of one Glowcast entry: runs the pipeline and reacts to its status.

Architecture 4.4. Every platform reaches it through `entry.runtime_data`.
"""

import asyncio
import logging
from collections.abc import Callable, Iterable, Mapping
from contextlib import suppress
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.start import async_at_started

from .const import (
    CONF_OUTPUT,
    CONF_SOURCE,
    CONF_TYPE,
    DOMAIN,
    REAUTH_PART,
    REAUTH_SOURCE,
)
from .core.pipeline import (
    MonotonicClock,
    Phase,
    Pipeline,
    PipelineStatus,
    StopReason,
    run_light_test,
)
from .core.ports import (
    LightOutput,
    SourceAuthFailed,
    SourceNotSupported,
    SourceUnavailable,
)
from .hardware_check import async_notify_done, async_run_check
from .issues import Issue
from .outputs import OUTPUTS, OutputType, part_options
from .outputs.blocks import output_blocks, output_types
from .settings import read_settings
from .sources import SOURCES, SourceType

# The package's logger, so log lines read as before the runtime moved here.
_LOGGER = logging.getLogger(__package__)

# Why syncing stopped, for the log (no secrets, no image URLs). An output
# type's repair issues bring their own message for their stop reasons.
_STOP_MESSAGES = {
    StopReason.USER: "switched off",
    StopReason.TV_OFF: "the picture source is off or not reachable",
    StopReason.OUTPUT_LOST: "the light stream was lost or taken over by another app",
    StopReason.OUTPUT_BUSY: "another app is streaming to the lights",
    StopReason.OUTPUT_NOT_FOUND: "the chosen lights no longer exist",
    StopReason.OUTPUT_TOO_MANY_CHANNELS: "the chosen lights have too many channels",
    StopReason.OUTPUT_UNAVAILABLE: "the lights could not be reached",
    StopReason.AUTH_FAILED: "the lights rejected Glowcast's key; pair again",
    StopReason.SOURCE_UNAVAILABLE: "the picture source failed",
    StopReason.SOURCE_AUTH_FAILED: (
        "the picture source rejected Glowcast's key; pair again"
    ),
    StopReason.SOURCE_NOT_SUPPORTED: "the picture source does not allow capturing",
    StopReason.SOURCE_NOT_PERMITTED: (
        "the picture source does not permit Glowcast to capture"
    ),
}
# Stops the user does not need to act on are logged at info level.
_ROUTINE_STOPS = {StopReason.USER, StopReason.TV_OFF}
# After a restart (#183): how long and how often to look for the picture
# source, and how long one look may take.
RESUME_WINDOW = 180.0
RESUME_INTERVAL = 10.0
RESUME_CHECK_TIMEOUT = 15.0


type GlowcastConfigEntry = ConfigEntry[GlowcastData]


class GlowcastData:
    """Runs and stops the pipeline for one entry; the switch shows its state."""

    def __init__(self, hass: HomeAssistant, entry: GlowcastConfigEntry) -> None:
        """Initialize; nothing runs until async_start()."""
        self._hass = hass
        self._entry = entry
        self._pipeline: Pipeline | None = None
        self._status: PipelineStatus | None = None
        self._task: asyncio.Task[None] | None = None
        self._restarting = False
        # Starts and stops one at a time, in the order they came (#269): at
        # most one thing claims the lights or captures. Every stop counts, so
        # a start that waited sees that a later stop wins.
        self._transition = asyncio.Lock()
        self._stops = 0
        self._listeners: list[Callable[[], None]] = []
        self._status_listeners: list[Callable[[], None]] = []
        # What follows the TV as the pipeline started with it (or as stored,
        # before the first start); the update listener gets no old options.
        self._output: Mapping[str, Any] = entry.options[CONF_OUTPUT]
        # The parts of a combined output the running pipeline dropped, with
        # the reason their follow-up (repair issue, reauth) was for.
        self._dropped: dict[str, StopReason] = {}
        # Whether it synced when Home Assistant began to stop (#183).
        self._stopped_by_ha = False
        # Set by async_resume(); every start and stop clears it. The resume's
        # look for the picture source in progress, which they end.
        self._resume_wanted = False
        self._look: asyncio.Task[bool] | None = None
        # The light test while Glowcast does not sync (#182).
        self._test: asyncio.Task[None] | None = None
        # The hardware check (#115): the one running, whether it captures,
        # and the last result (in memory only, for the diagnostics).
        self._check: asyncio.Task[None] | None = None
        self._check_measures = False
        self._check_result: dict[str, Any] | None = None

    @property
    def is_on(self) -> bool:
        """Whether the pipeline is running."""
        return self._pipeline is not None

    @property
    def syncing_at_stop(self) -> bool:
        """Whether it synced when Home Assistant began to stop (#183)."""
        return self._stopped_by_ha or (self._hass.is_stopping and self.is_on)

    @property
    def status(self) -> PipelineStatus | None:
        """The pipeline's last status, kept after a stop; None if never started."""
        return self._status

    @property
    def hardware_check(self) -> dict[str, Any] | None:
        """The result of the last hardware check; None if none ran (#115)."""
        return self._check_result

    @callback
    def async_add_listener(self, update: Callable[[], None]) -> Callable[[], None]:
        """Call `update` when is_on or the options change; returns how to stop."""
        self._listeners.append(update)
        return lambda: self._listeners.remove(update)

    @callback
    def async_add_status_listener(
        self, update: Callable[[], None]
    ) -> Callable[[], None]:
        """Call `update` on every status of the running pipeline; returns how to
        stop. A stop reaches the listeners of async_add_listener instead.

        While streaming that is about once per second, so `update` writes its
        state only when its value changes.
        """
        self._status_listeners.append(update)
        return lambda: self._status_listeners.remove(update)

    async def async_start(self) -> None:
        """Start syncing, unless an integration the TV or lights use is not set up.

        A light test that runs meanwhile ends first, and its lights are
        released before syncing claims them. So do a hardware check that
        captures and the resume's look for the picture source: their captures
        could make the pipeline's fail. Waits for a start or stop in progress;
        a stop that comes while this one waits wins.
        """
        stops = self._stops
        self._resume_wanted = False
        async with self._transition:
            if self._pipeline is not None:
                return
            await self._async_end_look()
            if self._check_measures:
                await self._async_end_check()
            await self._async_end_test()
            if self._stops != stops:
                return  # switched off (or unloaded) meanwhile
            self._start_pipeline()

    def _start_pipeline(self) -> None:
        """Build and run the pipeline, or raise the repair issue of a missing
        integration entry."""
        data, options = self._entry.data, self._entry.options
        source_type = SOURCES[data[CONF_SOURCE][CONF_TYPE]]
        output_type = OUTPUTS[options[CONF_OUTPUT][CONF_TYPE]]
        if self._missing_reference(source_type, starting=True):
            # No pipeline ran; the repair issue says why. An earlier stop
            # reason would be misleading now.
            self._status = None
            self._notify()
            return
        pipeline = Pipeline(
            source_type.create(self._hass, data, options),
            output_type.create(self._hass, data, options),
            read_settings(options),
            self._hass.async_add_executor_job,
            MonotonicClock(),
            lambda status: self._async_status(pipeline, status),
        )
        self._pipeline = pipeline
        self._output = options[CONF_OUTPUT]
        self._dropped = {}
        self._task = self._entry.async_create_background_task(
            self._hass, pipeline.run(), f"glowcast {self._entry.title}"
        )
        self._notify()

    @callback
    def async_resume(self) -> None:
        """It synced when Home Assistant stopped: start again once Home
        Assistant has started and the picture source is on (#183).

        Looks for the source every RESUME_INTERVAL for RESUME_WINDOW; a
        start or stop meanwhile (the switch, an automation, an unload or Home
        Assistant stopping) ends it, also before Home Assistant has started.
        """
        self._resume_wanted = True

        @callback
        def started(hass: HomeAssistant) -> None:
            if self._resume_wanted:
                self._entry.async_create_background_task(
                    hass, self._async_resume(), f"glowcast {self._entry.title} resume"
                )

        self._entry.async_on_unload(async_at_started(self._hass, started))

    async def _async_resume(self) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + RESUME_WINDOW
        while self._resume_wanted:
            # A hardware check that captures goes first: no look beside it.
            on = False if self._check_measures else await self._async_look()
            if not self._resume_wanted:
                return  # started or stopped meanwhile
            if on:
                _LOGGER.info(
                    "Glowcast %s continues after Home Assistant's restart",
                    self._entry.title,
                )
                await self.async_start()
                return
            if loop.time() + RESUME_INTERVAL > deadline:
                self._resume_wanted = False
                _LOGGER.info(
                    "Glowcast %s did not continue after Home Assistant's restart: "
                    "the picture source is off, not reachable or shows no picture",
                    self._entry.title,
                )
                return
            await asyncio.sleep(RESUME_INTERVAL)

    async def _async_look(self) -> bool:
        """Look for the picture source in a task of its own, which a start, a
        stop and a hardware check that captures end (False then)."""
        self._look = look = self._entry.async_create_background_task(
            self._hass, self._async_source_on(), f"glowcast {self._entry.title} look"
        )
        try:
            await asyncio.wait({look})
        finally:
            look.cancel()  # the resume itself ended (unload, Home Assistant stops)
            if self._look is look:
                self._look = None
        return not look.cancelled() and look.result()

    async def _async_end_look(self) -> None:
        """End the resume's look in progress and wait until its connection
        is closed."""
        look, self._look = self._look, None
        if look is not None and not look.done():
            look.cancel()
            await asyncio.wait({look})

    async def _async_source_on(self) -> bool:
        """Whether the picture source can be reached, is on and gives a
        picture (a locked screen answers, but without one), by connecting.

        A source that refuses its picture counts as on (#253)."""
        data, options = self._entry.data, self._entry.options
        source_type = SOURCES[data[CONF_SOURCE][CONF_TYPE]]
        if source_type.is_missing(self._hass, data, options):
            return False
        source = source_type.create(self._hass, data, options)
        try:
            async with asyncio.timeout(RESUME_CHECK_TIMEOUT):
                await source.start()
                if not source.is_on():
                    return False
                await source.capture()
            return True
        except SourceNotSupported:
            # On, but refusing its picture: the normal start ends on the
            # refusal after the startup grace, with the source type's repair
            # issue, instead of looking again for minutes (#253).
            return True
        except (TimeoutError, SourceUnavailable, SourceAuthFailed):
            return False
        except Exception:
            # Not on, as far as the restart goes; the next look tries again.
            _LOGGER.debug(
                "Unexpected error looking for the picture source", exc_info=True
            )
            return False
        finally:
            with suppress(Exception):
                await source.close()

    async def async_stop(self) -> None:
        """Stop syncing (or the light test) and wait until the lights are
        released. Wins over a start that waits (#269) and over a restart."""
        self._stops += 1
        self._resume_wanted = False
        async with self._transition:
            await self._async_end_look()
            await self._async_end_test()
            await self._async_stop()

    async def async_test_lights(self) -> None:
        """Show the light test (#182): known colours by position, about 3 s
        each, then back to what the lights did before.

        While syncing, the running pipeline shows it instead of the picture;
        otherwise the lights are claimed for the test only and released after
        it, as a stop does, without the picture source. The switch does not
        change. A second press during a test does nothing.
        """
        title = self._entry.title
        if self._pipeline is not None:
            if self._pipeline.testing:
                _LOGGER.debug("Glowcast %s is testing the lights already", title)
            elif not await self._pipeline.async_test():
                _LOGGER.info(
                    "Glowcast %s cannot test the lights now: they are not "
                    "following the picture (starting, retrying or no picture)",
                    title,
                )
            return
        if self._test is not None and not self._test.done():
            _LOGGER.debug("Glowcast %s is testing the lights already", title)
            return
        if self._transition.locked():
            _LOGGER.debug(
                "Glowcast %s is starting or stopping; no light test now", title
            )
            return
        data, options = self._entry.data, self._entry.options
        output_type = OUTPUTS.get(options[CONF_OUTPUT][CONF_TYPE])
        if output_type is None:
            # An entry written by a later version, after a downgrade.
            _LOGGER.warning("Glowcast %s cannot test the lights: unknown lights", title)
            return
        if output_type.is_missing(self._hass, data, options):
            issue = output_type.missing
            _LOGGER.warning(
                "Glowcast %s cannot test the lights: %s",
                title,
                "an integration it uses is not set up"
                if issue is None
                else issue.message,
            )
            return
        output = output_type.create(self._hass, data, options)
        _LOGGER.info("Glowcast %s tests the lights", title)
        self._test = self._entry.async_create_background_task(
            self._hass,
            self._async_test(
                output_type, output, read_settings(options).brightness / 100
            ),
            f"glowcast {title} light test",
        )

    async def _async_test(
        self, output_type: OutputType, output: LightOutput, brightness: float
    ) -> None:
        """Run the light test while Glowcast does not sync; log why it could
        not claim the lights (no repair issue or reauth: the next switch-on
        does that)."""
        reason = await run_light_test(
            output, brightness, self._hass.async_add_executor_job, MonotonicClock()
        )
        if reason is not None:
            issue = output_type.stop_issues.get(reason)
            _LOGGER.warning(
                "Glowcast %s could not test the lights: %s",
                self._entry.title,
                _STOP_MESSAGES[reason] if issue is None else issue.message,
            )

    async def async_hardware_check(self) -> None:
        """Run the hardware check (#115) in the background, at most about 30 s.

        Read-only: no stream, no light change, and the switch does not change.
        While syncing the picture source is not captured; the running
        pipeline's measurement counts instead. A press during a check does
        nothing. When it is done, a notification says where the result is.
        """
        title = self._entry.title
        if self._check is not None and not self._check.done():
            _LOGGER.debug("Glowcast %s is running the hardware check already", title)
            return
        # A start (or stop) in progress counts as syncing.
        syncing = self._pipeline is not None or self._transition.locked()
        self._check_measures = not syncing
        _LOGGER.info("Glowcast %s runs the hardware check", title)
        self._check = self._entry.async_create_background_task(
            self._hass, self._async_check(syncing), f"glowcast {title} hardware check"
        )

    async def _async_check(self, syncing: bool) -> None:
        try:
            if not syncing:
                await self._async_end_look()  # no look beside the captures
            result = await async_run_check(
                self._hass, self._entry.data, self._entry.options, syncing=syncing
            )
        finally:
            self._check_measures = False
        status = self._status if syncing and self._pipeline is not None else None
        # While syncing: what the running pipeline measures instead.
        result["pipeline"] = (
            None
            if status is None
            else {
                "phase": status.phase,
                "captures_per_second": status.captures_per_second,
                "black_picture": status.black_picture,
            }
        )
        self._check_result = result
        _LOGGER.info(
            "Glowcast %s finished the hardware check; the result is in its diagnostics",
            self._entry.title,
        )
        await async_notify_done(self._hass, self._entry.entry_id, self._entry.title)

    async def _async_end_check(self) -> None:
        """End a running hardware check that captures, and wait for it."""
        task, self._check = self._check, None
        if task is None or task.done():
            return
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        _LOGGER.info(
            "Glowcast %s ended the hardware check: it starts syncing",
            self._entry.title,
        )
        self._check_result = {"interrupted": "started syncing"}

    async def _async_end_test(self) -> None:
        """End a running light test and wait until its lights are released."""
        task, self._test = self._test, None
        if task is None or task.done():
            return
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    async def _async_stop(self) -> None:
        if self._pipeline is not None:
            self._pipeline.stop()
        task = self._task
        if task is not None:
            with suppress(asyncio.CancelledError):
                await task
            if self._task is task:
                self._task = None

    async def async_options_updated(self) -> None:
        """New options: a running pipeline takes the settings without a restart.

        Only a change of what follows the TV (the output block, compared as a
        whole) restarts it; that change also clears the repair issues about
        the previous choice. A change of the output type reloads the entry,
        so its entities change too. Not running: the next start reads the
        options.
        """
        options = self._entry.options
        if output_types(options) != output_types({CONF_OUTPUT: self._output}):
            # Another output type (or another combination of them) has other
            # entities: reload the entry, which stops syncing and adds the new
            # type's entities; the previous type's entities are removed at
            # setup (__init__.py).
            self._output = options[CONF_OUTPUT]
            self._delete_output_issues(missing=True)
            self._hass.config_entries.async_schedule_reload(self._entry.entry_id)
            return
        self._notify()  # the entities show the new values
        if options[CONF_OUTPUT] != self._output:
            self._output = options[CONF_OUTPUT]
            self._delete_output_issues()
            await self.async_restart()
        elif self._pipeline is not None:
            self._pipeline.update_settings(read_settings(options))

    def _delete_output_issues(self, *, missing: bool = False) -> None:
        """Remove the repair issues about the previous choice of lights.

        Also the one about a missing integration entry when the type changed.
        """
        issues = [
            issue
            for output_type in OUTPUTS.values()
            for issue in (
                *output_type.stop_issues.values(),
                *(notice.issue for notice in output_type.notices),
                *((output_type.missing,) if missing else ()),
            )
        ]
        for key in _keys(issues):
            ir.async_delete_issue(
                self._hass, DOMAIN, issue_id(self._entry.entry_id, key)
            )

    async def async_restart(self) -> None:
        """Restart a running pipeline with the new settings; the switch stays on."""
        if not self.is_on:
            return
        stops = self._stops
        self._restarting = True
        try:
            await self._async_stop()
        finally:
            self._restarting = False
        if self._stops != stops:
            # Switching off (or unloading) during a restart wins over it.
            self._notify()  # the switch shows off now
            return
        await self.async_start()

    @callback
    def async_remove_issues(self) -> None:
        """Remove this entry's repair issues."""
        async_remove_issues(self._hass, self._entry.entry_id)

    def _missing_reference(self, source_type: SourceType, *, starting: bool) -> bool:
        """Raise the type's repair issue if an entry it uses is gone or not loaded.

        True when Glowcast cannot sync: the source's entry is missing, or the
        entry of every output part. A part of a combined output whose entry
        is missing gets its repair issue, and the other parts start without it
        (said in the log only when `starting`; after a stop the stop is
        logged already).
        """
        data, options = self._entry.data, self._entry.options
        if source_type.is_missing(self._hass, data, options):
            self._log_missing(source_type.missing, log="cannot")
            return True
        parts = self._parts()
        missing = [
            part
            for _, part, part_opts in parts
            if part.is_missing(self._hass, data, part_opts)
        ]
        cannot_sync = bool(parts) and len(missing) == len(parts)
        for part in missing:
            log = "cannot" if cannot_sync else "without" if starting else None
            self._log_missing(part.missing, log=log)
        return cannot_sync

    def _log_missing(self, issue: Issue | None, *, log: str | None) -> None:
        """Raise the repair issue of a part whose entry is missing, and log it:
        "cannot" (Glowcast cannot sync), "without" (it syncs without the
        part) or None (not at all)."""
        if issue is None:
            message = "an integration it uses is not set up"
        else:
            message = issue.message
            self._raise_issue(issue.key)
        if log == "cannot":
            _LOGGER.warning("Glowcast %s cannot sync: %s", self._entry.title, message)
        elif log == "without":
            _LOGGER.warning(
                "Glowcast %s syncs without some of its lights: %s",
                self._entry.title,
                message,
            )

    def _parts(self) -> list[tuple[str, OutputType, Mapping[str, Any]]]:
        """Each output type the entry uses, with the options as it sees them."""
        options = self._entry.options
        return [
            (block[CONF_TYPE], OUTPUTS[block[CONF_TYPE]], part_options(options, block))
            for block in output_blocks(options[CONF_OUTPUT])
            if block.get(CONF_TYPE) in OUTPUTS
        ]

    @callback
    def _async_status(self, pipeline: Pipeline, status: PipelineStatus) -> None:
        """Keep the pipeline's status and react to it (architecture 4.4)."""
        if pipeline is not self._pipeline:
            return  # an old pipeline, for example after a restart
        previous, self._status = self._status, status
        # The lights were claimed: earlier problems are solved, except those
        # of parts that did not start. Later streaming reports only update
        # the captures per second.
        if status.phase is Phase.STREAMING and (
            previous is None or previous.phase is not Phase.STREAMING
        ):
            self._remove_issues_except(status.dropped_parts)
            self._entry.async_create_task(
                self._hass,
                self._async_check_notices(pipeline),
                f"glowcast {self._entry.title} notices",
            )
        self._async_parts_dropped(status.dropped_parts)
        if status.phase is Phase.STOPPED:
            # The pipeline always gives a reason; without one, still turn off.
            self._async_stopped(status.stop_reason or StopReason.OUTPUT_UNAVAILABLE)
            return
        for update in list(self._status_listeners):
            update()

    async def _async_check_notices(self, pipeline: Pipeline) -> None:
        """Raise or remove each output part's notices, now the lights are claimed."""
        output = self._entry.options[CONF_OUTPUT]
        for _, output_type, part_opts in self._parts():
            for notice in output_type.notices:
                placeholders = await notice.check(
                    self._hass, self._entry.data, part_opts
                )
                if (
                    pipeline is not self._pipeline
                    or self._entry.options[CONF_OUTPUT] != output
                ):
                    return  # stopped or changed meanwhile; the next claim checks
                if placeholders is None:
                    ir.async_delete_issue(
                        self._hass,
                        DOMAIN,
                        issue_id(self._entry.entry_id, notice.issue.key),
                    )
                else:
                    self._raise_issue(notice.issue.key, placeholders)

    def _remove_issues_except(self, dropped: Mapping[str, StopReason]) -> None:
        """Remove the entry's repair issues, but not those of dropped parts.

        The notices stay: the check after the claim raises or removes them.
        """
        keep = {
            issue.key
            for type_id, reason in dropped.items()
            if (output_type := OUTPUTS.get(type_id)) is not None
            for issue in (output_type.missing, output_type.stop_issues.get(reason))
            if issue is not None
        } | {
            notice.issue.key
            for output_type in OUTPUTS.values()
            for notice in output_type.notices
        }
        for key in issue_keys():
            if key not in keep:
                ir.async_delete_issue(
                    self._hass, DOMAIN, issue_id(self._entry.entry_id, key)
                )

    @callback
    def _async_parts_dropped(self, dropped: Mapping[str, StopReason]) -> None:
        """The follow-up of each part of a combined output that was dropped.

        As for a single output that stops with that reason: reauth for a
        rejected key, the type's repair issue, or the missing-entry issue. Once
        per part and reason; the combined output logs the drop itself.
        """
        self._dropped = {
            type_id: reason
            for type_id, reason in self._dropped.items()
            if type_id in dropped
        }
        for type_id, reason in dropped.items():
            if self._dropped.get(type_id) is reason:
                continue
            self._dropped[type_id] = reason
            if (output_type := OUTPUTS.get(type_id)) is None:
                continue
            if reason is StopReason.AUTH_FAILED:
                self._entry.async_start_reauth(self._hass, data={REAUTH_PART: type_id})
            elif (issue := output_type.stop_issues.get(reason)) is not None:
                self._raise_issue(issue.key)
            elif (
                reason is StopReason.OUTPUT_UNAVAILABLE
                and output_type.missing is not None
                and any(
                    part.is_missing(self._hass, self._entry.data, part_opts)
                    for part_id, part, part_opts in self._parts()
                    if part_id == type_id
                )
            ):
                self._raise_issue(output_type.missing.key)

    @property
    def dropped_parts(self) -> Mapping[str, StopReason]:
        """The parts of a combined output that do not run now, while syncing."""
        if self._pipeline is None or self._status is None:
            return {}
        return self._status.dropped_parts

    @callback
    def _async_stopped(self, reason: StopReason) -> None:
        """React to the pipeline's stop reason (architecture 4.4)."""
        self._pipeline = None
        if self._restarting:
            _LOGGER.debug("Glowcast %s restarting with new settings", self._entry.title)
            return
        log = _LOGGER.info if reason in _ROUTINE_STOPS else _LOGGER.warning
        data, options = self._entry.data, self._entry.options
        source_type = SOURCES[data[CONF_SOURCE][CONF_TYPE]]
        output_type = OUTPUTS[options[CONF_OUTPUT][CONF_TYPE]]
        issue = output_type.stop_issues.get(reason) or source_type.stop_issues.get(
            reason
        )
        message = _STOP_MESSAGES[reason] if issue is None else issue.message
        if reason is StopReason.USER and self._hass.is_stopping:
            # HA cancels background tasks on shutdown; the pipeline then
            # releases the lights and reports USER. It starts again after
            # the restart (#183).
            message = "Home Assistant is stopping"
            self._stopped_by_ha = True
        log("Glowcast %s stopped: %s", self._entry.title, message)
        if reason is StopReason.AUTH_FAILED:
            # A part's own reauth started already when it was dropped.
            if StopReason.AUTH_FAILED not in self._dropped.values():
                self._entry.async_start_reauth(self._hass)
        elif reason is StopReason.SOURCE_AUTH_FAILED:
            self._entry.async_start_reauth(
                self._hass, data={REAUTH_PART: REAUTH_SOURCE}
            )
        elif issue is not None:
            self._raise_issue(issue.key)
        elif reason in (
            StopReason.TV_OFF,
            StopReason.SOURCE_UNAVAILABLE,
            StopReason.OUTPUT_UNAVAILABLE,
        ):
            # The TV or the lights may have been removed from HA while syncing.
            self._missing_reference(source_type, starting=False)
        self._notify()

    def _raise_issue(
        self, key: str, placeholders: Mapping[str, str] | None = None
    ) -> None:
        """Raise the repair issue with this key of the entry's source or outputs."""
        source_type = SOURCES[self._entry.data[CONF_SOURCE][CONF_TYPE]]
        issues = [source_type.missing, *source_type.stop_issues.values()]
        for _, output_type, _ in self._parts():
            issues.extend(
                (
                    output_type.missing,
                    *output_type.stop_issues.values(),
                    *(notice.issue for notice in output_type.notices),
                )
            )
        issue = next(
            issue for issue in issues if issue is not None and issue.key == key
        )
        ir.async_create_issue(
            self._hass,
            DOMAIN,
            issue_id(self._entry.entry_id, issue.key),
            is_fixable=False,
            severity=ir.IssueSeverity.ERROR,
            translation_key=issue.translation_key,
            translation_placeholders={
                **issue.placeholders,
                **(placeholders or {}),
                "name": self._entry.title,
            },
        )

    def _notify(self) -> None:
        for update in list(self._listeners):
            update()


def issue_id(entry_id: str, key: str) -> str:
    """The id of an entry's repair issue."""
    return f"{key}_{entry_id}"


def issue_keys() -> list[str]:
    """The keys of the repair issues every source and output type can raise."""
    return _keys(
        [
            *(source_type.missing for source_type in SOURCES.values()),
            *(
                issue
                for source_type in SOURCES.values()
                for issue in source_type.stop_issues.values()
            ),
            *(output_type.missing for output_type in OUTPUTS.values()),
            *(
                issue
                for output_type in OUTPUTS.values()
                for issue in output_type.stop_issues.values()
            ),
            *(
                notice.issue
                for output_type in OUTPUTS.values()
                for notice in output_type.notices
            ),
        ]
    )


@callback
def async_remove_issues(hass: HomeAssistant, entry_id: str) -> None:
    """Remove an entry's repair issues, of every type."""
    for key in issue_keys():
        ir.async_delete_issue(hass, DOMAIN, issue_id(entry_id, key))


def _keys(issues: Iterable[Issue | None]) -> list[str]:
    """The issues' keys, each once, in order."""
    return list(dict.fromkeys(issue.key for issue in issues if issue is not None))
