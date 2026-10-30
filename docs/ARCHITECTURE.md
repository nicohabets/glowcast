# Glowcast - Architecture

Status: **v0.3.0 released**. This document is the source of truth for the first build. Features after v0.1 are tracked as GitHub issues (milestones v0.2 to v0.5, or no milestone when not planned yet), not here; this document only describes how the design leaves room for them (section 8).

## 1. Goal

Make Hue lights follow the picture on an LG webOS TV, from inside Home Assistant, with no cloud, no subscription and no extra hardware.

### v0.1 scope - the minimum

Glowcast only translates picture into colour into light. Everything Home Assistant or the Hue app already does, stays there.

- **Source:** an LG webOS TV that is already set up in HA through the core `webostv` integration.
- **Output:** one Hue **entertainment area** (for example the living room). Which lights take part, and where they stand, is set up in the Hue app. Gradient lightstrips work per segment.
- **Control:** one switch entity. Starting is done by the user, a dashboard or an **automation** (for example "TV turns on → switch on"). Glowcast never starts by itself.
- **Settings screen:** the integration's options (Settings → Devices & services → Glowcast → Configure): entertainment area, brightness, intensity, update rate. The TV and bridge are chosen once, during setup.

### Not in v0.1

Loose lights, other TVs, extra entities, effects and so on are GitHub issues in milestones v0.2 to v0.5, or without a milestone when not planned yet. Build one only when its issue is in Todo, and never build placeholders for them. Section 8 shows where they will plug in.

## 2. Verified facts (tested 2026-10-28 on an LG OLED65G36LA, 2023)

