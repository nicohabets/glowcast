# Glowcast next to a Hue Sync Box

Research for spike #202, done on 2026-10-30. Desk research and code reading only; no Sync Box, bridge, TV or Home Assistant was used. No new hardware facts were found, so nothing was added to "Verified facts" in `docs/ARCHITECTURE.md`; the hardware facts cited below come from that table.

## The short answer

- **The Sync Box does not share its colours.** Neither the classic Sync Box (4K) nor the Sync Box 8K exposes the colours or zone colours it computes: not in its local API, not in Home Assistant, and not in any other documented way we found. It only exposes its **state** (syncing or not, mode, input, brightness, intensity, which entertainment area).
- **Glowcast can run next to it with loose lights only.** A loose-lights-only Glowcast never talks to the bridge's entertainment API, so it cannot stop the box's stream (D10). It needs its own picture source (the LG TV or a computer screen); it cannot reuse the box's picture.
- **"Follow the Sync Box" in Home Assistant** is a start and stop on the box's state: the `huesyncbox` integration's "Light Sync" switch, or Home Assistant's own Hue "entertainment area" binary sensor.

## Evidence levels

As in #12:

- **verified: hardware** - measured by Glowcast on real hardware ("Verified facts" in `docs/ARCHITECTURE.md`).
- **verified: source** - code we read, linked to a tag or commit.
- **verified: docs** - official documentation.
- **reported** - forum post, issue or blog, with its date.
- **inference** - our own conclusion, not tested and not stated by a source.

## Question 1 - does the Sync Box expose its colours?

**No.**

### The official API

