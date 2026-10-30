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
| TV off | Stopped because the TV turned off or could not be reached. For a computer screen, TV off means the screen helper stopped or cannot be reached, or the screen is locked, asleep or gone, for 3 seconds in a row. |
| Error | Stopped for another reason; Home Assistant shows a repair or asks to pair again where you can fix it, and the log says why. |

## Why it stopped

For TV off and Error, the attribute **Stop reason** (`stop_reason`) says why, as a fixed key: `tv_off`, `output_lost` (the light stream was lost), `output_busy` (another app is streaming), `output_not_found` (the entertainment area no longer exists), `output_too_many_channels`, `output_unavailable` (the bridge could not be reached), `auth_failed` (the bridge rejected Glowcast's key), `source_unavailable` (the TV source failed) or `source_auth_failed` (the computer's screen helper rejected Glowcast's key; Home Assistant asks for a new pairing code). Use it in automations, for example to get a notification when Glowcast stops with an error. [Troubleshooting](troubleshooting.md) says what to do for each.

## The capture rate

The **Capture rate** sensor shows how many pictures per second Glowcast gets from the TV while it streams (about 3-4 on a 2023 LG G3 at Maximum). It is a diagnostic sensor and off by default: on the device page, under **Diagnostic**, open **+1 disabled entity**, then **Capture rate**, its settings (the gear), and turn on **Enabled**. Home Assistant adds it after about 30 seconds. It is unknown when Glowcast is not streaming or has not measured yet. In automations and templates the unit is always `captures/s`; the screens show it in your language.

## Diagnostics

For a problem report, download Glowcast's diagnostics: **Settings → Devices & services → Glowcast → ⋮ → Download diagnostics**. The download shows the versions, your settings, the TV's and bridge's model and software version, and what Glowcast was doing last (also after it stopped, with the reason). Keys, addresses, ids, serial numbers and names are left out, but have a look before you post it: issues on GitHub are public. See [Reporting a problem](troubleshooting.md#reporting-a-problem).
