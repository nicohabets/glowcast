"""Frame sources: adapters behind the FrameSource port."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from homeassistant.core import HomeAssistant

from ..const import SOURCE_WEBOS
from ..core.ports import FrameSource
from . import webos


@dataclass(frozen=True)
class SourceType:
    """How Glowcast builds and checks one type of source (architecture 5)."""

    # Build the source from the entry's data and options.
    create: Callable[[HomeAssistant, Mapping[str, Any], Mapping[str, Any]], FrameSource]
    # Whether the integration entry the source uses is removed or not loaded.
    is_missing: Callable[[HomeAssistant, Mapping[str, Any]], bool]
    # The TV's device in Home Assistant, to link Glowcast's device to: its
    # identifier (older HA) and its device registry id (newer HA).
    device_identifier: Callable[[Mapping[str, Any]], tuple[str, str]]
    device_id: Callable[[HomeAssistant, Mapping[str, Any]], str | None]


SOURCES = {
    SOURCE_WEBOS: SourceType(
        webos.create, webos.is_missing, webos.device_identifier, webos.device_id
    ),
}
