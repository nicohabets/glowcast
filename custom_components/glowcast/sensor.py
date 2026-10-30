"""What Glowcast is doing, and how fast it captures (architecture 4.3)."""

from enum import StrEnum
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .core.pipeline import Phase, PipelineStatus, StopReason
from .entity import GlowcastEntity
from .runtime import GlowcastConfigEntry

ATTR_STOP_REASON = "stop_reason"
# The parts of a combined output that do not run while syncing (#11): their
# output type and why, as a stop reason.
ATTR_DROPPED_OUTPUTS = "dropped_outputs"


class Status(StrEnum):
    """The status sensor's values."""

    IDLE = "idle"
    STARTING = "starting"
    STREAMING = "streaming"
    BLACK_PICTURE = "black_picture"
    NO_PICTURE = "no_picture"
    TV_OFF = "tv_off"
    ERROR = "error"


def status_of(status: PipelineStatus | None, syncing: bool) -> Status:
    """The sensor's value for the pipeline's last status and the switch."""
    if status is None or status.phase is Phase.STOPPED:
        if syncing:
            # Switched on (or restarted with a new output), before the new
            # pipeline's first report.
            return Status.STARTING
        if status is None or status.stop_reason is StopReason.USER:
            return Status.IDLE
        if status.stop_reason is StopReason.TV_OFF:
            return Status.TV_OFF
        return Status.ERROR
    if status.phase is Phase.NO_PICTURE:
        return Status.NO_PICTURE
    if status.phase is Phase.STREAMING:
        return Status.BLACK_PICTURE if status.black_picture else Status.STREAMING
    return Status.STARTING  # starting, or retrying after the stream was lost


async def async_setup_entry(
    hass: HomeAssistant,
    entry: GlowcastConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the status and the captures per second."""
    async_add_entities(
        [GlowcastStatus(hass, entry), GlowcastCapturesPerSecond(hass, entry)]
    )


class GlowcastStatusEntity(GlowcastEntity, SensorEntity):
    """Follows every status of the pipeline; writes only when it changes."""

    def __init__(
        self, hass: HomeAssistant, entry: GlowcastConfigEntry, key: str
    ) -> None:
        """Initialize for one Glowcast entry."""
        super().__init__(hass, entry)
        self._attr_translation_key = key
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._written: tuple[Any, dict[str, Any] | None] | None = None

    @property
    def _shown(self) -> tuple[Any, dict[str, Any] | None]:
        return self.native_value, self.extra_state_attributes

    async def async_added_to_hass(self) -> None:
        """Follow the pipeline's status too, not only starts and stops."""
        await super().async_added_to_hass()
        self.async_on_remove(
            self._data.async_add_status_listener(self._async_runtime_changed)
        )
        self._written = self._shown  # Home Assistant writes it once added

    @callback
    def _async_runtime_changed(self) -> None:
        """Write the state only if the value or the attributes changed."""
        if (shown := self._shown) != self._written:
            self._written = shown
            self.async_write_ha_state()


class GlowcastStatus(GlowcastStatusEntity):
    """Idle, starting, streaming, black picture, no picture, TV off or error."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = [status.value for status in Status]

    def __init__(self, hass: HomeAssistant, entry: GlowcastConfigEntry) -> None:
        """Initialize for one Glowcast entry."""
        super().__init__(hass, entry, "status")

    @property
    def native_value(self) -> str:
        """The value for the pipeline's last status."""
        return status_of(self._data.status, self._data.is_on).value

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Why it stopped, for TV off and error; the repair issues explain it.

        While syncing, the parts of a combined output that were dropped.
        """
        status = self._data.status
        if self.native_value in (Status.TV_OFF, Status.ERROR) and status is not None:
            reason = status.stop_reason or StopReason.OUTPUT_UNAVAILABLE
            return {ATTR_STOP_REASON: reason.value}
        if dropped := self._data.dropped_parts:
            return {
                ATTR_DROPPED_OUTPUTS: {
                    type_id: reason.value for type_id, reason in dropped.items()
                }
            }
        return None


class GlowcastCapturesPerSecond(GlowcastStatusEntity):
    """The measured captures per second while streaming; diagnostic."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False
    _attr_suggested_display_precision = 1

    def __init__(self, hass: HomeAssistant, entry: GlowcastConfigEntry) -> None:
        """Initialize for one Glowcast entry."""
        super().__init__(hass, entry, "captures_per_second")

    @property
    def native_value(self) -> float | None:
        """Unknown when not streaming or not measured yet (also after a retry)."""
        status = self._data.status
        if status is None or status.phase is not Phase.STREAMING:
            return None
        if status.captures_per_second is None:
            return None
        return round(status.captures_per_second, 1)