| Fact | Detail |
|---|---|
| Screen capture works | `ssap://tv/executeOneShot` with `{"path": "/tmp/capture.jpg", "method": "DISPLAY", "format": "JPG"}` returns `{"imageUri": "https://<tv>:3001/resources/<hash>/capture.jpg"}` |
| Frame | 960x540 JPEG, about 80 KB. The `width`/`height` parameters are ignored. |
| Rate | about 4.1 captures per second with `WebOsFrameSource`, sequential (request, download, decode to 64x36); see the `WebOsFrameSource` row below. An earlier test without decoding measured about 3.4. |
| TLS | The image URL uses the TV's self-signed certificate: download without verification, LAN only. |
| Pairing | Reusing `host` + `client_secret` from HA's `webostv` config entry works with `aiowebostv.WebOsClient(host, client_key=...)`. No pairing prompt, and it coexists with HA's own connection. |
| Hue app | The official Hue Sync TV app is not available on 2023 LG models (webOS 24+ only). |
| HDMI input works | A game console on HDMI: 10/10 captures, about 3.5 per second, real picture. |
| Hue Pro bridge | `hue-entertainment==0.1.2` pairs (link button), lists all entertainment areas with channel positions, and starts/stops a stream without errors. A left-red / centre-green / right-blue test pattern showed correctly on the lights in a test area, which returned to their previous state afterwards. |
| Gradient strips | Appear as several channels with their own positions (a TV gradient strip: 4 channels). The test area had 16 channels in total. |
| Position axes | In the test area the TV strip sits at y = 0.8 and a lamp in the far corner at y = -0.95, so y = +1 is the TV side. x and z behave as documented. |
| TV strip positions | The four channels of the TV gradient strip in the test area, as read from the bridge, sit at (x, y, z) = (-0.4, 0.8, -0.4), (-0.4, 0.8, 0.0), (0.4, 0.8, 0.4) and (0.4, 0.8, 0.0): left and right of the screen at different heights, not at the edges (-1/+1). See 3.3. |
| `hue-entertainment` behaviour (source code, v0.1.2) | Colours go out as **8-bit**: values above 255 are shifted down, values of 255 or less are sent as-is, so mixing scales is wrong (see 3.4). `EntertainmentSession` defaults to `stop_others=True` and `idle_timeout=10` s. The DTLS stream is one-way UDP; a dropped stream is not detected by the library, and the handshake does not verify the bridge's Finished message, so a wrong key also looks connected. `start()` can block up to about 23 s against an unreachable bridge. `pair()` raises `TimeoutError` for every failure, including an unreachable bridge. Area listing has no status; `remote_status()` covers only the session's own area. `MAX_LIGHTS_PER_MESSAGE = 20` is not enforced. `HueEntertainmentAPI` opens its own aiohttp session and logs the bridge host at INFO. |
| `hue-entertainment` installs in HA (tested, v0.1.2) | Installs and imports in Home Assistant 2026.10.4 (Home Assistant OS, Python 3.14.9) from a custom integration's `requirements`. No install errors in the HA log, and HA's pins stay as they are (`aiohttp==3.14.5`, `cryptography==50.0.1`, `zeroconf==0.151.5`). Offline, in a Python 3.14.5 virtualenv with HA's `package_constraints.txt`, only `hue-entertainment` was added and `uv pip check` reports all packages compatible. |
| Minimum Home Assistant version | **2025.7.2.** Glowcast needs `aiowebostv` >= 0.7.4: 0.7 brought `client_session=` and `tv_state.is_on`, and 0.7.4 fixed the handshake that current LG firmware needs (see "aiowebostv before 0.7.4" below). HA's `webostv` integration pins 0.4.2 in 2025.1, 0.6.1 in 2025.2, 0.7.3 from 2025.3 to 2025.7.1 and 0.7.4 from 2025.7.2 (checked in HA core's `webostv/manifest.json` per release tag). The minimum was 2025.3.0 until that handshake problem was found (#27). `hue-entertainment` does not raise this: it only needs `aiohttp>=3.9`, `cryptography>=42.0`, `zeroconf>=0.130` and Python >= 3.11, which HA's pins meet from 2025.1 on (2025.7.2: `aiohttp==3.12.14`, `cryptography==45.0.3`, `zeroconf==0.147.0`, Python >= 3.13.2). Derived from `package_constraints.txt`; only 2026.10.4 was install-tested. |
| `aiowebostv` behaviour (source code, v0.10.0) | `WebOsClient(host, client_key=..., client_session=...)` accepts HA's session. `tv_state.is_on` / `is_screen_on` come from the TV's power-state subscription. Default request timeout is 20 s. `connect()` also subscribes to apps, inputs and volume, so a second connection costs a few seconds. On any websocket drop the client clears `tv_state`, so `is_on` turns false until it reconnects; `connect()` raises at once when the TV is unreachable. |
| `WebOsFrameSource` on the TV (#2) | With aiowebostv 0.10.0 on an HDMI input: `start()` 0.20-0.50 s (median 0.22 s over 5 runs), `start()` plus first frame 0.56 s, 4.1 captures per second including download and decode to 64x36, a forced websocket drop reconnected in 0.34 s with `is_on()` on throughout, and a capture started right after a drop waited for the reconnect and succeeded after 0.52 s, an unreachable host fails `start()` after 2.0 s (aiowebostv's connect timeout). HA's own TV entity was unaffected. |
| aiowebostv before 0.7.4 | Cannot connect to this TV's current firmware: the TV never answers the `hello` message, which aiowebostv up to 0.7.3 sends without a payload. Fixed in 0.7.4 ("Fix hello message payload", July 2025), first pinned by HA 2025.7.2. On older HA releases HA's own `webostv` integration has the same problem. |
| Glowcast's own stream (#4) | `GET /auth/v1` with the `hue-application-key` header returns a `hue-application-id` response header. While Glowcast streams, the area's `active_streamer` is `{rid: <that id>, rtype: "auth_v1"}` in both the raw `entertainment_configuration` and `remote_status()`; after the stop it is inactive with no streamer. So Glowcast can recognise its own (stale) stream. |
| `HueEntertainmentOutput` on the bridge (#4) | On a test area with a gradient TV strip (16 channels): `start()` 3.5-4.4 s including the DTLS handshake; `check()` stays fine after 11 s without `send()` (`idle_timeout=0`); after a run killed without stopping, the area stays active and the next `start()` stops that stale stream and starts normally; after `stop()` the area is inactive. |
| DRM app returns black frames (#5) | Prime Video in the TV's own app: every capture is completely black (all pixels 0), at about 6.9 captures per second because the JPEG is tiny. Until #68 the pipeline then showed the minimal glow (at the time a dim neutral white; a warm white since #38); since #68 it releases the lights after a wait of such frames (3.5): 3 s at first, 7 s since #139, so about 8 s with the release. Telling the user is #14. |
| HDMI streamer with a DRM app (#5) | Prime Video on a streaming stick over HDMI: captures show the real picture (not black). Letterbox bars in that film were pure black (0-7 of 255) and changed width between scenes. |
| All-zero frames outside DRM apps (#68) | 55 min of a game console over HDMI at 1 capture per second (3286 frames, while Glowcast synced): no all-zero frame during play; its darkest frames still had a maximum of 100 or more. All-zero frames did occur at transitions: one frame (under 2 s) at a hard cut to a menu, one at an input switch, and 4-6 s while an HDMI device's picture dropped. Hence the 3 s wait before releasing the lights (3.5), 7 s since #139 (next row). Not yet measured: fades in films and series, live TV and the TV's own apps. |
| All-zero frames in a film or series over HDMI (#126) | 60 min of a streaming device over HDMI at 1 capture per second (3592 frames, while Glowcast synced with the 3 s wait): 10 all-zero runs in the middle of normal viewing, after a hard cut from a normal picture or an almost black frame, never after a fade; 4 of them lasted 3.0-5.0 s and each released the lights for 1.4-3.8 s (plus the Hue handshake). No frame was near black without being all zero (darkest maximum 53). So 3 s is too short for this source; the product owner chose a fixed 7 s wait (#139). Not measured: live TV, the TV's own apps, other HDMI devices. |
| `Pipeline` on real hardware (#5) | Real source and output, update rate High, brightness 80 %: first colour 4.1-4.3 s after start (mostly the Hue handshake), 23.9 sends and 2.87-2.90 captures per second over 27 s, no failed captures. With the TV off: `TV_OFF` after 10.0 s, no area touched. TV switched off with the remote during a run: `TV_OFF` through `is_on()` before any capture failed, area released. A Hue app sync running: `OUTPUT_BUSY` 0.9 s after start (after the TV came up), no sends, the sync kept running. |
| Black before "off" (#39) | Switching the TV off with the remote: the TV first shows an almost black switch-off animation (1-2 % of the pixels lit, some nearly white) for 2.7-3.0 s, then `is_on()` turns false (4 tries with whole-frame black: 2.99, 2.84, 2.87, 2.72 s; the last frame with a picture 2.96-3.24 s before). No capture failed in between. A first measurement that counted a frame as black only when every channel region was black found 0.3-0.6 s, because the animation's bright element kept one region lit. `output.stop()` then takes about 1.2 s before the bridge restores the lights. |
| The switch on Home Assistant (#7) | Home Assistant 2026.10.4. `switch.turn_on` to the entertainment area active on the bridge: 2.1 s, then the DTLS handshake (3.5-4.4 s, #4), so the first colour comes after about 5.5-6.5 s. `switch.turn_off` to the area inactive: about 2.1 s (`output.stop()` itself about 1.2 s). TV switched off with the remote while syncing: the switch was off 1.2 s after the TV's `media_player` turned `off`. Switched on with the TV in standby: off again after about 10.5 s (startup grace), area untouched. A Hue app sync running: `output_busy` after about 2 s, the sync kept running. HA restarted while syncing: area inactive 1.8 s after the restart command, long before HA stopped; the switch was off after the restart. Bridge key removed in the Hue app: `auth_failed` and reauth; after pairing again the next switch-on worked with all settings kept. The README automation (TV `off` to `on`) started Glowcast 2 ms after the TV turned on. |
| TV in standby (#7) | With the TV in standby the `webostv` entry stays loaded and its `media_player` reads `off` for a few minutes, then `unavailable`; switching the TV on always passes `off` before `on`. The TV accepts the websocket and closes it at once with code 1008, which aiohttp reports as `WSMessageTypeError`, a `TypeError`: the source treats it as "not reachable". |
| Soak test (#19, 2026-10-29) | Glowcast v0.1.0 installed through HACS on Home Assistant 2026.10.4, update rate Maximum, about 95 minutes of syncing on HDMI sources (a streaming box and a game console): no crash, no errors, no stop on its own. HA core memory went from 1154 to about 1195 MB in the first half hour, then stayed between 1188 and 1238 MB, mostly 1190-1210 MB, for the last hour (no visible growth; too short to rule out a slow leak). TV standby: switch off 1.20 s after the TV's `media_player` turned `off`; in standby the `media_player` turned `unavailable` after 2 min 11 s, and switching on with the remote 10 min later went `unavailable` → `off` → `on` within 0.5 ms, so the README automation started Glowcast (2.3 ms). Bridge power cut while syncing: noticed after about 11 s, one retry, stopped with `OUTPUT_LOST` about 38 s after the bridge went silent, 4 warnings and no errors; the next switch-on worked (area active 0.9 s later). The lights stayed on the last streamed colour after the power cut, while the bridge at first reported them as off; the next start and stop of Glowcast restored them. Accepted as is by the product owner (#91). HA restart while syncing: area inactive 1.27 s after the restart command, switch off afterwards, the next switch-on worked. |
| Screen of a computer as source (#106, cloud sandbox, no real hardware) | A helper with `mss` 10.2.0, Pillow and `aiohttp` is 11 packages, 32 MB, with prebuilt wheels for macOS (arm64, x86_64), Windows (x64, ARM) and Linux. Grab plus downscale to 64x36 took about 9 / 18 / 45 ms at 1080p / 1440p / 2160p on virtual (Xvfb) screens; a 64x36 frame is 6,912 bytes raw. In a browser, `getDisplayMedia()` needs HTTPS, a click and the user's choice of screen every session. Not yet measured on macOS or Windows. |
| Test bench delays (#134) | Glowcast with the screen source in a local Home Assistant (WSL2), the real Hue output on a 2-channel bench area (one light left, one right), a webcam judging both lights; update rate High (3 per second), brightness 80 %, no-picture wait 7 s (#157), hold on black 3.5 s, 30-10-2026 in morning light (ambient 6-21 % of the lights' white), calibrated at exposure -8. From a picture change to the light passing halfway in the camera: pattern mode (from the helper's pattern change; includes the camera's own latency of up to about 75 ms, #133) median 537 ms (330-697, p90 651, 22 changes); screen mode (from the monitor changing to the light changing, both in the same camera frames, so the camera's latency cancels out) median 488 ms (335-688, p90 664, 16 changes). The bridge reports the area active 0.4-0.6 s after the switch-on call (when the stream is claimed, before the light shows a colour). All-zero black: the colours hold for the hold on black, the glow shows, the lights are released 8.4-8.7 s after the change (Glowcast's status says "no picture" then) and are back in their own state (camera) 9.5-9.9 s after it; the next picture claims them again within 0.9 s on the bridge. Near-black (not all zero): the glow 4.0-4.2 s after the change, still streaming. Switch off: area inactive after 1.2-1.3 s, lights back after 2.3-2.6 s. Helper stopped: `tv_off` 5.5-5.7 s later (the screen source's requests to the stopped helper failing until the source reports the computer off; measured from when the helper's process was gone), lights back after 6.5-6.7 s. |
| The lights between the stream start and the first colour (#153) | Test bench (#134), 30-10-2026, `main` (v0.2.0), 4 starts (pattern and screen mode), the light in warm white before each start (its own state): the bridge does not jump the lights to dark when the stream starts. The area was active 0.55-0.77 s after the switch-on, the first colour came 3.45-3.57 s after it; in the 2.8 s between, the light kept its own state (within 4-11 % of its level before the start), then jumped to the first colour. So the fade-in can start from each light's own state as read before the stream (3.5). With the fade-in (PR #188, same bench, 30-10-2026): the first streamed frame of a light in its own cool white (about 4300 K, full brightness) is 0.60-0.63 of its own brightness by camera at every claim, then the fade reaches the picture in 1.5-1.65 s. Streamed full white is about 70 % of the light's own full white, so no streamed colour reaches it: the dip at the start cannot be avoided by the starting colour. |
| HA light actions on Hue lights (#125) | Home Assistant 2026.10.4, `hue` integration (bridge REST API), 4 colour bulbs and the Hue room they are in, measured with the logic of `scripts/try_ha_lights.py` on HA's websocket (2 ms round trip), with the product owner watching. `light.turn_on` with `hs_color`, one call per light: the call returns in about 130 ms (p95 about 200 ms) up to 20 calls per second, and 325 ms at 40 per second, with ticks skipped. The call returns once the bridge accepts the command, so a backlog on the bridge does not show in HA: at 5 updates per second per light (20 commands per second) the lights visibly changed only about twice per second, and other lights on the same bridge stopped responding until the run ended. HA reports a Hue light's new state at most about once per second (the bridge's event stream), so the reported state cannot show whether a light keeps up. A Hue room as the target is one command: the call takes about 70 ms, all members change together, and 2 updates per second showed no backlog. Without `transition` the changes show as steps; with `transition` equal to the interval, as a fade. The product owner set the limit for #9 at 1 update per second and at most 4 lights per bridge, a Hue room or zone counting as one. |
| HA light actions on a Matter light (#125) | One Matter colour bulb on Thread, through HA's `matter` integration, same method. HA reported every update about 90-100 ms after the call (call about 120 ms) up to 5 updates per second; only at 10 per second did HA show trouble (calls about 220 ms, a timeout, 82 % of the colours reported). On the light itself updates were already lost at 2 per second, more at 5, and 10 per second was chaotic; 1 per second was clean, and with `transition` 1 s a smooth fade. With `transition` at 5 per second: 3 timeouts, 38 % reported. So for Matter, too, HA's reported state is no measure of what the light shows. The product owner set the limit for #9 at 1 update per second and at most 4 lights per Thread network. Several Matter lights at once: not measured. |
| Light groups list their members (#9, source code, no hardware) | Home Assistant's light groups and the `hue` integration's rooms and zones (`GroupedHueLight`) both put their member lights in the `entity_id` state attribute, in 2025.7.2 and 2026.10.4 (checked in HA core's `group/light.py` and `hue/v2/group.py`). A Hue room's own `unique_id` is its grouped_light id, so it is not one of the bridge's `light` resources. Not checked: other integrations' groups (Zigbee2MQTT, ZHA). |
| `zeroconf` on Windows on ARM (#108, 2026-10-29, CI and a build check, no ARM hardware) | `zeroconf` 0.151.5 has no `win_arm64` wheel, so uv builds it from the sdist. Its build dependencies (`setuptools`, `Cython`, `poetry-core`) are pure-Python wheels, and its `build_ext.py` falls back to pure Python when compiling fails: built with the compiler switched off, it printed "Failed to build cython extensions" and made a wheel without compiled modules. On GitHub's `windows-11-arm` runner (which has MSVC) uv built it in about 45 s with the compiled modules (`_dns.cp313-win_arm64.pyd`), and the screen helper's tests passed. So the helper installs on Windows on ARM with or without a compiler. |
| A USB webcam for the test bench (#133) | A USB 4K webcam on Windows through OpenCV's DirectShow backend: 54.5 frames per second at 1280x720 MJPG (requested 60); MJPG only sticks when the FOURCC is set after the width, height and fps, otherwise YUY2 at 27 fps. Manual exposure (`AUTO_EXPOSURE` 0.25, `EXPOSURE` in log2 s) and a fixed white balance hold: 30 s within ±2 of 255. On a wall lit by a Hue light, red, green, blue, warm white and off were told apart in 5 of 5 rounds each (dominant channel after a white and a dark reference); a spot facing the camera clips even at 1/1024 s and only shows on and off. A full-screen coloured window on a monitor in view shifted the wall region by at most 3 of 255. A monitor change showed in the picture after 75 ms (median, max 84 ms); a Hue light through HA's REST `light.turn_on` (`transition: 0`, call about 50 ms) after 110 ms (median, all 80 changes under 300 ms). |
| Hue stream from WSL2 (#133) | `hue-entertainment` streams from WSL2 in mirrored networking mode on Windows 11: `start()` including the DTLS handshake in 3.46 s, pattern shown, area inactive after `stop()`. NAT mode and Docker not tested. (The lights that stayed on the last colour after the stop in #133 came from the area's Hue app setting, see the next row.) |
| "When syncing stops" in the Hue app (#142) | Each entertainment area has a Hue app setting for what the lights do when a stream ends (the app offers "Pause on the last colour", "Off", "Last on-state" and some scenes; only the first and third were tested). The bridge applies it to Glowcast's stream too. With "Pause" the lights kept the last streamed colour for the 30-40 s they were watched, in 6 of 6 runs here and 2 of #133: whatever the last colour, with or without a pause before `stop()`, with `PUT stop` before or after the DTLS close, and when the bridge's own idle timeout ended the stream (no `stop()`). With "Last on-state" the camera saw them back 1.7 s after `stop()` returned, in 2 of 2 runs; the TV's area uses that setting, which matches the restores in #7 and #19. The setting is not in CLIP v2's `entertainment_configuration`, and after the stream the bridge and Home Assistant report the lights' state from before it either way, so only looking at the lights shows it. Setting the lights again through Home Assistant brings them back. |
| The bridge's device in Home Assistant (#163) | Home Assistant 2026.10.4 with a Hue Bridge Pro (read from the device registry): the bridge's device has the bridge id as the bridge reports it as a `hue` identifier (16 characters, with `fffe` in the middle), while the `hue` entry's unique id is the normalised 12-character form (`aiohue`'s `normalize_bridge_id`, the same in 2025.7.2). Every device of the `hue` entry, the bridge's included, has a `via_device_id` (2026.10 splits composite devices). The Hue output finds the bridge by the normalised identifier, else as the device most of the entry's devices are linked through. |
| Not yet tested | Other DRM apps (Netflix etc.) and HDCP-protected HDMI sources may also return black frames. Whether each segment of a gradient TV strip shows its own colour on the lights (unit-tested in #4; the test strip has loose contacts, #94). `executeOneShot` is undocumented and could disappear in a firmware update. |

## 3. Architecture: ports and adapters

A pipeline with three stages. The two stages that touch the outside world sit behind a small interface (port). v0.1 had exactly one adapter per port; the screen source (#110) is the second source adapter, loose lights (#9) the second output adapter.

```
                 ┌──────────────────────── Pipeline (one asyncio task) ─────────────────────────┐
 LG TV  ──►  FrameSource  ──►  Frame  ──►  ColorEngine  ──►  list[ChannelColor]  ──►  LightOutput  ──►  Hue Bridge
             (port)                        (pure logic)                               (port)
             ├ WebOsFrameSource                                                       ├ HueEntertainmentOutput
             └ ScreenFrameSource (#110)                                               └ HaLightsOutput (#9)
```

Everything in `core/` (models, ports, colour engine **and the pipeline**) is plain asyncio with no Home Assistant imports. Blocking work and time are injected, so the pipeline is tested with fakes and a fake clock, without Home Assistant's test framework.

### 3.1 Core types (`core/models.py`)

```python
@dataclass(frozen=True)
class Frame:
    width: int
    height: int
    pixels: bytes            # packed RGB, row-major, already downscaled (for example 64x36)

@dataclass(frozen=True)
class Channel:
    id: str                  # stable id, for example the Hue channel id
    name: str
    x: float                 # -1 = left of the screen, +1 = right
    z: float                 # -1 = floor / bottom of the picture, +1 = ceiling / top
    start_rgb: tuple[float, float, float] | None = None   # what the light shows before the claim,
                             # as the output would send it (0.0 - 1.0, brightness included); None: unknown

@dataclass(frozen=True)
class ChannelColor:
    channel_id: str
    rgb: tuple[float, float, float]   # 0.0 - 1.0
```

**A channel with a position is the common language** between outputs and the ColorEngine. The ColorEngine never knows where a position came from. That is what makes loose lights possible later (section 8.1). The bridge also gives a front/back value (`y`); v0.1 ignores it.

### 3.2 Ports (`core/ports.py`)

```python
class FrameSource(Protocol):
    max_rate_hz: float                             # how often capture() may be called (caps the update rate)
    async def start(self) -> None: ...             # connect; may take a few seconds; raises SourceUnavailable
                                                   # or SourceAuthFailed
    def is_on(self) -> bool: ...                   # "TV is on and showing a picture"; false only after the
                                                   # source's own reconnect attempt has failed
    async def capture(self) -> Frame: ...          # raises SourceUnavailable (off/unreachable) within 5 s,
                                                   # or SourceAuthFailed (Glowcast's key rejected)
    async def close(self) -> None: ...

class LightOutput(Protocol):
    max_rate_hz: float                             # how often send() may be called
    dropped_parts: Mapping[str, Exception]         # parts of a combined output that do not run (#11); {} otherwise
    async def start(self, *, retry: bool = False) -> list[Channel]: ...  # claims the lights (the output's own channel limit); raises OutputBusy /
                                                   # OutputAuthFailed / OutputNotFound / OutputTooManyChannels / OutputUnavailable
    def send(self, colors: list[ChannelColor]) -> None: ...   # non-blocking
    async def check(self) -> None: ...             # raises OutputLost (gone / taken over), OutputGone (lost, and the
                                                   # output's own retry failed: no retry by the pipeline) or OutputAuthFailed
    async def stop(self) -> None: ...              # releases the lights; safe to call twice
    async def close(self) -> None: ...             # releases network resources; after stop()
```

Brightness and the minimal glow are applied in `core/` (the light state, 3.5), so outputs stay dumb.

What every source must allow (#110):
- **Its own rate cap.** The capture loop never captures faster than `max_rate_hz`, whatever the update rate (3.6): the webOS source has no cap of its own (`math.inf`, Maximum is as fast as the TV answers), the screen source caps at 4 per second. The generic code only reads the number.
- **`SourceAuthFailed`** when the source rejects Glowcast's own key (the screen helper's token); the pipeline stops with `SOURCE_AUTH_FAILED` at once, without retrying, and the source type's reauth step pairs again (4.1).

What every output must allow (#66):
- **`check()` on every send tick.** The send loop starts a new `check()` whenever none is running, so it may be called many times a second. The output throttles its own status requests (the Hue output asks the bridge at most every 5 s and returns at once in between).
- **`start()` again after `stop()` and `close()`.** The retry after `OutputLost` (3.5) releases the output and then calls `start()` on the same object.
- **The channel limit belongs to the output.** The Hue output raises `OutputTooManyChannels` above Hue's 20 channels per stream; another output has its own limit, or none.
- **What each light showed before, if it can read it (#153).** `start()` reports, per channel, what the light shows before Glowcast claims it (`Channel.start_rgb`), in the output's own send colours: 0-1 RGB with the brightness in it, black for a light that is off; the first colours fade in from there (3.5). An output that cannot read it reports `None`, and that channel fades in from dark; a failed read never fails the start. On the retry after `OutputLost` (and a combined output's retry of a part) `start(retry=True)` says there is no fade-in, so the output need not read it. The Hue output reads the bridge's CLIP v2 `light` resources (read-only, within 5 s, before the stream starts): the channel's member, its entertainment service, its device, that device's light; the `xy` colour at its `dimming` brightness, also in colour-temperature mode (a colour light reports the `xy` of its temperature too), with the same conversion for both modes, because the bridge shows the sRGB colour of a warm white redder (as for the glow, #38); only a light without `xy` starts from the sRGB colour of its temperature 800 K cooler; every segment of a gradient light starts from the light's main colour, a channel of several lights from their average. The loose-lights output takes it from Home Assistant's state of each light, from the states it saves for the restore (so a light that has not reported its restored state yet counts with the state from before, #125), as `_command()` would send it; a group from the average of its members. A combined output passes on its parts' channels as they report them.

### 3.3 ColorEngine (`core/color_engine.py`)

Pure and synchronous. `compute(frame, channels, intensity) -> list[ChannelColor]`

For each channel:
1. **Region:** map `x` to a horizontal band and `z` to a vertical band of the frame, linearly from -1..1 to the frame edges (band size: a constant, start with 30 %).
2. **Average** the pixels in the region, skipping near-black pixels (letterbox bars).
3. **Black picture:** if almost the whole region is black, return black; the pipeline turns that into the minimal glow (3.5).
4. **Intensity:** scale saturation (0 = nearly white, 1 = as on screen, up to 2 = boosted).

**Known limitation (decision D12):** a TV gradient strip's channels sit close to the centre (test area: x and z between -0.4 and 0.4), so with the linear mapping they sample about 30 % in from the edges, not the edges themselves. v0.1 accepts this; stretching channels near the TV to the full screen width is issue #23.

`is_black(frame)` applies the same black level and 5 % share to the whole frame; the pipeline uses it for the hold on sudden black and to decide when the picture is back after no picture (3.5).

`is_all_zero(frame)` is true when every pixel is 0: no picture at all, as a protected app in the TV's own apps delivers (see "Verified facts"). The pixels are the only signal, so any source can deliver such frames and the pipeline needs no knowledge of the TV (#68).

**Performance:** 16 channels on a 64x36 frame in under 20 ms on a GitHub Actions runner (asserted in a test). Target hardware is a Raspberry Pi 4 class CPU.

### 3.4 Adapters (v0.1)

**`WebOsFrameSource`** (`sources/webos.py`)
- Resolves the `webostv` config entry **by its unique id** at runtime and reads `host` and `client_secret` from it. Never copies them.
- `aiowebostv.WebOsClient(host, client_key=..., client_session=async_get_clientsession(hass))`.
- `is_on()` uses the client's own `tv_state.is_on` and `is_screen_on` (power-state subscription), so the pipeline never needs to know which `media_player` belongs to the TV.
- `capture()`: `tv/executeOneShot`, then download `imageUri` with HA's session and `ssl=False`; wrapped in `asyncio.timeout(5)` (aiowebostv's own timeout is 20 s).
- Decodes with Pillow in the executor; use `Image.draft()` for a fast JPEG downscale to about 64x36.
- **The source owns reconnecting.** aiowebostv clears its state on every websocket drop, so a raw `is_on` would read "off" during a hiccup. The source reconnects once; `is_on()` reports off only when that reconnect fails or the TV's power state says off.

**`ScreenFrameSource`** (`sources/screen.py`, #110)
- Pulls frames from the screen helper (8.3, protocol 1) over plain HTTP with HA's session (`async_get_clientsession`) and `Authorization: Bearer <token>`. The helper's address (`host`, `port`), its id and the token are Glowcast's own, in the entry's `data` (5); the source reads them at every start, so an address that discovery updates (#109) is used at the next start.
- `start()`: `GET /v1/info` within 2 s; `401` raises `SourceAuthFailed`. No answer, another status, an answer that is not JSON, an `id` other than the stored helper id or a `protocol` other than 1 raises `SourceUnavailable`, so frames are never asked from another helper or service at that address.
- `capture()` reads a frame only when its `Content-Length` is 6,912, so an oversized body is never read.
- `capture()`: `GET /v1/frame`, each request within 2 s and the whole capture within 5 s. `200` with 6,912 bytes is the `Frame` (64x36 RGB, built directly: no JPEG, no executor job). **No picture (`204`), no answer, another status or a body of another size counts as one failure; the source asks once more after 0.1 s (the helper hands out one grab for 50 ms), and when that fails too `capture()` raises `SourceUnavailable`.** Each failed frame raises, but **`is_on()` turns false only after 3 s of no picture in a row after a frame**, counted from the start of the first failed capture (so one capture that hangs for about 4 s counts in full) (product owner's decision on #110); a frame resets the wait. The pipeline then stops with `TV_OFF`, so a few slow grabs on a busy computer do not stop syncing. The clock is injected (`now`, `time.monotonic` by default), so tests use a fake one. Before the first frame `is_on()` stays true, so the startup grace (3.5) keeps asking while the computer wakes up or unlocks, and stops with `TV_OFF` when no frame comes within it. A computer that is off, asleep or locked answers no picture or nothing. `401` raises `SourceAuthFailed` at once.
- `max_rate_hz = 4`: Maximum is 4 per second for a screen (product owner's decision, #110); Low, Medium and High are unchanged.
- No device of another integration to link to: `device_identifier` and `device_id` return `None`, and Glowcast's own device is "Glowcast <computer name>" (the entry's title). `is_missing` is always false, since no other integration's entry is used. The token and frame contents are never logged; diagnostics show only the shape of the source block (4.4).

**`HueEntertainmentOutput`** (`outputs/hue_entertainment.py`)
- Library `hue-entertainment==0.1.2` (Apache-2.0, pure-Python DTLS): `EntertainmentSession(host, app_key, client_key, idle_timeout=0)`.
- **Bridge host:** the `hue` config entry is resolved **by its unique id** (the bridge id) at runtime; HA keeps its host current.
- **Colours:** convert to **0-255** before building `LightColorCommand`s. Never send 16-bit values: the library maps values above 255 down but sends values of 255 or less as-is, so 16-bit dark values would flash. (Upstream bug, #22.)
- **Before starting:** one raw `GET /clip/v2/resource/entertainment_configuration` (HA's session, `ssl=False`, `hue-application-key` header) returns every area with its `status` and `active_streamer` (the library's area listing has no status). If **any** area is active (a bridge allows one stream in total):
  - streamed by Glowcast's own application id (a stale stream, for example after an HA crash) → stop it with `HueEntertainmentAPI.stop_entertainment(that_area_id)` and continue (`session.stop()` only stops what that session started);
  - otherwise → raise `OutputBusy` and start nothing.
  Glowcast's own application id comes from `GET /auth/v1` with the `hue-application-key` header (response header `hue-application-id`). **Verify early in #4** that `active_streamer.rid` matches this id.
- Then `session.start(area_id, stop_others=False)` inside `asyncio.timeout(30)`; on timeout raise `OutputUnavailable`. Never stop someone else's stream.
- **`stop()` always sends a stop for the chosen area** (`stop_entertainment(area_id)`, shielded from cancellation), even when `start()` was cancelled halfway; otherwise the lights stay in entertainment mode.
- At most 20 channels per area (the HueStream limit); above that raise `OutputTooManyChannels` with the number of channels.
- **Area removed** → `OutputNotFound`. **HTTP 401/403 from the bridge** (key revoked), at start or in `check()` → `OutputAuthFailed`. Bridge or `hue` entry not available → `OutputUnavailable`.
- `check()`: `session.is_streaming` must be true, and every 5 s `session.remote_status()` must still report `active` with Glowcast as streamer; otherwise raise `OutputLost`. One failed status request is tolerated; two in a row count as lost. This also catches a wrong key, which the handshake does not detect.
- The library opens its own aiohttp session (documented exception to the "use HA's session" rule); `close()` calls `session.aclose()`.
- `max_rate_hz = 25`.

**`HaLightsOutput`** (`outputs/ha_lights.py`, #9): loose lights, any colour light in Home Assistant.
- **What it follows** (the `output` block, 5): single lights, a Home Assistant area, or a light group. Lights and groups are referenced by entity registry id (stable when the user renames the entity id), areas by area id. Only lights with a colour mode (`hs`, `xy`, `rgb`, `rgbw`, `rgbww`, read with HA's `get_supported_color_modes`, which falls back to the registry's capabilities while a light is unavailable) are used; lights without a registry entry (YAML without `unique_id`) cannot be referenced and are not offered.
- **`start()` resolves the lights every time** (so also at the retry): an area's lights are the light entities in it, by their own area or their device's, without the light groups in it (their members are lights of their own, so a Hue room in the area would drive its members twice); a group's lights are the members in its `entity_id` attribute (2), and a group in the group is expanded too, each light once (decided in #9). A picked single light that is itself a group (for example a Hue room) is one channel and one command. Unavailable lights are skipped for the run and keep their position. No colour lights (the area or group removed or empty, the chosen lights all removed) raise `OutputNotFound`, with the type's repair issue "no colour lights to follow"; an unavailable group, or lights that are all unavailable, raise `OutputUnavailable`.
- **A channel per light,** with the light's stored position `x`, `z`; a light without one (it joined the area or group later) is at "ambient", (0, 0), the middle of the picture. The presets: left (-1, 0), right (1, 0), top (0, 1), bottom (0, -1), ambient (0, 0). Ambient samples the middle band like any position (3.3), not the whole picture.
- **Commands:** `light.turn_on` with `rgb_color` scaled so its largest component is 255, `brightness` = that component of the light state's colour (so Glowcast's brightness and the glow, 3.5, are in `brightness`; lights take `rgb_color` as the colour only), and `transition` = the light's actual interval (the time since its command before; its interval for the first one and after a wait). Each command is a task; a light whose command is still on its way gets none, a colour that did not change is not sent again, and a colour counts as sent only when the call succeeded, so one the light did not take is sent again at its next turn. A failed call is logged with its type only (its text can name the entity).
- **Rates** (`outputs/ha_lights_schedule.py`, pure, with the time as an argument; product owner's decisions on #9 after #125): each light at most once per second; at most 4 commands per second per network, where a network is a Hue bridge (the light's `hue` config entry) or a Thread network, which do not share capacity. Home Assistant does not show which Thread network a Matter light is on, so all Matter lights of one `matter` config entry count as one Thread network (an approximation: with two Thread networks it is stricter than needed). A Hue room or zone is one command on its bridge (one grouped_light request). A light group of Home Assistant picked as a single light sends one `light.turn_on` per member, so it counts as its members: one command on each member's network, all in the same tick; a group with more members on one network than 4 goes only when that network had no command in the last second. Lights of any other integration have no shared network (not measured yet) and are limited to once per second each. With more than 4 commands per round on one network each light gets an equal share: its interval is `commands / 4` seconds, and `transition` follows it. `max_rate_hz = 4`: the send loop ticks 4 times per second and the schedule serves at most one light per network per tick and never more than 4 commands within a second, less a tolerance of 10 ms: the pipeline's ticks come a few milliseconds late, and without it a network lost a slot every few ticks (3.3 instead of 4 commands per second, a light every 1.25 s while it faded over 1 s). The light that waited longest goes first. A network with more than 4 lights is logged as a warning at every start (the setup warns when saving).
- **Restore:** `start()` saves each light's `State` (for a group picked as a single light, a light group of Home Assistant or a Hue room or zone, its members' states: the group's own state is an average of theirs); `stop()` (shielded, safe twice) cancels and awaits every command still on its way, so none lands after the restore, then sends each light back with `light.turn_off`, or `light.turn_on` with its brightness and the colour attribute of its colour mode (hs, xy, rgb, rgbw, rgbww or colour temperature), within 10 s. It always sends, also when Home Assistant still shows the saved state: a light's report of Glowcast's colour can come after the stop (Home Assistant's `async_reproduce_state` would skip such a light). A light claimed again soon after a release, before it reported its restored state (a Hue light reports about once per second), would otherwise save Glowcast's colour as its state: the states restored at a release are kept per light in `hass.data`, across outputs (every start of the pipeline builds a new one), and saved again instead while the light has reported nothing newer. That runs on every exit path, since the pipeline calls `stop()` on every one (3.5): switch off, TV off, an error, no picture, a retry, unload and HA stop. `close()` has nothing to close. `start()` works again after `stop()` and `close()`.
- **`check()`** asks the states at most every 5 s and raises `OutputLost` only when none of the lights is available any more.
- `is_missing` is always false and `device_id` is `None`: it uses no other integration's entry, and the lights each have their own device.

**`CompositeOutput`** (`outputs/composite.py`, #11): an entertainment area and loose lights together. Generic: it knows only the port of its parts (no Hue, no Home Assistant imports; tested with fake parts and a fake clock). The registry's `combined` type builds it with a part per block (5), named by the part's type.
- **Channels:** each part's channel ids get the part's index as a prefix (`"<index>/<id>"`), so they are unique across parts; `send()` hands each part only its own channels, without the prefix.
- **Rates:** `max_rate_hz` is the fastest part's (25 with an area), so the area is never held back; a slower part (the loose lights, 4) is sent to on the tick nearest to when it is due (`1 / its max_rate_hz`), skipping ticks instead of bursting.
- **Start:** all parts start together. A part that cannot start is dropped and the rest starts; with none started, the first part's error is raised (the pipeline stops with its reason). A part whose integration entry is missing is not built and is dropped from the start (its repair issue, 4.4).
- **Lost while running:** `check()` returns at once; it checks each running part in a task of its own and handles what the part raised. A lost part (`OutputLost`) is released in the background (`stop()`, `close()`) and started again after 5 s while the other parts keep being sent to; when that retry fails, or the part is lost again within 60 s of a successful retry, it stays dropped until the next start (a new `CompositeOutput`, built at every switch-on and options restart; a no-picture release and claim keeps it dropped). A rejected key (`OutputAuthFailed`) drops the part without a retry. After a retry, only the channels the part had at the start get colours. With no part left and no retry pending, `check()` raises `OutputGone` (or, as for a single output, the last part's `OutputAuthFailed` or unexpected error), so the pipeline stops with `OUTPUT_LOST` (or `AUTH_FAILED`, or `OUTPUT_UNAVAILABLE`) without a retry of its own; a part whose retry failed counts as lost: the two retries never add up.
- **Reporting:** `dropped_parts` maps each part that does not run (could not start, waiting for its retry, dropped) to its error; the pipeline reports it in its status (3.5) and the runtime does each part's follow-up (4.4). The composite logs one warning per event: the part lost (and retried in 5 s), the retry worked, the retry failed, could not start.
- **Release:** `stop()` (shielded, safe twice) cancels the retries and checks, waits for the background releases and stops every part, also the dropped ones and when one of them fails; `close()` closes every part.

### 3.5 Pipeline (`core/pipeline.py`)

One supervisor task per config entry, started and stopped by the switch. Not a `DataUpdateCoordinator`. The blocking-call runner and the clock are injected, and the pipeline reports what it is doing through one injected callback (`on_status(status)`, on the event loop), so the Home Assistant side can clear repair issues, turn the switch off or start reauth.

```
start:    try source.start() and one good capture, retrying, for up to 10 s (startup grace)
          → if not: stop with reason TV_OFF; the lights were never touched
          channels = output.start()   (with what each light showed before)
          → light_state.fade_in(each channel's start_rgb)
run:      two concurrent sub-tasks under the supervisor:
            capture loop: every 1/update_rate s: frame = source.capture() → target = engine.compute(...)
                          → light_state.set_target(target, frame black?, now)
            send loop:    every 1/25 s: output.send(light_state.colors(now, brightness));
                          every 5 s: output.check()
          the send loop keeps its 1/25 s rhythm even while a capture hangs (up to its 5 s timeout)
no picture: frames all zero for 7 s → output.stop() → output.close(); capture on, without sends
          until a frame that is not black → channels = output.start() → fade in → run again
stop:     cancel both sub-tasks → output.stop() → output.close() → source.close()   (on every exit path)
```

**The pipeline only supervises** (start order, startup grace, the two loops, the `OutputLost` retry, stop handling and cleanup). What the lights show between frames is kept by **`LightState`** (`core/light_state.py`): pure and synchronous, with the time as an argument, so it is tested without the pipeline. It fades from the current colours to each new target over the measured capture interval (in engine colour space), holds the last colours on sudden black, and applies brightness and the minimal glow when the send loop asks for the colours. When the lights have just been claimed it fades the first colours in from what each light showed before (below). After a retry the pipeline tells it which channels still exist; the gap is not used as a capture interval. Rule for later: memory about colours or the picture (for example letterbox bars, #34) lives in small helpers like this next to the colour engine, not in the supervisor.

- **Brightness:** `c = c_engine * brightness` per channel, in 0-1 RGB.
- **Minimal glow:** then `out = c + max(0, 1 - m / g) × g × warm` per channel, with `m` the brightest component of `c`, `g = min(0.10, brightness)` and `warm = (1.0, 0.78, 0.50)`. On black this is the whole glow, `g × warm`: a dim warm white of about 2700K on the lights at 10 %. The glow is for dark channels: it gets smaller as the channel gets brighter and is gone once the brightest component reaches `g`, so bright, saturated colours stay pure, and the colour changes continuously during a fade (#43). While the glow is added, no component goes above `g`, so the result is never brighter than the brightness setting. Constants, not options. Examples: a black picture at 80 % gives (0.10, 0.078, 0.050); brightness 40 % and a red picture give (0.40, 0, 0); brightness 20 % and a blue picture give (0, 0, 0.20); brightness 50 % and an engine colour (0.10, 0, 0), so `c` = (0.05, 0, 0), give (0.10, 0.039, 0.025); brightness 3 % and black give (0.03, 0.0234, 0.015). Until #43 the glow was a floor per RGB component, `max(c, g × warm)`, which mixed warm white into every colour (a blue at 20 % became lilac). The level and the colour were chosen on real hardware (#38): the warm ratio gives less light than a neutral glow at the same level, so the level is 10 % instead of the earlier 5 %; and the bridge renders sRGB 2700K (about 1.00 : 0.65 : 0.35) too red, especially in a dark room, so the ratio is two steps cooler in sRGB.
- **Fade-in after a claim (#153):** at a start, and when the lights are claimed again after no picture, the first colours fade in over 2 s (`FADE_IN`, a constant in `core/light_state.py`, not an option) from what each light showed before (`Channel.start_rgb`, 3.2), or from dark when the output could not read it. The fade-in is a blend in sent colours, after brightness and the glow: `out = start + (out_normal - start) × p`, with `p` going linearly from 0 at the first target to 1 after `FADE_IN`. So a light that was warm white goes from warm white to the picture, one that was off fades up from dark, a dark first picture fades in to the glow (not to full brightness), and a change of brightness during the fade-in shows at once. Later frames during the fade-in change `out_normal` (the usual fade between frames goes on underneath), they do not restart it. No fade-in after a live settings change (brightness, intensity, update rate: the lights stay claimed) or after the `OutputLost` retry (the lights come back where they were); a part of a combined output that comes back after its own retry (4.4) gets the current colours at once too, since its channels never left the pipeline. A change of the area (or other lights) restarts the pipeline (4.2), so it fades in like a start. The bridge keeps each light in its own state from the stream start until the first colour (#153, see "Verified facts"), so there is no drop to dark first. With an entertainment area a bright light still dips a little at the first streamed colour (to 0.60-0.63 of its own brightness on the bench) before the fade, because the stream cannot show a light's own full white (streamed full white is about 70 % of it).
- **Hold on sudden black:** when the whole frame turns (almost) black after a picture, keep the last colours for 3.5 s before fading to the glow. "Whole frame black" uses the colour engine's black level and 5 % share over the whole frame (`is_black()` in 3.3's module), so a small bright element such as a TV's switch-off animation does not count as a picture. A TV being switched off shows such an animation for up to 3.0 s before it reports off (see "Verified facts"), so the lights go straight from the picture to their previous state; a genuinely black scene reaches the glow 3.5 s later. Starting on black shows the glow at once. The hold only changes colours, never when the pipeline stops. A constant, not an option (#39).
- **No picture (#68):** when the frames have been all zero (`is_all_zero()`, 3.3) for 7 s, the pipeline releases the lights (`output.stop()` and `output.close()`, as for the `OutputLost` retry), so they return to how they were before syncing; the switch stays on. During the wait an all-zero frame is a black frame, so the hold on sudden black keeps the last colours for 3.5 s from the first black frame and the glow shows for the rest of the wait (sooner if the picture was already black); a frame that is not all zero ends the wait. The wait is a constant, not an option: all-zero moments in a film or series over HDMI, after a hard cut, lasted up to 5 s (#126, see "Verified facts"; 3 s until #139), so they do not release and claim the lights again. While released, the pipeline keeps capturing at the update rate, without sends or `check()`, and still watches `source.is_on()` and failed captures (`TV_OFF`, 4.4). The first frame that is not black by the whole-frame rule claims the lights again (`output.start()`, raced against a stop by the user, start errors handled as at switch-on), with a fresh `LightState`, so the first colours fade in from the lights' own state, as at a start; almost black frames (subtitles or player controls over no picture, a switch-off animation) do not, because each claim costs the bridge's handshake. Switched on while the frames are all zero, it starts released without claiming the lights. Releasing and claiming each log one line at info level.
- **Status:** a frozen `PipelineStatus` with the `phase`, the number of `channels` (while the lights are claimed; 0 before and once they are released, so also in the `STOPPED` status and after a failed retry, #105), the `stop_reason` (only when stopped) and the measured `captures_per_second` (frames delivered per second, counted in the capture loop from the end of one capture to the end of later ones; `None` until measured, cleared when the lights are released for a retry or for no picture until the next measurement (#105), kept after a stop) and `dropped_parts` (the parts of a combined output that do not run, by name, with a `StopReason` per part from the output's `dropped_parts`: `OUTPUT_LOST` for a lost one, otherwise as for a start error; reported when it changes, read on every send tick; empty for one output, #11) and `black_picture` (while streaming: the whole frame is black by `is_black()`, a dark scene with the hold on black or the glow; also during the 7 s wait before a release for no picture, since an all-zero frame is black; false in every other phase and cleared when the lights are released, #14). It is reported on every change of phase, when `black_picture` changes, and while streaming about once per second with a new `captures_per_second` (never per frame, so entities do not write their state 25 times per second): `STARTING` (waiting for the TV, then claiming the lights), `STREAMING` (the lights are claimed; also after a successful retry), `RETRYING` (the stream was lost, before the retry), `NO_PICTURE` (the TV shows no picture and the lights are released, #68; `channels` is 0 and `captures_per_second` is `None`) and `STOPPED` (the lights are released; always the last report, exactly once, with the reason). A stop is only reported this way; there is no separate stop callback. The phases are an enum that can grow; `STREAMING` says what the pipeline does, not what the switch shows. A callback that raises is logged and does not stop the pipeline.
- **Stop reasons:** `USER`, `TV_OFF`, `OUTPUT_LOST`, `OUTPUT_BUSY`, `OUTPUT_NOT_FOUND`, `OUTPUT_TOO_MANY_CHANNELS`, `OUTPUT_UNAVAILABLE`, `AUTH_FAILED` (the output rejected Glowcast's key), `SOURCE_UNAVAILABLE`, `SOURCE_AUTH_FAILED` (the source rejected Glowcast's key, #110).

### 3.6 Update rate

| Level | Captures per second | Use |
|---|---|---|
| Low | 1 | calm background light, lowest load on the TV |
| Medium | 2 | |
| High (default) | 3 | |
| Maximum | as fast as the TV allows (about 4 on a 2023 G3) | |

The TV cannot go faster than about 4 per second, so there is no level above Maximum. A source caps the rate with its own `max_rate_hz` (3.2): the screen source has the same four levels, with Maximum at 4 per second (#110). The lights are always updated 25 times per second by fading, whatever the level.

## 4. Home Assistant integration

### 4.1 Config flow (first setup)

Each source and output type provides its own steps, as a mixin of `async_step_<id>` methods next to its adapter (`sources/webos_flow.py`, `sources/screen_flow.py`, `outputs/hue_entertainment_flow.py`, `outputs/ha_lights_flow.py`). The registries name each type's mixin and first step, and `config_flow.py` combines the mixins into the one registered flow. That flow has no Hue or webOS knowledge (8.1, 8.2): it runs the source type's steps, then the output type's, and creates the entry from what they hand back (`flow.py` describes that handshake). The first screen is a menu of the source types that have setup steps, labelled by each type's texts (`config.step.user.menu_options.<first step>`); with only one such type, its first step is shown without a menu. After the source's steps comes the same kind of menu for the output types (`config.step.output.menu_options.<first step>`, #9), whose labels carry the short line on which to choose: an entertainment area is the smoothest, loose lights take any colour light at about one change per second. The output types' steps use only what the setup and the options flow both have, so the options flow runs them too (4.2).

Today the steps are:

1. **What the lights follow** (the generic menu): **LG webOS TV** or **Computer screen**.
   - **TV** (the webOS source type): pick a loaded `webostv` config entry that has no Glowcast entry yet.
   - **Computer screen** (the screen source type, #109, `sources/screen_flow.py`): the helper's address (IP address or host name) and port (default 8488), checked with an unauthenticated `GET /v1/info` (the helper answers 401); then the **pairing code** step. Home Assistant's zeroconf discovery (`"zeroconf": ["_glowcast._tcp.local."]` in the manifest) starts the same flow at the pairing code step (`async_step_zeroconf`, provided by the screen source type), shown under Discovered as "Glowcast - <name>" (`config.flow_title`, `title_placeholders` from the TXT `name`). A discovery without a TXT `id` or with another `protocol` than 1 is not shown. `async_step_zeroconf` lives in the screen type's steps: that is fine while one type is discovered; a second discovered type needs the generic flow to dispatch by service type (`discovery_info.type`) to the type that announces it.
   - **Pairing code** (`screen_code`): `POST /v1/pair` with the 6 digits; a wrong code (403 `wrong_code`, or not 6 digits, which is not sent) shows "the code is not correct; check the code the helper shows" on the same form; a helper without a code (`pairing_unavailable`), an unreachable helper, a helper with another id than the discovered one, and another protocol each have their own error, and the user can try again. Then `GET /v1/info` with the new token gives the name (the entry's title, so the device is "Glowcast <computer name>") and checks the id and protocol 1. The source block is `{"type": "screen", "helper_id", "host", "port", "token"}` (5).
2. **Which lights follow the picture** (the generic menu, #9): **Hue entertainment area** or **Loose lights**.
   - **Loose lights** (`outputs/ha_lights_flow.py`): no bridge is asked for or paired, and the entry has no `bridge` block. What to follow (single lights, an area, a light group; an error on the same form when Home Assistant has none with colour lights), then the lights (an entity picker limited to colour lights in the entity registry), the area or the group (dropdowns of those with colour lights). Then a hint when some of the lights are Hue lights that can join an entertainment area (see below), a place per light (`ha_lights_position`: the presets or Fine-tune, then `ha_lights_fine_tune` with two sliders, -100 to 100 %), one light per page, and a warning before saving when more than 4 lights share one Hue bridge or Thread network (`ha_lights_rate`, 3.4). The output block is then stored with a position for every light it has now.
   - **The Hue hint:** for the picked lights of the `hue` integration, Glowcast asks their bridge, with the host and key of Home Assistant's own `hue` entry (read at runtime, never stored or logged), for `/clip/v2/resource/light`, `/clip/v2/resource/entertainment` and the areas. A light whose device is an entertainment renderer can join an area: the setup says that an area follows the TV much more smoothly (`ha_lights_hue_hint`), or, when the bridge has no area yet, how to make one in the Hue app with a link to the site's Loose lights page (`ha_lights_hue_no_area`). Hue lights that cannot join one (older ones, other brands' lights on the bridge) and a bridge that does not answer get no hint. Glowcast does not create areas itself (product owner's decision on #9).
3. **Hue bridge** (the Hue output type, as are the steps after it): pick a loaded `hue` config entry with `api_version == 2` (older bridges cannot stream).
   - First check that the bridge answers (unauthenticated `GET https://<host>/api/config`, `ssl=False`); if not → "could not reach the bridge". (`pair()` raises the same `TimeoutError` for every failure, so reachability must be checked first.)
   - Then a form "press the link button on your bridge, then click Submit", followed by a **progress step** running `HueEntertainmentAPI(host).pair(device_type=...)` (up to 30 s). The returned `username` is stored as `app_key`, `clientkey` as `client_key`. Timeout → "button not pressed, try again", unless the bridge no longer answers (checked again, because the library retries network errors until its deadline and then raises the same timeout) → "could not reach the bridge". Always call `api.close()` afterwards; closing the flow cancels the pairing.
   - `device_type` is `glowcast#<name>`, truncated to the bridge's limit (20 characters before `#`, 19 after).
4. **Entertainment area:** pick from the bridge's list, read with one raw `GET /clip/v2/resource/entertainment_configuration` (HA's session, `ssl=False`), here and in the options flow; the library's area listing also fetches every device and light. If there are none, or the list cannot be read, keep the screen open with a message (for none: how to create one in the Hue app); Submit reads the list again, so the user does not pair a second time (which would add another app on the bridge).

Rules:
- **One Glowcast entry per TV:** the entry's unique id is the `webostv` entry's unique id; adding the same TV twice aborts.
- **One Glowcast entry per computer:** the entry's unique id is the helper's id (TXT `id`, or the `id` from pairing). A helper that is discovered again, for example at a new address, shows no new discovery; its `host` and `port` are updated inside the entry's `source` block (`_abort_if_unique_id_configured(updates=..., reload_on_update=False)`). The entry is not reloaded, which would stop a running sync: the source reads the address at every start. Typing the address of a helper that has an entry also updates the address, stores the token of the pairing that just worked (so it also works as a reauth at a new address; the reauth step itself pairs at the stored address) and aborts ("already set up for this computer"); its manual flow does not wait for a discovery of the same helper (`raise_on_progress=False`), and creating the entry removes that discovery.
- **Two TVs on one bridge** are allowed, but only one can stream at a time: the second one gets "another app is streaming".
- **Reauth:** when the pipeline stops with `AUTH_FAILED`, the HA side calls `entry.async_start_reauth(hass)`. The generic reauth step runs the reauth step of the entry's output type. With `SOURCE_AUTH_FAILED` (#110) it passes `{"glowcast_reauth_part": "source"}` as the flow's data, and the generic step runs the source type's `reauth_step` instead, so a rejected helper token never leads to pairing with the bridge again (each pairing adds an app on the bridge). A type without a reauth step (today the webOS source) aborts with `reauth_unavailable`. The screen source's reauth step (`screen_reauth`, #109) asks for a new pairing code, checks that the helper that answers has the stored id, stores the new token in the `source` block and keeps everything else; no reload, as the token is read at every start. For the output, HA then shows a re-authentication notification; the user presses the bridge button again and the new keys are stored with all settings kept. Opening the settings screen with a rejected key also starts reauth (instead of showing the area list).
- **Security:** Glowcast's `app_key` is a full bridge user, not only a streaming key. Diagnostics never include it (4.4). The bridge has no API to remove an app, so removing Glowcast's entry leaves the app on the bridge. When the entry is removed, `async_remove_entry` lets the output type say what the user must clean up: the Hue output type shows a persistent notification with the steps in the Hue app (the same as on the website's "Uninstalling" page), for an entry that paired with a bridge. Discovery of a screen helper trusts the TXT `id` on the local network, as Home Assistant integrations commonly do: a device on the network that claims another helper's id can move that entry to its own address and so receive Glowcast's token at the next start. That is acceptable because the token only unlocks that one helper (its small frames and its info), not Home Assistant, the lights or anything else. The id check at every start (`/v1/info` must report the stored id, 3.4) keeps an unrelated service at that address from being used; it does not stop a device that claims the id.
- **Why a separate Hue pairing:** HA's `hue` config entry stores only an `api_key`. Streaming needs a `clientkey` (the DTLS pre-shared key), which the bridge hands out only when an app pairs with `generateclientkey`. It cannot be read back later, so Glowcast pairs once itself.

### 4.2 Options (the settings screen)

| Option | Values | Default |
|---|---|---|
| Output type | Hue entertainment area / Loose lights (#9) | chosen at setup |
| Entertainment area, or the loose lights with a position each | list from the bridge; lights, an area or a light group | chosen at setup |
| Brightness | 1-100 % | 80 % |
| Intensity | 0-200 % | 100 % |
| Update rate | Low / Medium / High / Maximum | High |

The screen has three parts (#9): a menu of the output types (`options.step.init.menu_options.<options step>`), then the chosen type's own steps, then the common settings (`settings`: brightness, intensity, update rate). Each output type provides `options_steps`, a mixin of `async_step_<id>` methods for the options flow, and `options_step`, its first step; its steps end, as at setup, with `async_output_chosen(data, output)`. The generic options flow only chains the parts, and stores the `data` the type hands back (for example a new `bridge` block) in the entry's data before it saves the options, so the output finds it when the options change starts it.
- **The Hue type** (`hue_options`): with a `bridge` block, the area from the bridge's current list (aborts when the bridge cannot be reached; a rejected key starts reauth); without one (an entry set up with loose lights), the setup's own bridge, link, pairing and area steps, so the user pairs from the settings. The `bridge` block is kept when the user switches to another output type, so switching back needs no new pairing.
- **The loose-lights type** (`ha_lights_options`): the setup's steps with the stored choice and positions as defaults. For an area or a group whose lights changed since they were placed, a notice first (`ha_lights_changes`): the lights that joined (to place; until then they follow ambient) and the lights that left (no longer used; their positions are removed when the user saves). The Hue hint (4.1) shows here too, and reads the host and key of Home Assistant's own `hue` entry at runtime, as at setup: nothing of it is stored in Glowcast's entry.
- **Both together** (`combined_options`, #11): an output type of its own (`outputs/composite_flow.py`, generic), offered only in the settings screen. It runs every other type's options steps in the order of the registry (the area's, which may pair first; then the loose lights', each with its own stored block as defaults) and stores their blocks as one combined block (5). It collects each type's `async_output_chosen`: the generic options flow's own steps live in a base class after the types' mixins, so the combined mixin's `async_output_chosen` comes first and hands the combination on. Types read and write their own block with `outputs/blocks.py` (`output_block`, `with_output_block`), alone or inside the combination; the flows provide `chosen_outputs` (the data and block each earlier type handed in this flow), so the loose lights' steps know the area chosen just before. A Hue light already in the entertainment area cannot also be a loose light (#132 merged into #11): the loose lights' steps ask the bridge, with Home Assistant's own `hue` entry, for the area's `light_services` and the lights of the devices its channels stream to (the host and key of that entry, read at runtime, nothing stored in Glowcast's entry), and refuse single lights, an area or a group with such a light (or a group with such a member) with an error naming them (`ha_lights_in_area`); the **Lights** and **Entertainment area** entities refuse such a choice too (`lights_in_area`). A bridge that does not answer refuses nothing. The same check runs at every start of the loose lights (#198), for a light that joined the area later in the Hue app: it is left out of the loose lights for that run (before their states are saved, so the release never touches it) and logged with its name, and the repair issue `loose_lights_in_area` (the type's notice, 4.4) names it and says how to fix it. With every loose light in the area the part does not start (`OUTPUT_UNAVAILABLE`, without the `ha_lights_not_found` issue) and the area streams on. A bridge that does not answer leaves nothing out. When the area's step cannot read the bridge's areas (the bridge cannot be reached, has none, or rejects the key, which also starts reauth), it does not abort the combined settings: with a stored area it shows a notice (`hue_area_kept`), keeps that area and hands it on, so the loose lights' steps still run (#197). The flow tells the types it runs them as parts with `combining` (`flow.py`). An area alone, or no stored area, aborts as before.
- **What stays in the screen** (product question on #9): everything as before, plus the output type. Recommended to the product owner and built that way; the alternatives were only what is not an entity, or a "settings only" menu item.

The TV and bridge are fixed after setup (remove and re-add to change them); a bridge can be added from the settings by switching to an entertainment area. The same values are also entities (4.3); both write the entry's `options`, the only copy. Every change goes through one update listener, and the config entry is not reloaded, so the switch stays on (#13):
- **Brightness, intensity, update rate:** passed to the running pipeline with `pipeline.update_settings(Settings)`, without a restart and without a new Hue handshake. Brightness applies at the next send tick, intensity and the update rate at the next capture (the capture loop reads its interval on every capture).
- **Another output type** (for example from an entertainment area to loose lights, or to both together; the types compared are the parts of a combined block): **reloads the entry** (`async_schedule_reload`), so the type's entities change with it (#9, from #13's note); syncing stops, and the switch is off after the reload. At setup the entities of the other output types are removed from the entity registry (by the keys in each type's `entity_keys`), so the previous type's entity does not stay as an orphan. The repair issues of the previous choice, including a missing integration entry, are removed.
- **What follows the TV within the type** (the `output` block, compared as a whole: the entertainment area; the loose lights, area or group and their positions): **restarts only the pipeline**. The listener gets no old options, so the runtime remembers the output block its pipeline started with. A changed output block also clears the repair issues about the previous choice (`area_not_found`, `too_many_channels`), also while not syncing; the next start raises them again if the new choice has the same problem.
- **Not syncing:** the values are stored; the next start reads them.

When the options screen reads the bridge's areas, it hands the list to the entertainment-area entity (a dispatcher signal from the Hue output module), so both show the same list. In HA 2025.x and later an `OptionsFlow` must not assign `self.config_entry` itself.

### 4.3 Entities

One device "Glowcast <TV name>", linked to the TV's device with `via_device`, with:
- **switch** (start/stop syncing), under Controls. The switch is the device's main feature, so it has no name of its own, its unique id is the entry id and its id is `switch.glowcast_<tv name>` (unchanged since v0.1). After an HA restart it is off, unless Glowcast synced when HA stopped (#183): the switch is a `RestoreEntity` whose extra data `resume` says so (true while HA is stopping and Glowcast syncs, or its pipeline stopped because HA was stopping; false in the dumps every 15 minutes, so a crash does not resume). Then the runtime (`async_resume`) waits until HA has started and looks for the picture source every 10 s for 3 minutes, by connecting to it and asking one picture (`start()`, `is_on()`, `capture()`, `close()`, at most 15 s each; a locked screen answers without a picture, so it counts as off); once it is on, it starts as if switched on, through the normal start. A start or stop meanwhile (the switch, an automation, an unload, Home Assistant stopping) ends the looking, also before Home Assistant has started. The switch's state itself is not restored.
- **Configuration entities** (`entity_category: config`, #13), one per setting, with a translation key and the unique id `<entry id>_<key>`: **brightness** (`number`, 1-100, step 1, %, slider), **intensity** (`number`, 0-200, step 1, %, slider) and **update rate** (`select`, the `UpdateRate` values with translated state names). They read their value from the entry's `options` through `read_settings()` on every state write and store a change with `write_settings()` and `async_update_entry`; they keep no copy. The runtime notifies them of every options change, so they follow the options screen and each other. An older entry without the settings keys shows the defaults.
- **Status** (`sensor`, `device_class: enum`, unique id `<entry id>_status`, #14), derived from the runtime's last `PipelineStatus` and whether it syncs: `idle` (never started, a start refused because a referenced entry is missing, or stopped with `USER`), `starting` (phase `STARTING` or `RETRYING`, and switched on before the pipeline's first report), `streaming`, `black_picture` (`STREAMING` with `black_picture`), `no_picture` (`NO_PICTURE`), `tv_off` (stopped with `TV_OFF`) and `error` (stopped with any other reason). For `tv_off` and `error` the attribute `stop_reason` holds the `StopReason` value; while syncing with a combined output, the attribute `dropped_outputs` maps each part that does not run (by output type) to its `StopReason` value (#11; the state stays `streaming`); the repair issues explain each one, so the sensor has no long texts. The state names and the reasons are translated. The texts name "the picture source", not the TV, since `tv_off` also covers a computer screen (the screen helper stopped or the screen is locked); the keys keep their names, so automations keep working (#155). The pipeline's and the runtime's log lines say "the picture source" too.
- **Capture rate** (`sensor`, `entity_category: diagnostic`, disabled by default, unique id `<entry id>_captures_per_second`, #14): `captures_per_second` rounded to one decimal while the phase is `STREAMING`, unknown otherwise (not measured yet, after a retry or a release, and after a stop, though the status keeps the last measurement for diagnostics). The unit is translated through `unit_of_measurement` in the entity translations: the state attribute is always the English one (`captures/s`, Home Assistant takes it from English whatever its language), and the frontend shows the translation. No `state_class`, so no long-term statistics.
- Both sensors are generic (no webOS or Hue knowledge) and follow the runtime's status listener (`async_add_status_listener`, every status of the running pipeline, about once per second while streaming) next to its general listener; they write their state only when their value or attributes change. The switch and the configuration entities follow only the general listener (starts, stops and options changes), so they are not written once a second.
- **What follows the TV**, provided by the output type (decision in #61). The generic `number.py` and `select.py` add the common settings plus whatever the entry's output type provides for their platform (`output_entities()` in `outputs/__init__.py`); they know nothing about Hue or areas. A type that provides nothing gets the common settings only. #66 follows the same pattern for the setup and options steps.

The Hue entertainment-area output provides the **entertainment area** (`select`, `outputs/hue_area_select.py`, unique id `<entry id>_entertainment_area`): the options are the bridge's areas by name (sorted; a name used twice gets a number, " (2)", in the order of the ids, so the labels stay stable), and it stores the chosen id in `options["output"]["area_id"]`. Areas with more than 20 channels are listed; the repair issue handles them at start. A stored area that is not in the list (removed in the Hue app) shows as unknown.
- **The list** is read in the background (`async_areas()`, one raw request), never during platform setup: when the entity is added, after every stop and every change while not syncing (so an area removed in the Hue app shows as unknown after the failed start; not for the stop when HA stops or the entry unloads), when the bridge's `hue` entry is (re)loaded (`SIGNAL_CONFIG_ENTRY_CHANGED`, present in 2025.7.2), when the options screen has read it, and after a failed read with a backoff (30 s, doubling up to 10 minutes; a list that arrives otherwise cancels the pending retry). No polling once it is read. While the `hue` entry is not loaded there is nothing to ask; its own setup retry tells when the bridge answers again.
- **Bridge unreachable at HA start:** the entity is a `RestoreEntity` and keeps the last list as extra restore data (`{area id: name}`, in HA's restore state, not in the entry), so it still shows the stored area by name. Without a restored list (the first start after the update with the bridge down) it shows unknown until the list is read; the stored id is kept.
- **Rejected key (401/403):** the stored value is kept and this is logged once; reauth is started by the switch-on and the options screen, not by the entity.

The loose-lights output provides **Lights** (`select`, `outputs/ha_lights_select.py`, unique id `<entry id>_loose_lights`, #9): the options are the Home Assistant areas and the light groups that have colour lights, by name (sorted; a name used twice gets a number); while single lights are used the current option is `chosen_lights` ("Chosen lights", translated as a state), which is offered only then, since single lights are picked in the settings screen. Selecting an area or a group stores it in the `output` block (`follow`, `area_id` or `group`), which restarts a running pipeline on its lights; their positions are kept, and lights without one follow ambient. An area or group that no longer has colour lights shows as unknown. The list is made again on area and entity registry changes, when a light first appears (its registry entry comes before its state), when Home Assistant has started, and on every start, stop or options change.

The entities can be used in automations, dashboards and voice.

Example automation (on the website's "Starting automatically" page):

```yaml
triggers:
  - trigger: state
    entity_id: media_player.living_room_tv
    from:
      - "off"
      - "unavailable"
    to: "on"
actions:
  - action: switch.turn_on
    target:
      entity_id: switch.glowcast_living_room_tv
```

The trigger accepts both `off` and `unavailable`, because a TV that Home Assistant has not reached since its start can go from `unavailable` to `on`; it never fires from `unknown`, so a restart of Home Assistant while the TV is on does not start Glowcast (#49); a restart while Glowcast syncs is the switch's own resume (4.3, #183). The blueprint `blueprints/automation/glowcast/start_with_tv.yaml` ("Start Glowcast when the TV turns on", linked from the website) uses the same trigger, asks for the TV and the Glowcast switch, and can also turn the switch off when the TV turns off.

The startup grace period (3.5) covers the seconds between the TV reporting "on" and captures working.

### 4.4 Lifecycle and ownership

| What | Where | Story |
|---|---|---|
| Config flow, options flow and reauth flow (the screens): chaining the types' steps and parts, creating and storing the entry (4.1, 4.2) | `config_flow.py`; `flow.py` | #6, #66 |
| Each type's own steps and options part: the TV step; the screen helper's discovery, address, pairing code and reauth steps; the bridge, link, pair, area and reauth steps and the area on the options screen (or pairing from it); the loose lights' choice, lights, area, group, hint, positions, warning and notice | `sources/webos_flow.py`; `sources/screen_flow.py`; `outputs/hue_entertainment_flow.py`; `outputs/ha_lights_flow.py` | #66, #109, #9 |
| The loose-lights output (3.4): resolving the lights, the commands, the restore, its repair issue; its rate limits; its Lights entity | `outputs/ha_lights.py`; `outputs/ha_lights_schedule.py`; `outputs/ha_lights_select.py` | #9 |
| The screen source (3.4): frames, no picture, the rejected token and the 4 per second cap; pairing and the helper's info for its steps | `sources/screen.py` | #110, #109 |
| The entry's lifecycle: `async_setup_entry` / `async_unload_entry` (which create and stop the runtime in `entry.runtime_data`), stopping on `EVENT_HOMEASSISTANT_STOP`, and the update listener that hands every options change to the runtime (4.2) | `__init__.py` | #6, #7, #13 |
| The runtime (`GlowcastData`, one per entry, reached by every platform through `entry.runtime_data`): running the pipeline with `entry.async_create_background_task`, stopping and restarting it, its listeners (the general one for starts, stops and options changes; the status listener for every status of the running pipeline, without a stop during an options restart), reacting to the pipeline's status (clear the repair issues when the lights are claimed, also after a retry; on a stop: switch off, start reauth, repair issue), keeping the last status after a stop until the next start (`GlowcastData.status`, `None` if never started), and raising and removing the repair issues. Each issue, its translation key and its log message come from the source or output type (`Issue` in `issues.py`); the generic code only adds the entry id to the issue's key. Only the running pipeline's reports count: after an options restart, a late report from the old pipeline is ignored. On an options change (`async_options_updated`): notify the entities, then pass the settings to the running pipeline, or restart it when the output block differs from the one it started with (4.2) | `runtime.py` | #7, #63, #64, #13, #14 |
| Reading the settings from `options` into `Settings` (defaults from `const.py` for missing keys) and writing them back; used by the setup flow, the options flow and the pipeline start | `settings.py` | #63 |
| The base entity: `has_entity_name`, the device "Glowcast <TV name>" with its link to the TV's device (`via_device_id` on newer HA, `via_device` on 2025.7.2), following the runtime's listener (`_async_runtime_changed`, which a subclass may override to write less often) | `entity.py` | #63 |
| The status and the capture rate (4.3): the value from the last `PipelineStatus` and the switch, the stop reason attribute, and writing only on a change | `sensor.py` | #14 |
| Switch entity | `switch.py` | #7 |
| The base of the configuration entities (`GlowcastSettingEntity`: entity category, unique id and translation key from the setting's key, reading and storing through `settings.py`) | `entity.py` | #13 |
| Brightness and intensity; the update rate. Each platform also adds the entities the output type provides for it | `number.py`; `select.py` | #13 |
| The entertainment-area entity of the Hue output type, and reading its list | `outputs/hue_area_select.py` | #13 |
| Diagnostics (`async_get_config_entry_diagnostics`), read through `entry.runtime_data` and the entry: the versions (Glowcast and the manifest's requirements, installed), the settings, the source and output type with the model, manufacturer and software version of their devices (through `device_id` in `SOURCES` and `OUTPUTS`), whether it syncs, the last `PipelineStatus` (cleared when a start is refused because the TV or bridge is not set up) and the entry's active repair issues. Works while the entry is not loaded (no status then). Bug reports are public, so no stored value of the entry is included: only its shape (each key with the type of its value), so a key a later source or output type adds cannot leak, and generic code needs no list of each type's secret keys. Devices show only manufacturer, model and software version, never a name or serial number; `async_redact_data` (hosts, titles, serial numbers) is a backstop | `diagnostics.py` | #21 |
| Removing the entry: delete its repair issues (of every type) and run every output type's `async_removed` (an entry may have paired with a bridge before it switched to loose lights, #9). The Hue output type shows one persistent notification (`glowcast_bridge_app_left_<entry id>`) on how to remove Glowcast's app in the Hue app, in Home Assistant's server language, if the entry has a `bridge` block, whatever its output type; nothing on reload or restart, and nothing for an entry that never paired | `__init__.py`; `outputs/hue_entertainment.py` | #50, #66 |
| Pipeline behaviour (start order, sub-tasks, failure handling, no picture, the status and its phases, stop reasons); fading, hold on black and glow | `core/pipeline.py`; `core/light_state.py` | #5, #64, #65, #68, #14 |

Failure handling:
- **TV off:** `source.is_on()` false (after the source's own reconnect attempt), or failed captures in a row after the startup grace, at least 5 and at least 3 s after the start of the first of them: stop, release the lights, switch off. The 3 s (#110) keep a fast source (4 per second, where 5 failures take about 1 s) from stopping before a screen's own 3 s wait for no picture. The rule applies to the TV too (product owner's decision on #110): at Low the 5 failures take longer than 3 s anyway, but at Medium, High and Maximum captures that fail fast now stop about 1-2.8 s later than with 5 failures alone (Medium about 1 s, High about 1.7 s, Maximum about 2.8 s). Captures that time out after 5 s are unaffected, and a TV switched off with the remote still stops through `is_on()`.
- **A combined output** (#11, 3.4): a part that cannot start, is lost, or rejects its key is dropped while the rest keeps running; its retry lives in the composite. The runtime reacts to every new entry in the status's `dropped_parts` as it would to that stop reason for that type alone, but keeps syncing: reauth for `AUTH_FAILED` (with `glowcast_reauth_part` set to the type, so the reauth flow runs that type's reauth step), the type's repair issue for its stop issues, and the missing-entry issue for `OUTPUT_UNAVAILABLE` when its entry is missing. A part whose integration entry is missing at switch-on gets its repair issue and the rest starts; only when every part's entry is missing does Glowcast not start. Claiming the lights clears the earlier repair issues except those of the parts that are dropped. Once per part and reason; the composite logs the drops itself. No part left: stop with `OUTPUT_LOST` (or `AUTH_FAILED`), as for one output whose retry failed.
- **A notice** (`OutputType.notice` and `async_notice`, #198): a problem an output type works around while the lights follow the picture, so not a stop. The loose lights have one: a light that joined the entertainment area later (4.2); their notice reports what the last start left out (kept per area and loose-lights block), so the issue and the run never disagree. Each time the lights are claimed (the first `STREAMING` status of a pipeline) the runtime runs each part's `async_notice` and raises the notice's repair issue with the placeholders it returns, or removes it when it returns None, unless the pipeline stopped or the output block changed meanwhile. The claim's clean-up of earlier issues leaves notices to that check; a change of the output block or type and removing the entry remove them as any other output issue.
- **`OutputLost`** (stream dropped, bridge restarted, another app took over): one retry after 5 s if the bridge is free; otherwise stop, switch off, log why. Never fight for the area. **Recovery means: Glowcast stops cleanly and the next switch-on works.** A bridge reboot takes longer than the retry, so Glowcast stops; it does not wait for the bridge.
- **`OutputBusy` / `OutputUnavailable`:** do not start; switch off; log a clear message.
- **`OutputNotFound` / `OutputTooManyChannels`:** settings problems the user must fix: do not start, switch off, log, and raise the output type's repair issue (for the Hue output: "the entertainment area no longer exists; choose another one in Glowcast's settings" / "the entertainment area has more than 20 channels; choose or create a smaller area"). Cleared after the next successful start, or when another area is chosen (4.2).
- **`OutputAuthFailed`:** switch off and start reauth.
- **Referenced entry gone** (the `webostv` or `hue` entry with the stored unique id is removed or not loaded): checked before starting (and after a stop that may be caused by it); do not start, switch off, log clearly and raise the source or output type's repair issue explaining which integration to set up again. Cleared after the next successful start.
- **Every stop reason** turns the switch off and is logged once (info for `USER` and `TV_OFF`, warning for the rest). Changing what follows the TV while syncing restarts only the pipeline; that stop is not logged and the switch stays on. Other settings reach the running pipeline without a stop (4.2).
- **HA stops:** HA does not unload entries on shutdown. It first cancels all background tasks and then fires `EVENT_HOMEASSISTANT_STOP`, waiting for both (checked in HA's `core.py`, 2025.7 and 2026.10). Cancelling the pipeline's task makes it release the lights (its shielded cleanup, 3.5); a listener on `EVENT_HOMEASSISTANT_STOP` also stops it and waits for the release, as a backstop. The log says "Home Assistant is stopping". After the restart Glowcast continues when it synced at the stop and the picture source is on (4.3, #183).
- **No picture** (all-zero frames for 7 s, 3.5): release the lights; the switch stays on and nothing is logged as a stop. Claiming them again when the picture returns can fail like a start: then stop with the same reason and the same reaction as at switch-on (switch off, repair issue or reauth); never take the lights from another app.
- **HA crashed while streaming:** the next start finds Glowcast's own stale stream, stops it and starts normally (3.4).

## 5. Configuration data model - built for growth

Config entry `data` and `options` store source and output as **typed blocks**, even though v0.1 has only one type of each. Other integrations' entries are referenced by **unique id**, never by entry id, so removing and re-adding the TV or bridge in HA does not break Glowcast.

```python
data = {        # fixed at setup: what Glowcast is connected to, and secrets
    "source": {"type": "webos", "unique_id": "<webostv entry unique id>"},
    "bridge": {"unique_id": "<hue entry unique id>", "app_key": "...", "client_key": "..."},
}
options = {     # everything the user can change in the settings screen
    "output": {"type": "hue_entertainment", "area_id": "..."},
    "brightness": 80, "intensity": 100, "update_rate": "high",
}
```

`bridge` is present only once a Hue bridge has been paired: at setup with an entertainment area, or later from the settings when the user switches to one. It is kept when the user switches to another output type, so switching back does not pair again. Only the Hue output type reads it.

Loose lights (#9) store their output block as:

```python
"output": {
    "type": "ha_lights",
    "follow": "lights",            # or "area" or "group"
    "area_id": "<area id>",        # only with "area"
    "group": "<entity registry id of the light group>",  # only with "group"
    "lights": [{"entity": "<entity registry id>", "x": -1.0, "z": 0.0}, ...],
}
```

`lights` holds a position for every light placed so far, also for an area or a group; a light without an entry follows ambient (0, 0). With `follow: "lights"` it is also the list of chosen lights. Saving the settings keeps the positions of the lights the area or group has at that moment only.

A screen source (#110) stores the helper and Glowcast's token in its block: `"source": {"type": "screen", "helper_id": "<helper id>", "host": "...", "port": 8488, "token": "..."}`. The helper id is the entry's unique id and stays when the address changes; discovery keeps `host` and `port` current (4.1, #109).

An entertainment area and loose lights together (#11) are one combined block, a new output type, so no migration (v0.1 and v0.2 entries keep their single block):

```python
"output": {
    "type": "combined",
    "parts": [
        {"type": "hue_entertainment", "area_id": "..."},
        {"type": "ha_lights", "follow": "lights", "lights": [...]},
    ],
}
```

At most one part per type. Each part's `create` and `is_missing` get the options with their own block as `options["output"]` and the whole combination as `options["combination"]` (`part_options` in `outputs/blocks.py`, never stored), so a type never reads another type's keys; its flows and entities find and replace their block with `outputs/blocks.py`. A type may hand another type's block (`other_block`) to that type's own code only: the loose lights hand the area's block to the Hue type's lookup, in the settings and at every start (#198). The `bridge` block stays in `data` as for an area alone.

The selectable output (type and its settings) lives entirely in `options`, so a later output type (loose lights, #9) can be chosen in the settings screen without a migration. Adapters are looked up in two small registries:

```python
SOURCES = {"webos": SourceType(create, is_missing, device_identifier, device_id,  # sources/__init__.py
                               steps, setup_step, reauth_step, missing),
           "screen": SourceType(create, is_missing, device_identifier, device_id,
                                steps, setup_step, reauth_step)}
OUTPUTS = {"hue_entertainment": OutputType(create, is_missing, device_id, entities,  # outputs/__init__.py
                                           steps, setup_step, reauth_step,
                                           options_steps, options_step, entity_keys,
                                           missing, stop_issues, async_removed),
           "ha_lights": OutputType(create, is_missing, device_id, entities,
                                   steps, setup_step, options_steps, options_step,
                                   entity_keys, stop_issues),
           "combined": OutputType(create, is_missing, device_id,   # a part per block (#11)
                                  options_steps, options_step)}
```

Each adapter module provides these functions itself, so the runtime, the flows and the entities never read a source's or output's keys: `create(hass, data, options)` builds the adapter, `is_missing(hass, data, options)` says whether the referenced `webostv` or `hue` entry is removed or not loaded (4.4; it gets the options too, because loose lights keep their lights there), the source's `device_identifier(data)` names the TV's device for `via_device` (4.3), and `device_id(hass, data)` finds the device of the TV or the bridge in Home Assistant's device registry (for `via_device_id` and diagnostics): both types pick their own device from the referenced entry's devices (`entry_devices()` in `devices.py`, which knows no integration). A source may have no device: then both return `None`, Glowcast's device is not linked and diagnostics show no device. The type's steps and options steps are described in 4.1 and 4.2; `entity_keys` names its own entities, so they are removed when the entry changes to another type (4.2); `missing` and `stop_issues` are its repair issues (4.4), and `async_removed` runs when the entry is removed.

The `bridge` block in `data` belongs to the Hue output type: only `outputs/` reads or writes it (setup, reauth, the area entity, the removal notice). An output type can also provide its own entities on Glowcast's device, for what follows the TV (4.3): `entities` maps a platform to a factory `(hass, entry) -> list[Entity]`, and is optional (empty by default), so loose lights (#9) add their own without changing the generic code. Only platforms whose generic module asks for them (`number`, `select`) can be used; a type that needs another platform adds that module to `PLATFORMS` with the same one-line call.

Config entry `VERSION = 1`. The versioning rule:
- **New types need no migration.** A new source or output type is a new value of `type` in its block (D5, D16).
- **New keys get defaults on read.** A key added by a later version is missing in older entries; `settings.py` reads every setting with its default from `const.py`, so an older entry keeps working. A stored update rate that no longer exists also gets the default, with a warning in the log.
- **`MINOR_VERSION` with `async_migrate_entry` only for a change to the shape of stored data** (a key moved or renamed, a block split). A version bump without a migration function stops existing entries from loading.

## 6. Repository layout

```
custom_components/glowcast/
  __init__.py              # entry setup, unload and removal (with the removal notice)
  manifest.json  const.py
  config_flow.py           # setup, options, reauth: chains the types' steps
  flow.py                  # what the types' steps share with the flows
  devices.py               # what the types share to find their devices
  issues.py                # Issue: a type's repair issue and its log message
  runtime.py               # GlowcastData: runs the pipeline, reacts to stop reasons, repair issues
  settings.py              # options <-> Settings, with defaults for missing keys
  entity.py                # base entities: the device, the runtime's listener, a setting
  number.py  select.py     # the settings as configuration entities, plus the output type's own
  diagnostics.py           # the diagnostics download, redacted
  sensor.py                # the status and the capture rate
  switch.py
  translations/en.json  translations/nl.json  icons.json
  brand/                   # icon and logo PNGs (HA 2026.3+), made from docs/brand/
  core/                    # NO homeassistant imports
    models.py  ports.py  color_engine.py  light_state.py  pipeline.py
  sources/webos.py  sources/webos_flow.py        # the webOS source and its setup step
  sources/screen.py  sources/screen_flow.py      # the screen source, its discovery, setup and reauth steps
  outputs/hue_entertainment.py                  # the Hue output, its issues and removal notice
  outputs/hue_entertainment_flow.py             # its setup, reauth and options steps
  outputs/hue_area_select.py  # the Hue output type's entertainment-area entity
  outputs/ha_lights.py  outputs/ha_lights_schedule.py   # loose lights (#9) and their rate limits
  outputs/ha_lights_flow.py  outputs/ha_lights_select.py  # their steps and their Lights entity
  outputs/composite.py  outputs/composite_flow.py  # both together (#11): the output and its settings steps
  outputs/blocks.py                              # a type's block, alone or in a combined one
  outputs/colors.py  outputs/labels.py           # shared by the types: a light's colour in 0-1, option labels (#199)
tests/
  fakes.py                 # fake clock, source, output and pipeline, shared by the tests (no HA imports)
  conftest.py              # the fixtures that put the fakes into HA
  test_color_engine.py
  test_light_state.py      # fading, hold on black, brightness and glow, with the time as an argument
  test_pipeline.py         # the pipeline with the fakes and a fake clock
  test_init.py             # the switch and the lifecycle in HA; also through the real pipeline
  test_config_flow.py      # setup, options and reauth, including error cases
  test_settings.py         # reading and writing the settings
  test_entities.py         # the configuration entities, live settings and the area select
  test_sensor.py           # the status and the capture rate
  test_diagnostics.py      # the download, and that it has no secrets or names
  test_generic_code.py     # the generic modules import and name no source or output type
  test_webos_source.py  test_hue_output.py  test_manifest.py
  test_ha_lights_schedule.py  test_ha_lights_output.py  test_ha_lights_flow.py  test_ha_lights_sync.py
  test_composite_output.py  test_combined.py   # the combined output with fakes; in Home Assistant
.github/
  workflows/ci.yml         # hassfest, HACS validation, pytest, ruff
  workflows/website.yml    # builds the website, checks links, publishes it from main (#167)
  ISSUE_TEMPLATE/  pull_request_template.md
screen_helper/             # the screen helper (#108, 8.3): its own package, tests and lock file,
                           #   not installed by HACS, not imported by the integration
scripts/                   # developer tools, not installed (render_brand.py builds brand/;
                           #   demo.py + demo/: a local HA with stubs for review GIFs)
docs/ARCHITECTURE.md
docs/brand/                # icon and logo sources (SVG), colours, font
website/                   # the user documentation and landing page (MkDocs Material, #167),
                           #   published at https://nicohabets.github.io/glowcast/
hacs.json  README.md  LICENSE  CLAUDE.md
```

`manifest.json` (keys ordered `domain`, `name`, then alphabetically): `after_dependencies: ["hue"]` (Hue is optional since loose lights, #9; set up first when it is there; Glowcast does not import the `hue` integration), `codeowners`, `config_flow: true`, `dependencies: ["webostv"]`, `documentation`, `integration_type: "service"`, `iot_class: "local_polling"`, `issue_tracker`, `requirements: ["hue-entertainment==0.1.2"]`, `version`. Pillow ships with Home Assistant: do not list or pin it. Do not add `aiowebostv` to `loggers` (its debug logs print raw websocket traffic, including image URLs).

Translations live in `translations/en.json` and `translations/nl.json` (custom integrations do not use `strings.json` at runtime). Dutch shows the levels as Laag / Middel / Hoog / Maximaal.

## 7. Testing

- **ColorEngine:** synthetic frames (solid colours, left/right split, letterbox bars, all black), plus the performance budget.
- **Light state:** plain unit tests with the time as an argument: the first target shown at once, the linear fade over the measured interval, the hold on sudden black, brightness and the glow formula, and the channels kept after a retry.
- **Pipeline:** fake source, fake output and a fake clock: start order (TV off → lights never touched), startup grace with retries, sends keep their rhythm while a capture hangs, fading over the measured interval, brightness and glow formula (end to end), every stop reason, and that `stop()` and `close()` run on every exit path.
- **Config flow:** every setup, options and reauth path, including the error cases (built with #6).
- **Soak test:** two hours of real viewing; no crash, no memory growth; after a TV standby, a bridge restart and an HA restart Glowcast has stopped cleanly and the next switch-on works.
- **Manual end-to-end:** an LG webOS TV, a Hue bridge and an entertainment area with a gradient lightstrip.

## 8. Room to grow (not built in v0.1)

### 8.1 Loose lights with a position

Built in #9 as a new **output adapter** (`HaLightsOutput`, 3.4) that drives any HA light through `light.turn_on` (Hue and other brands). For #9 nothing in `core/` changed, because:

- The ColorEngine works on **channels with a position**, not on lights or areas.
- A loose light becomes a channel by giving it a position: `{"entity": "<entity registry id>", "x": -1.0, "z": 0.0}`. The setup and settings offer presets (left, right, top, bottom, ambient) that fill in `x`/`z`, and fine-tuning.
- It is stored as a new output type: `"output": {"type": "ha_lights", ...}` (section 5).
- A mix (entertainment area plus a few loose lights) is a `CompositeOutput` that fans out to several outputs (#11, built; 3.4, 4.4, 5). It stays generic: it knows the port, not Hue or Home Assistant lights; the Hue and loose-lights types still provide their own steps, entities and texts, and the check that a light is not in both lives in the loose-lights type (which may know Hue, being in `outputs/`). #11 did change `core/`: `core/ports.py` got `LightOutput.dropped_parts` and `OutputGone`, and `core/pipeline.py` reports the dropped parts in its status (3.4, 3.5). #153 changed it again for the fade-in: `Channel.start_rgb` in `core/models.py`, `start(retry=...)` in `core/ports.py`, and the fade in `core/light_state.py` and `core/pipeline.py` (3.5).

Rule: **no code outside `outputs/` may know about Hue**, and that includes the UI. The Hue output type provides its own setup and options steps (bridge, link button, pairing, area), its "what follows the TV" entity and its texts (stop messages, repair issues, the removal notice). The setup and options flows only chain what the types provide.

### 8.2 Other TVs

Planned as new **source adapters** behind `FrameSource`. Candidates, each still to be researched:

| Source | How it could capture | Known limits |
|---|---|---|
| Android TV / Google TV | ADB `screencap` | slow (about 1 per second), developer mode needed |
| Any HA camera entity | camera snapshot (HDMI capture card via go2rtc, or a camera aimed at the screen) | works for every TV, needs hardware |
| Samsung (Tizen) | no known local screenshot API | probably not possible |
| A PC or Mac screen | a screen helper on the computer serves 64x36 frames; see 8.3 | needs a helper installed on the computer |

Rule: **no code outside `sources/` may know about webOS**, and that includes the UI: the webOS source type provides its own setup step (pick a TV) and its texts. Each source decides itself whether its TV is on (`FrameSource.is_on()`), so the pipeline never needs a TV-specific entity.

### 8.3 A computer screen (feature #107)

A PC or Mac screen becomes a picture source through a small **screen helper** on that computer (spike #106, D18). Glowcast keeps running in Home Assistant and **pulls** frames from the helper, as it pulls them from the TV, so the switch, automations and settings work unchanged.

- **Helper** (#108, built): its own package `screen_helper/` outside `custom_components/` (not installed by HACS, not imported by the integration), with its own `pyproject.toml`, `uv.lock`, tests and CI job. Captures one screen with `mss` only when a frame is asked for (one grab per request, shared by requests within 50 ms, on one thread), shrinks it with Pillow (`reduce`, then a box resize; the whole screen stretched to 64x36, not cropped) and serves only the latest frame; no JPEG. Started with one `uv` command from the repository (`uv run --project screen_helper glowcast-screen`); an installable app later (#113). `--pattern <name>` serves synthetic frames without a display, for agents and automated tests (#112, #134); `--non-interactive`, `--pairing-code`, `--port` and `--json` (one JSON line per event) run it without a person. The flags, events, exit codes and the exact pixels of every pattern are in [`screen_helper/README.md`](../screen_helper/README.md).
- **Protocol** (version 1, plain HTTP on the LAN, like the TV's image download; default port 8488):
  - mDNS service `_glowcast._tcp.local.`, instance name `<name> (<first 6 characters of the id>)`, TXT record `id=<helper id>`, `name=<display name>`, `protocol=1`, with one address: the one on the computer's default route (virtual adapters such as Hyper-V, WSL or Docker are left out; all addresses only without a default route). Not announced when the helper listens on a loopback address.
  - The helper id is a random 32-character hex id, made on the first start and stored with the pairings in the user's config directory; it stays across restarts and `--unpair`.
  - `POST /v1/pair` with `{"code": "<6 digits>"}` (the code the helper shows) returns `200` `{"token": ..., "id": ...}`; a wrong code `403` `{"error": "wrong_code"}` after a 1 s delay, one attempt at a time. A new code on every start; tokens survive restarts until `--unpair`. Every other request needs `Authorization: Bearer <token>`, else `401` `{"error": "unauthorized"}`.
  - `GET /v1/frame` returns `200` with 6,912 bytes (`application/octet-stream`, 64x36 RGB, one byte per channel, row-major from the top left), or **`204` with no body for "no picture"**: the screen is locked, capture fails, or a grab takes longer than 1 s. A black picture is a picture (`200`), never "no picture". A client tells the cases apart by status alone: `200` a frame, `204` no picture, `401` pair again, anything else or no answer within its timeout unavailable.
  - `GET /v1/info` returns JSON: `id`, `name`, `protocol`, `version` (of the helper), `width`, `height`, `screens` (`index`, `width`, `height`, `left`, `top`, `primary`; empty for a pattern), `screen` (the index captured, or `null`), `pattern` (`null`, or `name`, `showing`, `since` in UTC and, for `cycle`, `interval`) and `no_picture` (`null`, or the reason of the last "no picture": `capture_failed`, `capture_timeout`, `screen_missing`). The helper lists the screens again every 5 s while frames are pulled, so a changed or re-plugged monitor is captured as it is now, and a chosen screen that is gone is "no picture".
  - Run flags for tests and parallel runs (#112, #134): `--host` (the address to listen on; a loopback address is not announced) and `--config-dir` (a separate id and pairings, so a test never touches the user's own).
- **`ScreenFrameSource`** (#110, built; 3.4): source type `"screen"` behind `FrameSource`; the same update-rate levels as the TV, with Maximum capped at 4 frames per second (product owner's decision); builds `Frame` directly (no JPEG decoding); `is_on()` false once captures have failed (no picture or no answer) for 3 s in a row after a frame, counted from the start of the first failed one (product owner's decision); 401 stops with `SOURCE_AUTH_FAILED` and starts the source's reauth. The entry stores the helper id, address and token in `data` (Glowcast's own secret, like the Hue keys).
- **Setup** (#109, built; 4.1): Home Assistant's zeroconf discovery (`"zeroconf": ["_glowcast._tcp.local."]` in the manifest) shows the helper under Discovered as "Glowcast - <name>"; or the user chooses Computer screen and types the address and port. The screen source type provides those steps, the pairing-code step and the reauth step (a new pairing code). Unique id = the helper id, so a new address updates the entry.

## 9. Decisions

| # | Decision | Why |
|---|---|---|
| D1 | HACS integration, not an add-on | Runs inside HA, reuses the TV pairing and Hue setup, easy to share |
| D2 | v0.1 = entertainment area only | Lights and their places are chosen in the Hue app; no light picker to build; gradient and 3D placement for free |
| D3 | One switch entity, start via automations | HA already has the tools for "when"; Glowcast only does "how" |
| D4 | Channels with a position as the common language | Loose lights and other outputs plug in without touching the ColorEngine |
| D5 | Typed source/output blocks in the config entry | New TV or light types without a data migration |
| D6 | `hue-entertainment` library | Pure-Python DTLS, no C build problems on HA OS |
| D7 | Read TV credentials from the `webostv` entry at runtime | No duplicate secrets, survives a re-pair |
| D8 | Update rate as levels, fixed 25 Hz fade | Simple choice for the user; smooth lights at every level |
| D9 | Own Hue pairing next to HA's | HA's Hue entry has no `clientkey`, which streaming needs and which cannot be retrieved later |
| D10 | Refuse when another app streams | Never take lights away from something the user started elsewhere |
| D11 | Minimal glow on a black picture: a dim warm white, about 2700K, added only to channels darker than the glow level (#43) | The room does not go fully dark between scenes or on black screens, and a warm white feels cosy where a neutral one looks grey; bright colours are not tinted by it |
| D12 | Linear position mapping in v0.1 | Simple and predictable; stretching TV-strip channels to the screen edges is a later improvement |
| D13 | Pipeline in `core/` with injected runner and clock | Testable with plain fakes, no Home Assistant test framework needed |
| D14 | Reference other integrations by unique id | Survives removing and re-adding the TV or bridge in HA |
| D15 | Recovery = stop cleanly, next start works | Predictable; no long waits or fights with the bridge |
| D16 | Selectable output lives in `options` | Output types can be switched in the settings screen without migrations |
| D17 | Minimum Home Assistant 2025.7.2 | First release with aiowebostv 0.7.4, which current LG firmware needs (0.7 API features are included) |
| D18 | A computer screen is a source through a helper that Glowcast pulls frames from (#106) | Home Assistant stays in charge (switch, automations), no Home Assistant token on the computer, the existing `FrameSource` port fits, and the same helper serves test patterns for automated tests; a browser page needs HTTPS and a new screen choice every session, a camera stream is heavier and slower |
| D19 | Loose lights: fixed rate limits, 1 command per second per light and at most 4 per second per Hue bridge or Thread network (#9, #125) | Home Assistant's reported state cannot show whether a light keeps up, so Glowcast cannot slow down by itself; above about 8-10 commands per second a Hue bridge builds a backlog and other lights in the house stop responding |
| D20 | A change of output type reloads the entry (#9) | The type's entities change with it; a change within the type stays a pipeline restart |
| D21 | An area and loose lights together are one combined output block with a part per type, whose parts are dropped and retried on their own (#11) | One entry and one switch for "my TV lighting"; a new type value needs no migration; a failing part never stops the rest, and the pipeline does not retry on top of the parts' own retry |
| D22 | After an HA restart Glowcast continues only when it synced at the stop and the picture source is on within 3 minutes (#183) | The lights do not silently stop after an update; a TV that was turned off meanwhile is left alone (the automation starts it later); no setting for it |
