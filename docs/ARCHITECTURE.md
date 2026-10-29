# Glowcast - Architecture

Status: **v0.1 released**. This document is the source of truth for the first build. Features after v0.1 are tracked as GitHub issues (milestones v0.2, v0.3 and v99 Later), not here; this document only describes how the design leaves room for them (section 8).

## 1. Goal

Make Hue lights follow the picture on an LG webOS TV, from inside Home Assistant, with no cloud, no subscription and no extra hardware.

### v0.1 scope - the minimum

Glowcast only translates picture into colour into light. Everything Home Assistant or the Hue app already does, stays there.

- **Source:** an LG webOS TV that is already set up in HA through the core `webostv` integration.
- **Output:** one Hue **entertainment area** (for example the living room). Which lights take part, and where they stand, is set up in the Hue app. Gradient lightstrips work per segment.
- **Control:** one switch entity. Starting is done by the user, a dashboard or an **automation** (for example "TV turns on → switch on"). Glowcast never starts by itself.
- **Settings screen:** the integration's options (Settings → Devices & services → Glowcast → Configure): entertainment area, brightness, intensity, update rate. The TV and bridge are chosen once, during setup.

### Not in v0.1

Loose lights, other TVs, extra entities, effects and so on are GitHub issues in milestones v0.2, v0.3 and v99 Later. Build one only when its issue is in Todo, and never build placeholders for them. Section 8 shows where they will plug in.

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
| DRM app returns black frames (#5) | Prime Video in the TV's own app: every capture is completely black (all pixels 0), at about 6.9 captures per second because the JPEG is tiny. The pipeline then shows the minimal glow (at the time a dim neutral white; a warm white since #38). No workaround in v0.1; telling the user is #14. |
| HDMI streamer with a DRM app (#5) | Prime Video on a streaming stick over HDMI: captures show the real picture (not black). Letterbox bars in that film were pure black (0-7 of 255) and changed width between scenes. |
| `Pipeline` on real hardware (#5) | Real source and output, update rate High, brightness 80 %: first colour 4.1-4.3 s after start (mostly the Hue handshake), 23.9 sends and 2.87-2.90 captures per second over 27 s, no failed captures. With the TV off: `TV_OFF` after 10.0 s, no area touched. TV switched off with the remote during a run: `TV_OFF` through `is_on()` before any capture failed, area released. A Hue app sync running: `OUTPUT_BUSY` 0.9 s after start (after the TV came up), no sends, the sync kept running. |
| Black before "off" (#39) | Switching the TV off with the remote: the TV first shows an almost black switch-off animation (1-2 % of the pixels lit, some nearly white) for 2.7-3.0 s, then `is_on()` turns false (4 tries with whole-frame black: 2.99, 2.84, 2.87, 2.72 s; the last frame with a picture 2.96-3.24 s before). No capture failed in between. A first measurement that counted a frame as black only when every channel region was black found 0.3-0.6 s, because the animation's bright element kept one region lit. `output.stop()` then takes about 1.2 s before the bridge restores the lights. |
| The switch on Home Assistant (#7) | Home Assistant 2026.10.4. `switch.turn_on` to the entertainment area active on the bridge: 2.1 s, then the DTLS handshake (3.5-4.4 s, #4), so the first colour comes after about 5.5-6.5 s. `switch.turn_off` to the area inactive: about 2.1 s (`output.stop()` itself about 1.2 s). TV switched off with the remote while syncing: the switch was off 1.2 s after the TV's `media_player` turned `off`. Switched on with the TV in standby: off again after about 10.5 s (startup grace), area untouched. A Hue app sync running: `output_busy` after about 2 s, the sync kept running. HA restarted while syncing: area inactive 1.8 s after the restart command, long before HA stopped; the switch was off after the restart. Bridge key removed in the Hue app: `auth_failed` and reauth; after pairing again the next switch-on worked with all settings kept. The README automation (TV `off` to `on`) started Glowcast 2 ms after the TV turned on. |
| TV in standby (#7) | With the TV in standby the `webostv` entry stays loaded and its `media_player` reads `off` for a few minutes, then `unavailable`; switching the TV on always passes `off` before `on`. The TV accepts the websocket and closes it at once with code 1008, which aiohttp reports as `WSMessageTypeError`, a `TypeError`: the source treats it as "not reachable". |
| Soak test (#19, 2026-10-29) | Glowcast v0.1.0 installed through HACS on Home Assistant 2026.10.4, update rate Maximum, about 95 minutes of syncing on HDMI sources (a streaming box and a game console): no crash, no errors, no stop on its own. HA core memory went from 1154 to about 1195 MB in the first half hour, then stayed between 1188 and 1238 MB, mostly 1190-1210 MB, for the last hour (no visible growth; too short to rule out a slow leak). TV standby: switch off 1.20 s after the TV's `media_player` turned `off`; in standby the `media_player` turned `unavailable` after 2 min 11 s, and switching on with the remote 10 min later went `unavailable` → `off` → `on` within 0.5 ms, so the README automation started Glowcast (2.3 ms). Bridge power cut while syncing: noticed after about 11 s, one retry, stopped with `OUTPUT_LOST` about 38 s after the bridge went silent, 4 warnings and no errors; the next switch-on worked (area active 0.9 s later). The lights stayed on the last streamed colour after the power cut, while the bridge at first reported them as off; the next start and stop of Glowcast restored them. Accepted as is by the product owner (#91). HA restart while syncing: area inactive 1.27 s after the restart command, switch off afterwards, the next switch-on worked. |
| Not yet tested | Other DRM apps (Netflix etc.) and HDCP-protected HDMI sources may also return black frames. Whether each segment of a gradient TV strip shows its own colour on the lights (unit-tested in #4; the test strip has loose contacts, #94). `executeOneShot` is undocumented and could disappear in a firmware update. |

## 3. Architecture: ports and adapters

A pipeline with three stages. The two stages that touch the outside world sit behind a small interface (port). v0.1 has exactly one adapter per port.

```
                 ┌──────────────────────── Pipeline (one asyncio task) ─────────────────────────┐
 LG TV  ──►  FrameSource  ──►  Frame  ──►  ColorEngine  ──►  list[ChannelColor]  ──►  LightOutput  ──►  Hue Bridge
             (port)                        (pure logic)                               (port)
             └ WebOsFrameSource                                                       └ HueEntertainmentOutput
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

@dataclass(frozen=True)
class ChannelColor:
    channel_id: str
    rgb: tuple[float, float, float]   # 0.0 - 1.0
```

**A channel with a position is the common language** between outputs and the ColorEngine. The ColorEngine never knows where a position came from. That is what makes loose lights possible later (section 8.1). The bridge also gives a front/back value (`y`); v0.1 ignores it.

### 3.2 Ports (`core/ports.py`)

```python
class FrameSource(Protocol):
    async def start(self) -> None: ...             # connect; may take a few seconds; raises SourceUnavailable
    def is_on(self) -> bool: ...                   # "TV is on and showing a picture"; false only after the
                                                   # source's own reconnect attempt has failed
    async def capture(self) -> Frame: ...          # raises SourceUnavailable (off/unreachable) within 5 s
    async def close(self) -> None: ...

class LightOutput(Protocol):
    max_rate_hz: float                             # how often send() may be called
    async def start(self) -> list[Channel]: ...    # claims the lights (at most 20 channels); raises OutputBusy /
                                                   # OutputAuthFailed / OutputNotFound / OutputTooManyChannels / OutputUnavailable
    def send(self, colors: list[ChannelColor]) -> None: ...   # non-blocking
    async def check(self) -> None: ...             # raises OutputLost (gone / taken over) or OutputAuthFailed
    async def stop(self) -> None: ...              # releases the lights; safe to call twice
    async def close(self) -> None: ...             # releases network resources; after stop()
```

Brightness and the minimal glow are applied in `core/` (the light state, 3.5), so outputs stay dumb.

### 3.3 ColorEngine (`core/color_engine.py`)

Pure and synchronous. `compute(frame, channels, intensity) -> list[ChannelColor]`

For each channel:
1. **Region:** map `x` to a horizontal band and `z` to a vertical band of the frame, linearly from -1..1 to the frame edges (band size: a constant, start with 30 %).
2. **Average** the pixels in the region, skipping near-black pixels (letterbox bars).
3. **Black picture:** if almost the whole region is black, return black; the pipeline turns that into the minimal glow (3.5).
4. **Intensity:** scale saturation (0 = nearly white, 1 = as on screen, up to 2 = boosted).

**Known limitation (decision D12):** a TV gradient strip's channels sit close to the centre (test area: x and z between -0.4 and 0.4), so with the linear mapping they sample about 30 % in from the edges, not the edges themselves. v0.1 accepts this; stretching channels near the TV to the full screen width is issue #23.

`is_black(frame)` applies the same black level and 5 % share to the whole frame; the pipeline uses it for the hold on sudden black (3.5).

**Performance:** 16 channels on a 64x36 frame in under 20 ms on a GitHub Actions runner (asserted in a test). Target hardware is a Raspberry Pi 4 class CPU.

### 3.4 Adapters (v0.1)

**`WebOsFrameSource`** (`sources/webos.py`)
- Resolves the `webostv` config entry **by its unique id** at runtime and reads `host` and `client_secret` from it. Never copies them.
- `aiowebostv.WebOsClient(host, client_key=..., client_session=async_get_clientsession(hass))`.
- `is_on()` uses the client's own `tv_state.is_on` and `is_screen_on` (power-state subscription), so the pipeline never needs to know which `media_player` belongs to the TV.
- `capture()`: `tv/executeOneShot`, then download `imageUri` with HA's session and `ssl=False`; wrapped in `asyncio.timeout(5)` (aiowebostv's own timeout is 20 s).
- Decodes with Pillow in the executor; use `Image.draft()` for a fast JPEG downscale to about 64x36.
- **The source owns reconnecting.** aiowebostv clears its state on every websocket drop, so a raw `is_on` would read "off" during a hiccup. The source reconnects once; `is_on()` reports off only when that reconnect fails or the TV's power state says off.

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

### 3.5 Pipeline (`core/pipeline.py`)

One supervisor task per config entry, started and stopped by the switch. Not a `DataUpdateCoordinator`. The blocking-call runner and the clock are injected, and the pipeline reports why it stopped through an injected callback (`on_stopped(reason)`), so the Home Assistant side can turn the switch off or start reauth.

```
start:    try source.start() and one good capture, retrying, for up to 10 s (startup grace)
          → if not: stop with reason TV_OFF; the lights were never touched
          channels = output.start()
run:      two concurrent sub-tasks under the supervisor:
            capture loop: every 1/update_rate s: frame = source.capture() → target = engine.compute(...)
                          → light_state.set_target(target, frame black?, now)
            send loop:    every 1/25 s: output.send(light_state.colors(now, brightness));
                          every 5 s: output.check()
          the send loop keeps its 1/25 s rhythm even while a capture hangs (up to its 5 s timeout)
stop:     cancel both sub-tasks → output.stop() → output.close() → source.close()   (on every exit path)
```

**The pipeline only supervises** (start order, startup grace, the two loops, the `OutputLost` retry, stop handling and cleanup). What the lights show between frames is kept by **`LightState`** (`core/light_state.py`): pure and synchronous, with the time as an argument, so it is tested without the pipeline. It fades from the current colours to each new target over the measured capture interval (in engine colour space), holds the last colours on sudden black, and applies brightness and the minimal glow when the send loop asks for the colours. After a retry the pipeline tells it which channels still exist; the gap is not used as a capture interval. Rule for later: memory about colours or the picture (for example letterbox bars, #34) lives in small helpers like this next to the colour engine, not in the supervisor.

- **Brightness:** `c = c_engine * brightness` per channel, in 0-1 RGB.
- **Minimal glow:** then `out = c + max(0, 1 - m / g) × g × warm` per channel, with `m` the brightest component of `c`, `g = min(0.10, brightness)` and `warm = (1.0, 0.78, 0.50)`. On black this is the whole glow, `g × warm`: a dim warm white of about 2700K on the lights at 10 %. The glow is for dark channels: it gets smaller as the channel gets brighter and is gone once the brightest component reaches `g`, so bright, saturated colours stay pure, and the colour changes continuously during a fade (#43). While the glow is added, no component goes above `g`, so the result is never brighter than the brightness setting. Constants, not options. Examples: a black picture at 80 % gives (0.10, 0.078, 0.050); brightness 40 % and a red picture give (0.40, 0, 0); brightness 20 % and a blue picture give (0, 0, 0.20); brightness 50 % and an engine colour (0.10, 0, 0), so `c` = (0.05, 0, 0), give (0.10, 0.039, 0.025); brightness 3 % and black give (0.03, 0.0234, 0.015). Until #43 the glow was a floor per RGB component, `max(c, g × warm)`, which mixed warm white into every colour (a blue at 20 % became lilac). The level and the colour were chosen on real hardware (#38): the warm ratio gives less light than a neutral glow at the same level, so the level is 10 % instead of the earlier 5 %; and the bridge renders sRGB 2700K (about 1.00 : 0.65 : 0.35) too red, especially in a dark room, so the ratio is two steps cooler in sRGB.
- **Hold on sudden black:** when the whole frame turns (almost) black after a picture, keep the last colours for 3.5 s before fading to the glow. "Whole frame black" uses the colour engine's black level and 5 % share over the whole frame (`is_black()` in 3.3's module), so a small bright element such as a TV's switch-off animation does not count as a picture. A TV being switched off shows such an animation for up to 3.0 s before it reports off (see "Verified facts"), so the lights go straight from the picture to their previous state; a genuinely black scene reaches the glow 3.5 s later. Starting on black shows the glow at once. The hold only changes colours, never when the pipeline stops. A constant, not an option (#39).
- **Stop reasons:** `USER`, `TV_OFF`, `OUTPUT_LOST`, `OUTPUT_BUSY`, `OUTPUT_NOT_FOUND`, `OUTPUT_TOO_MANY_CHANNELS`, `OUTPUT_UNAVAILABLE`, `AUTH_FAILED`, `SOURCE_UNAVAILABLE`.

### 3.6 Update rate

| Level | Captures per second | Use |
|---|---|---|
| Low | 1 | calm background light, lowest load on the TV |
| Medium | 2 | |
| High (default) | 3 | |
| Maximum | as fast as the TV allows (about 4 on a 2023 G3) | |

The TV cannot go faster than about 4 per second, so there is no level above Maximum. The lights are always updated 25 times per second by fading, whatever the level.

## 4. Home Assistant integration

### 4.1 Config flow (first setup)

1. **TV:** pick a loaded `webostv` config entry that has no Glowcast entry yet.
2. **Hue bridge:** pick a loaded `hue` config entry with `api_version == 2` (older bridges cannot stream).
   - First check that the bridge answers (unauthenticated `GET https://<host>/api/config`, `ssl=False`); if not → "could not reach the bridge". (`pair()` raises the same `TimeoutError` for every failure, so reachability must be checked first.)
   - Then a form "press the link button on your bridge, then click Submit", followed by a **progress step** running `HueEntertainmentAPI(host).pair(device_type=...)` (up to 30 s). The returned `username` is stored as `app_key`, `clientkey` as `client_key`. Timeout → "button not pressed, try again", unless the bridge no longer answers (checked again, because the library retries network errors until its deadline and then raises the same timeout) → "could not reach the bridge". Always call `api.close()` afterwards; closing the flow cancels the pairing.
   - `device_type` is `glowcast#<name>`, truncated to the bridge's limit (20 characters before `#`, 19 after).
3. **Entertainment area:** pick from the bridge's list, read with one raw `GET /clip/v2/resource/entertainment_configuration` (HA's session, `ssl=False`), here and in the options flow; the library's area listing also fetches every device and light. If there are none, or the list cannot be read, keep the screen open with a message (for none: how to create one in the Hue app); Submit reads the list again, so the user does not pair a second time (which would add another app on the bridge).

Rules:
- **One Glowcast entry per TV:** the entry's unique id is the `webostv` entry's unique id; adding the same TV twice aborts.
- **Two TVs on one bridge** are allowed, but only one can stream at a time: the second one gets "another app is streaming".
- **Reauth:** when the pipeline stops with `AUTH_FAILED`, the HA side calls `entry.async_start_reauth(hass)`. HA then shows a re-authentication notification; the user presses the bridge button again and the new keys are stored with all settings kept. Opening the settings screen with a rejected key also starts reauth (instead of showing the area list).
- **Security:** Glowcast's `app_key` is a full bridge user, not only a streaming key. Redact it in diagnostics. The bridge has no API to remove an app, so removing Glowcast's entry leaves the app on the bridge. When the entry is removed, `async_remove_entry` shows a persistent notification with the steps in the Hue app (the same as in the README).
- **Why a separate Hue pairing:** HA's `hue` config entry stores only an `api_key`. Streaming needs a `clientkey` (the DTLS pre-shared key), which the bridge hands out only when an app pairs with `generateclientkey`. It cannot be read back later, so Glowcast pairs once itself.

### 4.2 Options (the settings screen)

| Option | Values | Default |
|---|---|---|
| Entertainment area | list from the bridge | chosen at setup |
| Brightness | 1-100 % | 80 % |
| Intensity | 0-200 % | 100 % |
| Update rate | Low / Medium / High / Maximum | High |

The TV and bridge are fixed after setup (remove and re-add to change them). Saving options while syncing **restarts only the pipeline** through an update listener; the config entry is not reloaded, so the switch stays on. In HA 2025.x and later an `OptionsFlow` must not assign `self.config_entry` itself.

### 4.3 Entities

One device "Glowcast <TV name>", linked to the TV's device with `via_device`, with one entity: **switch** (start/stop syncing). The switch is the device's main feature, so it has no name of its own and its id is `switch.glowcast_<tv name>`. Off after an HA restart (no state restore). The switch can be used in automations, dashboards and voice.

Example automation (goes in the README):

```yaml
triggers:
  - trigger: state
    entity_id: media_player.living_room_tv
    from: "off"
    to: "on"
actions:
  - action: switch.turn_on
    target:
      entity_id: switch.glowcast_living_room_tv
```

The startup grace period (3.5) covers the seconds between the TV reporting "on" and captures working.

### 4.4 Lifecycle and ownership

| What | Where | Story |
|---|---|---|
| Config flow, options flow and reauth flow (the screens), `async_setup_entry` / `async_unload_entry`, `entry.runtime_data` | `config_flow.py`, `__init__.py` | #6 |
| Switch entity; running the pipeline with `entry.async_create_background_task`; stopping it on switch off, unload and `EVENT_HOMEASSISTANT_STOP`; the update listener that restarts the pipeline when options change; reacting to stop reasons (switch off, start reauth, repair issue) | `switch.py`, `__init__.py` | #7 |
| Removing the entry: delete its repair issues and show one persistent notification (`glowcast_bridge_app_left_<entry id>`) on how to remove Glowcast's app in the Hue app, in Home Assistant's server language; nothing on reload or restart | `__init__.py` | #50 |
| Pipeline behaviour (start order, sub-tasks, failure handling, stop reasons); fading, hold on black and glow | `core/pipeline.py`; `core/light_state.py` | #5, #65 |

Failure handling:
- **TV off:** `source.is_on()` false (after the source's own reconnect attempt), or 5 failed captures in a row after the startup grace: stop, release the lights, switch off.
- **`OutputLost`** (stream dropped, bridge restarted, another app took over): one retry after 5 s if the bridge is free; otherwise stop, switch off, log why. Never fight for the area. **Recovery means: Glowcast stops cleanly and the next switch-on works.** A bridge reboot takes longer than the retry, so Glowcast stops; it does not wait for the bridge.
- **`OutputBusy` / `OutputUnavailable`:** do not start; switch off; log a clear message.
- **`OutputNotFound` / `OutputTooManyChannels`:** settings problems the user must fix: do not start, switch off, log, and raise a repair issue ("the entertainment area no longer exists; choose another one in Glowcast's settings" / "the entertainment area has more than 20 channels; choose or create a smaller area"). Cleared after the next successful start.
- **`OutputAuthFailed`:** switch off and start reauth.
- **Referenced entry gone** (the `webostv` or `hue` entry with the stored unique id is removed or not loaded): checked before starting (and after a stop that may be caused by it); do not start, switch off, log clearly and raise a repair issue in HA explaining which integration to set up again. Cleared after the next successful start.
- **Every stop reason** turns the switch off and is logged once (info for `USER` and `TV_OFF`, warning for the rest). Changing options while syncing restarts only the pipeline; that stop is not logged and the switch stays on.
- **HA stops:** HA does not unload entries on shutdown. It first cancels all background tasks and then fires `EVENT_HOMEASSISTANT_STOP`, waiting for both (checked in HA's `core.py`, 2025.7 and 2026.10). Cancelling the pipeline's task makes it release the lights (its shielded cleanup, 3.5); a listener on `EVENT_HOMEASSISTANT_STOP` also stops it and waits for the release, as a backstop. The log says "Home Assistant is stopping".
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

The selectable output (type and its settings) lives entirely in `options`, so a later output type (loose lights, #9) can be chosen in the settings screen without a migration. Adapters are looked up in two small registries:

```python
SOURCES = {"webos": SourceType(create, is_missing, device_identifier)}      # sources/__init__.py
OUTPUTS = {"hue_entertainment": OutputType(create, is_missing)}              # outputs/__init__.py
```

Each adapter module provides these functions itself, so `__init__.py` and `switch.py` never read a source's or output's keys: `create(hass, data, options)` builds the adapter, `is_missing(hass, data)` says whether the referenced `webostv` or `hue` entry is removed or not loaded (4.4), and the source's `device_identifier(data)` names the TV's device for `via_device` (4.3).

Config entry `VERSION = 1`, so later changes get a proper migration.

## 6. Repository layout

```
custom_components/glowcast/
  __init__.py  manifest.json  const.py
  config_flow.py           # setup, options, reauth
  switch.py
  translations/en.json  translations/nl.json
  brand/                   # icon and logo PNGs (HA 2026.3+), made from docs/brand/
  core/                    # NO homeassistant imports
    models.py  ports.py  color_engine.py  light_state.py  pipeline.py
  sources/webos.py
  outputs/hue_entertainment.py
tests/
  fakes.py                 # fake clock, source and output, shared by the tests (no HA imports)
  test_color_engine.py
  test_light_state.py      # fading, hold on black, brightness and glow, with the time as an argument
  test_pipeline.py         # the pipeline with the fakes and a fake clock
  test_init.py             # the switch and the lifecycle in HA; also through the real pipeline
  test_config_flow.py      # setup, options and reauth, including error cases
  test_webos_source.py  test_hue_output.py  test_manifest.py
.github/
  workflows/ci.yml         # hassfest, HACS validation, pytest, ruff
  ISSUE_TEMPLATE/  pull_request_template.md
scripts/                   # developer tools, not installed (render_brand.py builds brand/)
docs/ARCHITECTURE.md
docs/brand/                # icon and logo sources (SVG), colours, font
hacs.json  README.md  LICENSE  CLAUDE.md
```

`manifest.json` (keys ordered `domain`, `name`, then alphabetically): `codeowners`, `config_flow: true`, `dependencies: ["webostv", "hue"]`, `documentation`, `integration_type: "service"`, `iot_class: "local_polling"`, `issue_tracker`, `requirements: ["hue-entertainment==0.1.2"]`, `version`. Pillow ships with Home Assistant: do not list or pin it. Do not add `aiowebostv` to `loggers` (its debug logs print raw websocket traffic, including image URLs).

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

Planned as a new **output adapter** (`HaLightsOutput`) that drives any HA light through `light.turn_on` (Hue and other brands). Nothing else changes, because:

- The ColorEngine works on **channels with a position**, not on lights or areas.
- A loose light becomes a channel by giving it a position: `{"entity_id": "light.x", "x": -1.0, "z": 0.0}`. The settings screen can offer presets (left, right, top, bottom, ambient) that fill in `x`/`z`, and fine-tuning later.
- It is stored as a new output type: `"output": {"type": "ha_lights", "lights": [ ... ]}` (section 5).
- A mix (entertainment area plus a few loose lights) becomes a `CompositeOutput` that fans out to several outputs.

Rule: **no code outside `outputs/` may know about Hue**, and that includes the UI. The Hue output type provides its own setup and options steps (bridge, link button, pairing, area), its "what follows the TV" entity and its texts (stop messages, repair issues, the removal notice). The setup and options flows only chain what the types provide.

### 8.2 Other TVs

Planned as new **source adapters** behind `FrameSource`. Candidates, each still to be researched:

| Source | How it could capture | Known limits |
|---|---|---|
| Android TV / Google TV | ADB `screencap` | slow (about 1 per second), developer mode needed |
| Any HA camera entity | camera snapshot (HDMI capture card via go2rtc, or a camera aimed at the screen) | works for every TV, needs hardware |
| Samsung (Tizen) | no known local screenshot API | probably not possible |

Rule: **no code outside `sources/` may know about webOS**, and that includes the UI: the webOS source type provides its own setup step (pick a TV) and its texts. Each source decides itself whether its TV is on (`FrameSource.is_on()`), so the pipeline never needs a TV-specific entity.

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
