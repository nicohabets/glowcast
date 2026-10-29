"""The runtime of one Glowcast entry: runs the pipeline and reacts to its status.

Architecture 4.4. Every platform reaches it through `entry.runtime_data`.
"""

import asyncio
import logging
from collections.abc import Callable
from contextlib import suppress
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir

from .const import CONF_OUTPUT, CONF_SOURCE, CONF_TYPE, DOMAIN
from .core.pipeline import MonotonicClock, Phase, Pipeline, PipelineStatus, StopReason
from .outputs import OUTPUTS
from .settings import read_settings
from .sources import SOURCES

# The package's logger, so log lines read as before the runtime moved here.
_LOGGER = logging.getLogger(__package__)

# Repair issues (translation keys under "issues" in translations/*.json).
ISSUE_SOURCE_MISSING = "source_missing"
ISSUE_OUTPUT_MISSING = "output_missing"
ISSUE_TOO_MANY_CHANNELS = "too_many_channels"
ISSUE_AREA_NOT_FOUND = "area_not_found"
ISSUES = (
    ISSUE_SOURCE_MISSING,
    ISSUE_OUTPUT_MISSING,
    ISSUE_TOO_MANY_CHANNELS,
    ISSUE_AREA_NOT_FOUND,
)

# Why syncing stopped, for the log (no secrets, no image URLs).
_STOP_MESSAGES = {
    StopReason.USER: "switched off",
    StopReason.TV_OFF: "the TV is off or not reachable",
    StopReason.OUTPUT_LOST: "the light stream was lost or taken over by another app",
    StopReason.OUTPUT_BUSY: "another app is streaming to the lights",
    StopReason.OUTPUT_NOT_FOUND: "the chosen entertainment area no longer exists",
    StopReason.OUTPUT_TOO_MANY_CHANNELS: (
        "the chosen entertainment area has more than 20 channels"
    ),
    StopReason.OUTPUT_UNAVAILABLE: "the lights could not be reached",
    StopReason.AUTH_FAILED: "the lights rejected Glowcast's key; pair again",
    StopReason.SOURCE_UNAVAILABLE: "the TV source failed",
}
# Stops the user does not need to act on are logged at info level.
_ROUTINE_STOPS = {StopReason.USER, StopReason.TV_OFF}


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
        self._stopped_during_restart = False
        self._listeners: list[Callable[[], None]] = []

    @property
    def is_on(self) -> bool:
        """Whether the pipeline is running."""
        return self._pipeline is not None

    @property
    def status(self) -> PipelineStatus | None:
        """The pipeline's last status, kept after a stop; None if never started."""
        return self._status

    @callback
    def async_add_listener(self, update: Callable[[], None]) -> Callable[[], None]:
        """Call `update` whenever is_on changes; returns a function to stop."""
        self._listeners.append(update)
        return lambda: self._listeners.remove(update)

    async def async_start(self) -> None:
        """Start syncing, unless the TV or the bridge is not set up in HA."""
        if self._pipeline is not None:
            return
        data, options = self._entry.data, self._entry.options
        source_type = SOURCES[data[CONF_SOURCE][CONF_TYPE]]
        output_type = OUTPUTS[options[CONF_OUTPUT][CONF_TYPE]]
        if self._missing_reference(source_type.is_missing, output_type.is_missing):
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
        self._task = self._entry.async_create_background_task(
            self._hass, pipeline.run(), f"glowcast {self._entry.title}"
        )
        self._notify()

    async def async_stop(self) -> None:
        """Stop syncing and wait until the lights are released."""
        # Switching off (or unloading) during a restart wins over the restart.
        self._stopped_during_restart = self._restarting
        await self._async_stop()

    async def _async_stop(self) -> None:
        if self._pipeline is not None:
            self._pipeline.stop()
        task = self._task
        if task is not None:
            with suppress(asyncio.CancelledError):
                await task
            if self._task is task:
                self._task = None

    async def async_restart(self) -> None:
        """Restart a running pipeline with the new settings; the switch stays on."""
        if not self.is_on:
            return
        self._restarting = True
        self._stopped_during_restart = False
        try:
            await self._async_stop()
        finally:
            self._restarting = False
        if self._stopped_during_restart:
            self._notify()  # the switch shows off now
            return
        await self.async_start()

    @callback
    def async_remove_issues(self) -> None:
        """Remove this entry's repair issues."""
        for key in ISSUES:
            ir.async_delete_issue(
                self._hass, DOMAIN, issue_id(self._entry.entry_id, key)
            )

    def _missing_reference(
        self,
        source_missing: Callable[[HomeAssistant, Any], bool],
        output_missing: Callable[[HomeAssistant, Any], bool],
    ) -> bool:
        """Raise a repair issue if the TV or bridge entry is gone or not loaded."""
        data = self._entry.data
        for missing, key, what in (
            (source_missing, ISSUE_SOURCE_MISSING, "TV"),
            (output_missing, ISSUE_OUTPUT_MISSING, "bridge"),
        ):
            if missing(self._hass, data):
                _LOGGER.warning(
                    "Glowcast %s cannot sync: the %s is not set up (or not loaded) "
                    "in Home Assistant",
                    self._entry.title,
                    what,
                )
                self._raise_issue(key)
                return True
        return False

    @callback
    def _async_status(self, pipeline: Pipeline, status: PipelineStatus) -> None:
        """Keep the pipeline's status and react to it (architecture 4.4)."""
        if pipeline is not self._pipeline:
            return  # an old pipeline, for example after a restart
        previous, self._status = self._status, status
        if status.phase is Phase.STREAMING:
            # The lights were claimed: earlier problems are solved. Later
            # streaming reports only update the captures per second.
            if previous is None or previous.phase is not Phase.STREAMING:
                self.async_remove_issues()
        elif status.phase is Phase.STOPPED:
            # The pipeline always gives a reason; without one, still turn off.
            self._async_stopped(status.stop_reason or StopReason.OUTPUT_UNAVAILABLE)

    @callback
    def _async_stopped(self, reason: StopReason) -> None:
        """React to the pipeline's stop reason (architecture 4.4)."""
        self._pipeline = None
        if self._restarting:
            _LOGGER.debug("Glowcast %s restarting with new settings", self._entry.title)
            return
        log = _LOGGER.info if reason in _ROUTINE_STOPS else _LOGGER.warning
        message = _STOP_MESSAGES[reason]
        if reason is StopReason.USER and self._hass.is_stopping:
            # HA cancels background tasks on shutdown; the pipeline then
            # releases the lights and reports USER.
            message = "Home Assistant is stopping"
        log("Glowcast %s stopped: %s", self._entry.title, message)
        if reason is StopReason.AUTH_FAILED:
            self._entry.async_start_reauth(self._hass)
        elif reason is StopReason.OUTPUT_TOO_MANY_CHANNELS:
            self._raise_issue(ISSUE_TOO_MANY_CHANNELS)
        elif reason is StopReason.OUTPUT_NOT_FOUND:
            self._raise_issue(ISSUE_AREA_NOT_FOUND)
        elif reason in (
            StopReason.TV_OFF,
            StopReason.SOURCE_UNAVAILABLE,
            StopReason.OUTPUT_UNAVAILABLE,
        ):
            # The TV or bridge may have been removed from HA while syncing.
            data, options = self._entry.data, self._entry.options
            self._missing_reference(
                SOURCES[data[CONF_SOURCE][CONF_TYPE]].is_missing,
                OUTPUTS[options[CONF_OUTPUT][CONF_TYPE]].is_missing,
            )
        self._notify()

    def _raise_issue(self, key: str) -> None:
        ir.async_create_issue(
            self._hass,
            DOMAIN,
            issue_id(self._entry.entry_id, key),
            is_fixable=False,
            severity=ir.IssueSeverity.ERROR,
            translation_key=key,
            translation_placeholders={"name": self._entry.title},
        )

    def _notify(self) -> None:
        for update in list(self._listeners):
            update()


def issue_id(entry_id: str, key: str) -> str:
    """The id of an entry's repair issue."""
    return f"{key}_{entry_id}"
