"""The hardware check (#115): what the picture source and the lights can do,
for a report.

Architecture 4.3 and 4.4. Generic: each source and output type brings its own
probe (`probe` in SOURCES and OUTPUTS); this runs them side by side and puts
what they found under the types' keys. The runtime keeps the last result in
memory, and the diagnostics download shows it.
"""

import asyncio
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from homeassistant.components import persistent_notification
from homeassistant.core import HomeAssistant
from homeassistant.helpers.translation import async_get_translations

from .const import CONF_OUTPUT, CONF_SOURCE, CONF_TYPE, DOMAIN, HARDWARE_REPORT_URL
from .outputs import OUTPUTS
from .probes import async_run_probe
from .sources import SOURCES

# The whole check; each probe has a shorter limit of its own (probes.py).
CHECK_TIMEOUT = 30.0
# Shown when a check is done. Its text lives under "issues" because Home
# Assistant has no translation category for notifications; it is never
# raised as a repair issue.
NOTICE_TRANSLATION_KEY = "hardware_check_done"


async def async_run_check(
    hass: HomeAssistant,
    data: Mapping[str, Any],
    options: Mapping[str, Any],
    *,
    syncing: bool,
) -> dict[str, Any]:
    """Probe the source and the output.

    While syncing the source does not capture (its captures could make the
    pipeline's fail); the runtime adds the pipeline's own measurement. The
    lights are only read, never claimed or changed.
    """
    begin = time.monotonic()
    source_type = data[CONF_SOURCE][CONF_TYPE]
    output_type = options[CONF_OUTPUT][CONF_TYPE]
    try:
        async with asyncio.timeout(CHECK_TIMEOUT):
            source, output = await asyncio.gather(
                async_run_probe(
                    SOURCES.get(source_type), hass, data, options, measure=not syncing
                ),
                async_run_probe(
                    OUTPUTS.get(output_type), hass, data, options, measure=not syncing
                ),
            )
    except TimeoutError:
        source = output = {"probe": None, "error": "timeout"}
    return {
        "finished": datetime.now(UTC).isoformat(timespec="seconds"),
        "seconds": round(time.monotonic() - begin, 1),
        "syncing": syncing,
        "source": {"type": source_type, **source},
        "output": {"type": output_type, **output},
    }


async def async_notify_done(hass: HomeAssistant, entry_id: str, name: str) -> None:
    """Tell the user the check is done and where the result is, in Home
    Assistant's server language; one notification per entry, replaced by the
    next check."""
    strings = await async_get_translations(
        hass, hass.config.language, "issues", {DOMAIN}
    )
    prefix = f"component.{DOMAIN}.issues.{NOTICE_TRANSLATION_KEY}"
    persistent_notification.async_create(
        hass,
        strings[f"{prefix}.description"].replace("{report_url}", HARDWARE_REPORT_URL),
        title=strings[f"{prefix}.title"].replace("{name}", name),
        notification_id=notification_id(entry_id),
    )


def notification_id(entry_id: str) -> str:
    """The id of the entry's notification that the check is done."""
    return f"{DOMAIN}_{NOTICE_TRANSLATION_KEY}_{entry_id}"
