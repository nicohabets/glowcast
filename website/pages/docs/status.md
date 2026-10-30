# Status and diagnostics

## The status

The **Status** sensor on Glowcast's device (for example `sensor.glowcast_living_room_tv_status`) shows what it is doing:

| Status | Meaning |
|---|---|
| Idle | Not syncing: never started since Home Assistant started, or switched off. Also when the TV or the bridge is not set up in Home Assistant, so syncing cannot start; a repair says which. |
| Starting | Waiting for the TV's picture (up to 10 seconds) and claiming the lights, also during the retry after a lost light stream. |
| Streaming | The lights follow the picture. |
| Black picture | Syncing, but the picture is black (a dark scene): the lights keep the last colours for a moment, then show the faint glow. |
| No picture | The TV gives no picture (for example a streaming app with DRM, see [Known limits](limits.md)): the lights are released; Glowcast stays on and follows again when the picture returns. Before this, the status shows Black picture for about 7 seconds. |
| Picture source off | Stopped because the picture source turned off or could not be reached. For a TV: the TV turned off or does not answer. For a computer screen: the screen helper stopped or cannot be reached, or the screen is locked, asleep or gone, for 3 seconds in a row. |
| Error | Stopped for another reason; Home Assistant shows a repair or asks to pair again where you can fix it, and the log says why. |

## Why it stopped

For Picture source off and Error, the attribute **Stop reason** (`stop_reason`) says why. Home Assistant shows it as a text (for example "Picture source off or not reachable"); in automations and templates it is a fixed key that stays the same in every language, also when a text changes: `tv_off` (the picture source is off or not reachable; the key kept its name when computer screens came), `output_lost` (the light stream was lost), `output_busy` (another app is streaming), `output_not_found` (the entertainment area no longer exists, or the Home Assistant lights have no colour lights left to follow), `output_too_many_channels`, `output_unavailable` (the bridge could not be reached, or none of the Home Assistant lights is available), `auth_failed` (the bridge rejected Glowcast's key), `source_unavailable` (the picture source failed) or `source_auth_failed` (the computer's screen helper rejected Glowcast's key; Home Assistant asks for a new pairing code). Use it in automations, for example to get a notification when Glowcast stops with an error.

With an [entertainment area and Home Assistant lights together](home-assistant-lights.md#together-with-an-entertainment-area), the attribute **Dropped lights** (`dropped_outputs`) lists, while syncing, the part that does not run, with the same keys as the stop reason: for example `{"hue_entertainment": "output_busy"}` when another app streams to the area while the Home Assistant lights follow the picture, or `{"ha_lights": "output_lost"}` while the Home Assistant lights wait for their retry. It is gone once the part runs again, and after switching off. [Troubleshooting](troubleshooting.md) says what to do for each.

With [Home Assistant lights](home-assistant-lights.md), `output_not_found` comes with the repair "Glowcast *name*: no colour lights to follow": the area or light group it follows was removed, or has no colour lights left. Choose other lights in Glowcast's settings; the repair goes away after the next start that works, or when you choose other lights.

## The capture rate

The **Capture rate** sensor shows how many pictures per second Glowcast gets from the TV while it streams (about 3-4 on a 2023 LG G3 at Maximum). It is a diagnostic sensor and off by default: on the device page, under **Diagnostic**, open **+1 disabled entity**, then **Capture rate**, its settings (the gear), and turn on **Enabled**. Home Assistant adds it after about 30 seconds. It is unknown when Glowcast is not streaming or has not measured yet. In automations and templates the unit is always `captures/s`; the screens show it in your language.

## Diagnostics

For a problem report, download Glowcast's diagnostics: **Settings → Devices & services → Glowcast → ⋮ → Download diagnostics**. The download shows the versions, your settings, the TV's and bridge's model and software version, and what Glowcast was doing last (also after it stopped, with the reason). Keys, addresses, ids, serial numbers and names are left out, but have a look before you post it: issues on GitHub are public. See [Reporting a problem](troubleshooting.md#reporting-a-problem).