- Philips documents the Sync Box API on its developer portal, behind a free developer login. We could not read it: the page redirects to a login form, and we did not log in. **Unverified** ([developer portal page](https://developers.meethue.com/develop/hue-entertainment/hue-hdmi-sync-box-api/)).
- So the API was read from the library that Home Assistant's integration uses, `aiohuesyncbox` 0.1.6, which models every resource the box returns.

### What the API contains

From `aiohuesyncbox` 0.1.6 ([models](https://github.com/mvdwetering/aiohuesyncbox/tree/v0.1.6/aiohuesyncbox/models), [controllers](https://github.com/mvdwetering/aiohuesyncbox/tree/v0.1.6/aiohuesyncbox/controllers)) - **verified: source**:

- **Transport:** HTTPS to the box (`/api/v1`), with a bearer token from a registration that the user confirms on the box. One `GET /api/v1` returns all resources.
- **`device`:** name, type, firmware and API level, Wi-Fi, LED mode, update settings, temperature and power warnings.
- **`execution`:** whether sync is active, the mode (`powersave`, `passthrough`, `video`, `game`, `music`), the last sync mode, the target entertainment area, brightness, intensity per mode (and the music palette), the HDMI input, the preset.
- **`hue`:** the connection to the bridge (`connectionState`, for example `connected`, `streaming`, `busy`), the bridge's id and address, the chosen area, and the list of areas with name, number of lights, whether active, and owner. Newer boxes add a standalone Bluetooth mode.
- **`hdmi`, `behavior`, `ir`, `registrations`, `presets`:** inputs and their status, power and CEC behaviour, infrared codes, apps registered with the box, saved presets.
- **No colour data.** No field holds a colour, an RGB or xy value, a zone or a channel. "Streaming" appears only as a connection state.

### The Sync Box 8K

- **Same API.** The 8K uses the same `/api/v1` and is told apart by its device type and API level. Newer API levels add fields (for example the standalone Bluetooth mode and power warnings), but no colours. **verified: source** (same library).
- Philips' release notes for the Sync Box firmware (from 1.2.2 in 2019 to 3.5.2 for the 2026 4K, 6 October 2026) mention no API change and no access to the colours. **verified: docs** ([Philips Hue release notes](https://www.philips-hue.com/en-gb/support/release-notes/philips-hue-play-hdmi-sync-box)).

### Through the bridge

- The box sends its colours to the bridge over the **encrypted entertainment stream** (DTLS), with a key the box keeps to itself; the API only lets an app **write** that key. A third party cannot read the stream. **verified: source** (the key is write-only in the library) and **inference** (no way to read the stream).
- **The bridge does not report the streamed colours.** After Glowcast's own stream, the bridge and Home Assistant report the lights' state from before the stream, not the streamed colours (architecture section 2, "Verified facts", row #142). **verified: hardware** for Glowcast's stream; **inference** that the box's stream behaves the same, since both use the same entertainment stream. We found no source that shows streamed colours in the bridge's API.

### Community attempts

- We found **no project that reuses the Sync Box's colours.**
  - The openHAB binding for the box exposes the same kind of state as the Home Assistant integration (power, mode, brightness, intensity, input, sync), without colours. **reported** ([openHAB community](https://community.openhab.org/t/philips-hdmi-sync-api/111679/9), 2021-2023).
  - WLED's Hue sync **polls** one Hue light at a configurable interval (1-2 s recommended). **verified: docs** ([WLED docs](https://kno.wled.ge/interfaces/philips-hue/)). So it cannot follow an entertainment stream. **inference**
  - diyHue emulates a Hue bridge, so WLED strips can join Hue Sync. **verified: docs** (same WLED page). That replaces the bridge; it does not read the box. We found no other route. **inference**

## Question 2 - can Glowcast run next to a syncing Sync Box?

**Yes, with loose lights only (#9).** Checked in the code on `main` (commit `3425b14`, 30-10-2026) - **verified: source**:

- **A loose-lights entry builds only the loose-lights output.** The output registry ([`outputs/__init__.py`](https://github.com/nicohabets/glowcast/blob/3425b149323a43c32cd6f8cb8c76134a1c081925/custom_components/glowcast/outputs/__init__.py)) builds the Hue entertainment output only for the `hue_entertainment` type, or for a `combined` entry that has an area part. A loose-lights-only entry is the `ha_lights` type.
- **The loose-lights output only calls Home Assistant's `light.turn_on` and `light.turn_off`** ([`outputs/ha_lights.py`](https://github.com/nicohabets/glowcast/blob/3425b149323a43c32cd6f8cb8c76134a1c081925/custom_components/glowcast/outputs/ha_lights.py)). It never starts or stops an entertainment stream.
- **Its only bridge calls are read-only.** `async_area_lights` and `async_entertainment_hint` ([`outputs/hue_entertainment.py`](https://github.com/nicohabets/glowcast/blob/3425b149323a43c32cd6f8cb8c76134a1c081925/custom_components/glowcast/outputs/hue_entertainment.py)) only `GET` the bridge's lights, entertainment services and areas: one tells which loose lights are already in a combined entry's area, the other shows a hint in the setup flow.
- **So the box's stream is never stopped.** D10 ("never take lights away from something the user started elsewhere") holds for loose lights.

What a user should know (**inference** unless marked):

- **Pick lights that are not in the box's entertainment area.** While the box streams, the bridge ignores normal commands to the lights in that area. **reported** ([HA community: overriding Hue light sync](https://community.home-assistant.io/t/overriding-philips-hue-light-sync-with-home-assistant/892474), 2025-2026; [ioBroker.hue issue #85](https://github.com/iobroker-community-adapters/ioBroker.hue/issues/85), undated). Glowcast would not disturb the box, but such a light would not follow Glowcast either. Glowcast does not warn about this today; it only checks a combined entry's own area.
- **Other Hue lights on the same bridge** (outside the box's area) should take Glowcast's commands normally, at Glowcast's limit of 4 commands per second per bridge (D19). No source says this explicitly. **inference, unverified**
- **Philips advises against the REST API for fast continuous updates**; it is slow next to the entertainment stream. **verified: docs** ([Hue developer portal](https://developers.meethue.com/new-hue-api/)). D19's limit (1 update per second per light, 4 per second per bridge) comes from Glowcast's own measurements in #125 (**verified: hardware**), which agree with this advice.
- **The timing difference** between the box's Hue lights and Glowcast's loose lights: **not measured** (needs a Sync Box). Expect the loose lights to trail: the box is reviewed as having no visible lag (**reported**, via #159), and loose lights change at most once per second each (D19). For comparison, Glowcast on a Hue entertainment area follows a picture change in about half a second (median 537 ms on the test bench, #134, **verified: hardware**).
- **Why no entertainment area for Glowcast here:** an entry with a Hue area (alone or combined) refuses to start while another app streams on the bridge (`OutputBusy`, D10), and a bridge streams one area at a time. **verified: source** ([`outputs/hue_entertainment.py`](https://github.com/nicohabets/glowcast/blob/3425b149323a43c32cd6f8cb8c76134a1c081925/custom_components/glowcast/outputs/hue_entertainment.py)) and architecture 3.4.
- **Two picture sources.** The box reads the HDMI signal; Glowcast reads the TV's own capture (or a computer screen). With the box between the source and the TV, Glowcast captures the TV's HDMI input as usual. **inference**

## Question 3 - "follow the Sync Box" in Home Assistant

### The `huesyncbox` integration

- **A HACS custom integration, not part of Home Assistant core.** Version 3.2.2 (7 Oct 2026) needs Home Assistant 2026.8.0 or newer, polls the box every 3 s, **verified: source** ([v3.2.2](https://github.com/mvdwetering/huesyncbox/tree/v3.2.2), [`hacs.json`](https://github.com/mvdwetering/huesyncbox/blob/v3.2.2/hacs.json), [`const.py`](https://github.com/mvdwetering/huesyncbox/blob/v3.2.2/custom_components/huesyncbox/const.py)). It supports the classic Sync Box, the single-input 4K, the 8K and Screen Sync. **verified: docs** ([README](https://github.com/mvdwetering/huesyncbox/blob/v3.2.2/README.md)).
- **Entities** - **verified: source** ([`switch.py`](https://github.com/mvdwetering/huesyncbox/blob/v3.2.2/custom_components/huesyncbox/switch.py), [`select.py`](https://github.com/mvdwetering/huesyncbox/blob/v3.2.2/custom_components/huesyncbox/select.py), [`sensor.py`](https://github.com/mvdwetering/huesyncbox/blob/v3.2.2/custom_components/huesyncbox/sensor.py), [`number.py`](https://github.com/mvdwetering/huesyncbox/blob/v3.2.2/custom_components/huesyncbox/number.py)):

| Entity | What it shows | States |
|---|---|---|
| switch "Light Sync" (`light_sync`) | **The box is syncing**: on when its mode is `video`, `game` or `music` (not `powersave` or `passthrough`). Turning it on or off starts or stops sync. | on / off |
| switch "Power" (`power`) | The box is not in power save | on / off |
| select "Sync mode" (`sync_mode`) | The current mode, or the last one when not syncing. Choosing a mode probably also starts sync (**inference**: the code only sets the mode). | video, music, game |
| select "Entertainment area" (`entertainment_area`) | Which area the box drives (hidden in standalone mode) | the area names |
| select "HDMI Input", "Intensity", "Led indicator" | Input, intensity, LED | per box |
| number "Brightness" | Brightness | 1-100 |
| sensor "Bridge connection" (diagnostic, **disabled by default**) | The box's link to the bridge | `uninitialized`, `disconnected`, `connecting`, `unauthorized`, `connected`, `invalidgroup`, `streaming`, `busy` |
| sensors "HDMI1-4 status" (diagnostic) | Input status | `unplugged`, `plugged`, `linked`, `unknown` |
| sensors "Bridge ID", "IP address", "Wifi quality", "Content info" (diagnostic, **disabled by default**) | Details | - |
| switch "Dolby Vision compatibility" (configuration, classic 4K) | A box setting | on / off |

- **Actions:** `huesyncbox.set_sync_state` (power, sync, mode, input, brightness, intensity, area) and `huesyncbox.set_bridge`. **verified: source** ([`services.yaml`](https://github.com/mvdwetering/huesyncbox/blob/v3.2.2/custom_components/huesyncbox/services.yaml)).

### The best trigger

- **The "Light Sync" switch** is the best start and stop: it is on in every sync mode (it is derived from the box's mode, not from `syncActive`), and it is enabled by default. A blueprint can add a condition on "Sync mode" (`video` or `game`) to skip music sync. **inference** from the source above.
- **An alternative without the custom integration:** Home Assistant's own Hue integration has a binary sensor per entertainment area ("running" device class), on while the area's status is `active`. It reacts to any streamer (the box, the Hue Sync apps, Glowcast's own area) and is pushed by the bridge, not polled. **verified: source** ([HA core 2025.7.2, `hue/v2/binary_sensor.py`](https://github.com/home-assistant/core/blob/2025.7.2/homeassistant/components/hue/v2/binary_sensor.py), `HueEntertainmentActiveSensor`; the same on `dev` at commit `2e3af9e`, 30-10-2026). It exists in Glowcast's minimum Home Assistant version (2025.7.2), while `huesyncbox` 3.2.2 needs 2026.8.0.

## Recommendation

The expected outcome of the spike: **the colours cannot be reused, but Glowcast can drive the other lights next to the box.** A story under #76 ("More lights than the entertainment area"), with:

- a blueprint that starts and stops Glowcast with the box's sync state;
- setup advice: loose lights only, no entertainment area, and none of the box's own lights;
- a section in the user guide (#20) on the website;
- the timing difference, measured once a Sync Box is available.

The draft story is in the comment on #202; the product owner decides whether to create it.

## Not verified

- The official Sync Box API documentation (behind a developer login).
- Whether the bridge accepts normal commands to lights **outside** the streaming area at full speed during a box stream (no source found; Glowcast's own stream did not show a problem, but that was not measured either).
- Whether the bridge publishes streamed colours anywhere (no source found either way; the reports point to "no").
- The timing difference between the box's lights and loose lights (needs a Sync Box).
