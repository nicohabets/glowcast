"""Frame sources: adapters behind the FrameSource port."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from homeassistant.config_entries import ConfigFlow
from homeassistant.core import HomeAssistant

from ..core.ports import FrameSource
from ..issues import Issue
from . import screen, webos, webos_flow


@dataclass(frozen=True)
class SourceType:
    """How Glowcast builds and checks one type of source (architecture 5)."""

    # Build the source from the entry's data and options.
    create: Callable[[HomeAssistant, Mapping[str, Any], Mapping[str, Any]], FrameSource]
    # Whether the integration entry the source uses is removed or not loaded,
    # from the entry's data and options.
    is_missing: Callable[[HomeAssistant, Mapping[str, Any], Mapping[str, Any]], bool]
    # The TV's device in Home Assistant, to link Glowcast's device to: its
    # identifier (older HA) and its device registry id (newer HA, and
    # diagnostics). None when the source has no device.
    device_identifier: Callable[[Mapping[str, Any]], tuple[str, str] | None]
    device_id: Callable[[HomeAssistant, Mapping[str, Any]], str | None]
    # The type's steps in the setup flow (flow.py): a mixin of async_step_
    # methods, and the step that starts them.
    steps: type[ConfigFlow] | None = None
    setup_step: str | None = None
    # The step that pairs again when the source rejects Glowcast's key.
    reauth_step: str | None = None
    # The repair issue when the integration entry it uses is missing.
    missing: Issue | None = None


SOURCES = {
    webos.SOURCE_WEBOS: SourceType(
        webos.create,
        webos.is_missing,
        webos.device_identifier,
        webos.device_id,
        steps=webos_flow.WebOsSteps,
        setup_step="tv",
        missing=webos.MISSING,
    ),
    # Its setup and reauth steps (discovery and the pairing code) are #109.
    screen.SOURCE_SCREEN: SourceType(
        screen.create,
        screen.is_missing,
        screen.device_identifier,
        screen.device_id,
    ),
}

# Every source type's setup steps, for the one registered flow.
SOURCE_STEPS = tuple(
    source_type.steps for source_type in SOURCES.values() if source_type.steps
)
