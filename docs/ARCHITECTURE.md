# Glowcast - Architecture

Status: **design for v0.1** (nothing built yet). This document is the source of truth for the first build. Features after v0.1 are tracked as GitHub issues (milestones v0.2 and Later), not here; this document only describes how the design leaves room for them (section 8).

## 1. Goal

Make Hue lights follow the picture on an LG webOS TV, from inside Home Assistant, with no cloud, no subscription and no extra hardware.

### v0.1 scope - the minimum

Glowcast only translates picture into colour into light. Everything Home Assistant or the Hue app already does, stays there.

- **Source:** an LG webOS TV that is already set up in HA through the core `webostv` integration.
- **Output:** one Hue **entertainment area** (for example the living room). Which lights take part, and where they stand, is set up in the Hue app. Gradient lightstrips work per segment.
- **Control:** one switch entity. Starting is done by the user, a dashboard or an **automation** (for example "TV turns on → switch on"). Glowcast never starts by itself.
- **Settings screen:** the integration's options (Settings → Devices & services → Glowcast → Configure): entertainment area, brightness, intensity, update rate. The TV and bridge are chosen once, during setup.

### Not in v0.1

Loose lights, other TVs, extra entities, effects and so on are GitHub issues in milestones v0.2 and Later. Do not build them, and do not build placeholders for them. Section 8 shows where they will plug in.

## 2. Verified facts (tested 2026-10-28 on an LG OLED65G36LA, 2023)

| Fact | Detail |
|---|---|
| Screen capture works | `ssap://tv/executeOneShot` with `{"path": "/tmp/capture.jpg", "method": "DISPLAY", "format": "JPG"}` returns `{"imageUri": "https://<tv>:3001/resources/<hash>/capture.jpg"}` |
| Frame | 960x540 JPEG, about 80 KB. The `width`/`height` parameters are ignored. |
| Rate | about 3.4 captures per second, sequential (request, then download) |
| TLS | The image URL uses the TV's self-signed certificate: download without verification, LAN only. |
| Pairing | Reusing `host` + `client_secret` from HA's `webostv` config entry works with `aiowebostv.WebOsClient(host, client_key=...)`. No pairing prompt, and it coexists with HA's own connection. |
| Hue app | The official Hue Sync TV app is not available on 2023 LG models (webOS 24+ only). |
| HDMI input works | A game console on HDMI: 10/10 captures, about 3.5 per second, real picture. |
| Hue Pro bridge | `hue-entertainment==0.1.2` pairs (link button), lists all entertainment areas with channel positions, and starts/stops a stream without errors. A left-red / centre-green / right-blue test pattern showed correctly on the lights in a test area, which returned to their previous state afterwards. |
| Gradient strips | Appear as several channels with their own positions (a TV gradient strip: 4 channels). The test area had 16 channels in total. |
| Position axes | In the test area the TV strip sits at y = 0.8 and a lamp in the far corner at y = -0.95, so y = +1 is the TV side. x and z behave as documented. |
| TV strip positions | The four channels of the TV gradient strip in the test area sit at x = -0.4 to 0.4 and z = -0.4 to 0.4, not at the edges (-1/+1). See 3.3. |
| `hue-entertainment` behaviour (source code, v0.1.2) | Colours go out as **8-bit**: values above 255 are shifted down, values of 255 or less are sent as-is, so mixing scales is wrong (see 3.4). `EntertainmentSession` defaults to `stop_others=True` and `idle_timeout=10` s. The DTLS stream is one-way UDP; a dropped stream is not detected by the library. `HueEntertainmentAPI` opens its own aiohttp session. |
| `aiowebostv` behaviour (source code, v0.10.0) | `WebOsClient(host, client_key=..., client_session=...)` accepts HA's session. `tv_state.is_on` / `is_screen_on` come from the TV's power-state subscription. Default request timeout is 20 s. `connect()` also subscribes to apps, inputs and volume, so a second connection costs a few seconds. |
| Not yet tested | DRM apps (Netflix etc.) and HDCP-protected HDMI sources may return black frames. `executeOneShot` is undocumented and could disappear in a firmware update. |

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
    async def start(self) -> None: ...             # connect; may take a few seconds
    def is_on(self) -> bool: ...                   # the source's own view of "TV is on and showing a picture"
    async def capture(self) -> Frame: ...          # raises SourceUnavailable (off/unreachable) within 5 s
    async def close(self) -> None: ...

class LightOutput(Protocol):
    max_rate_hz: float                             # how often send() may be called
    async def start(self) -> list[Channel]: ...    # claims the lights; raises OutputBusy / OutputAuthFailed / OutputNotFound
    def send(self, colors: list[ChannelColor]) -> None: ...   # non-blocking
    async def check(self) -> None: ...             # raises OutputLost when the stream is gone or taken over
    async def stop(self) -> None: ...              # releases the lights; safe to call twice
    async def close(self) -> None: ...             # releases network resources; after stop()
```

Brightness and the minimal glow are applied in the pipeline, so outputs stay dumb.

### 3.3 ColorEngine (`core/color_engine.py`)

Pure and synchronous. `compute(frame, channels, intensity) -> list[ChannelColor]`

For each channel:
1. **Region:** map `x` to a horizontal band and `z` to a vertical band of the frame, linearly from -1..1 to the frame edges (band size: a constant, start with 30 %).
2. **Average** the pixels in the region, skipping near-black pixels (letterbox bars).
3. **Black picture:** if almost the whole region is black, return black; the pipeline turns that into the minimal glow (3.5).
4. **Intensity:** scale saturation (0 = nearly white, 1 = as on screen, up to 2 = boosted).

**Known limitation (decision D12):** a TV gradient strip's channels sit close to the centre (test area: x and z between -0.4 and 0.4), so with the linear mapping they sample about 30 % in from the edges, not the edges themselves. v0.1 accepts this; stretching channels near the TV to the full screen width is a later issue.

**Performance:** 16 channels on a 64x36 frame in under 20 ms on a GitHub Actions runner (asserted in a test). Target hardware is a Raspberry Pi 4 class CPU.

### 3.4 Adapters (v0.1)

**`WebOsFrameSource`** (`sources/webos.py`)
- Resolves the `webostv` config entry **by its unique id** at runtime and reads `host` and `client_secret` from it. Never copies them.
- `aiowebostv.WebOsClient(host, client_key=..., client_session=async_get_clientsession(hass))`.
- `is_on()` uses the client's own `tv_state.is_on` and `is_screen_on` (power-state subscription), so the pipeline never needs to know which `media_player` belongs to the TV.
- `capture()`: `tv/executeOneShot`, then download `imageUri` with HA's session and `ssl=False`; wrapped in `asyncio.timeout(5)` (aiowebostv's own timeout is 20 s).
- Decodes with Pillow in the executor; use `Image.draft()` for a fast JPEG downscale to about 64x36.
- If the websocket drops while the TV is on, reconnect once before failures count.

**`HueEntertainmentOutput`** (`outputs/hue_entertainment.py`)
- Library `hue-entertainment==0.1.2` (Apache-2.0, pure-Python DTLS): `EntertainmentSession(host, app_key, client_key, idle_timeout=0)`.
- **Bridge host:** the `hue` config entry is resolved **by its unique id** (the bridge id) at runtime; HA keeps its host current.
- **Colours:** convert to **0-255** before building `LightColorCommand`s. Never send 16-bit values: the library maps values above 255 down but sends values of 255 or less as-is, so 16-bit dark values would flash. (Upstream bug, tracked in its own issue.)
- **Before starting:** list all entertainment areas. If **any** area on the bridge is active (a bridge allows one stream in total):
  - streamed by Glowcast's own application id (a stale stream, for example after an HA crash) → stop it and continue;
  - otherwise → raise `OutputBusy` and start nothing.
  Glowcast's own application id comes from `GET /auth/v1` with the `hue-application-key` header (response header `hue-application-id`); the library has no call for it. **Verify early in #4** that `active_streamer.rid` from the area's status matches this id.
- Then `session.start(area_id, stop_others=False)`. Never stop someone else's stream.
- **Area removed** → `OutputNotFound`. **HTTP 401/403 from the bridge** (key revoked) → `OutputAuthFailed`.
- `check()`: `session.is_streaming` must be true, and every 5 s `session.remote_status()` must still report `active` with Glowcast as streamer; otherwise raise `OutputLost`.
- The library opens its own aiohttp session (documented exception to the "use HA's session" rule); `close()` calls `session.aclose()`.
- `max_rate_hz = 25`.

### 3.5 Pipeline (`core/pipeline.py`)

One asyncio task per config entry, started and stopped by the switch. Not a `DataUpdateCoordinator`. The blocking-call runner and the clock are injected.

```
start:   source.start()
         wait up to 10 s (startup grace) until source.is_on() and one capture succeeds
         → if not: stop, report "TV off"; the lights were never touched
         channels = output.start()
loop:    every 1/update_rate s: frame = source.capture() → target = engine.compute(...)
         every 1/25 s: fade current → target over the measured capture interval,
                       apply brightness, apply the minimal glow, output.send()
         every 5 s:    output.check()
stop:    output.stop() → output.close() → source.close()   (on every exit path)
```

- **Minimal glow:** a channel never goes below a dim neutral white at 5 % absolute brightness, capped by the brightness setting (with brightness 3 %, the glow is 3 %). A constant, not an option.
- **Brightness:** scales all colours; 100 % = as computed.

### 3.6 Update rate

| Level | Captures per second | Use |
|---|---|---|
| Low | 1 | calm background light, lowest load on the TV |
| Medium | 2 | |
| High (default) | 3 | |
| Maximum | as fast as the TV allows (about 3.4 on a 2023 G3) | |

The TV cannot go faster than about 3.4 per second, so there is no level above Maximum. The lights are always updated 25 times per second by fading, whatever the level.

## 4. Home Assistant integration

### 4.1 Config flow (first setup)

1. **TV:** pick a loaded `webostv` config entry.
2. **Hue bridge:** pick a loaded `hue` config entry with `api_version == 2` (older bridges cannot stream). Then a **progress step** "press the link button": pair with `HueEntertainmentAPI(host).pair(device_type=...)` (blocks up to 30 s; the returned `username` is stored as `app_key`, `clientkey` as `client_key`). `device_type` is `glowcast#<name>`, truncated to the bridge's limit (20 characters before `#`, 19 after). Timeout → "button not pressed, try again"; any other error → "could not reach the bridge".
3. **Entertainment area:** pick from the bridge's list. If there are none, stop with a message explaining how to create one in the Hue app.

Rules:
- **One Glowcast entry per TV:** the entry's unique id is the `webostv` entry's unique id; adding the same TV twice aborts.
- **Two TVs on one bridge** are allowed, but only one can stream at a time: the second one gets "another app is streaming".
- **Reauth:** when the output raises `OutputAuthFailed`, call `entry.async_start_reauth(hass)`. HA then shows a re-authentication notification; the user presses the bridge button again and the new keys are stored with all settings kept.
- **Why a separate Hue pairing:** HA's `hue` config entry stores only an `api_key`. Streaming needs a `clientkey` (the DTLS pre-shared key), which the bridge hands out only when an app pairs with `generateclientkey`. It cannot be read back later, so Glowcast pairs once itself.

### 4.2 Options (the settings screen)

| Option | Values | Default |
|---|---|---|
| Entertainment area | list from the bridge | chosen at setup |
| Brightness | 1-100 % | 80 % |
| Intensity | 0-200 % | 100 % |
| Update rate | Low / Medium / High / Maximum | High |

The TV and bridge are fixed after setup (remove and re-add to change them). Saving options while syncing **restarts only the pipeline** through an update listener; the config entry is not reloaded, so the switch stays on.

### 4.3 Entities

One device "Glowcast <TV name>" with one entity: **switch** (start/stop syncing). Off after an HA restart. The switch can be used in automations, dashboards and voice.

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
| Config flow, options flow, reauth, `async_setup_entry` / `async_unload_entry`, `entry.runtime_data`, update listener | `config_flow.py`, `__init__.py` | #6 |
| Switch entity; starting the pipeline with `entry.async_create_background_task`; stopping it on switch off, unload and `EVENT_HOMEASSISTANT_STOP` | `switch.py`, `__init__.py` | #7 |
| Pipeline behaviour (start order, fading, glow, failure handling) | `core/pipeline.py` | #5 |

Failure handling:
- **TV off:** `source.is_on()` false, or 5 failed captures in a row (after the startup grace): stop, release the lights, switch off.
- **TV connection drops while the TV is on:** reconnect once; only then count failures.
- **`OutputLost`** (stream dropped, bridge restarted, another app took over): one retry after 5 s if the bridge is free; otherwise stop, switch off, log why. Never fight for the area.
- **`OutputBusy` / `OutputNotFound`:** do not start; switch off; log a clear message.
- **`OutputAuthFailed`:** switch off and start reauth.
- **HA stops:** HA does not unload entries on shutdown, so a listener on `EVENT_HOMEASSISTANT_STOP` stops the pipeline and releases the lights.
- **HA crashed while streaming:** the next start finds Glowcast's own stale stream and restarts it (3.4).

## 5. Configuration data model - built for growth

Config entry `data` and `options` store source and output as **typed blocks**, even though v0.1 has only one type of each. Other integrations' entries are referenced by **unique id**, never by entry id, so removing and re-adding the TV or bridge in HA does not break Glowcast.

```python
data = {
    "source": {"type": "webos", "unique_id": "<webostv entry unique id>"},
    "output": {"type": "hue_entertainment", "bridge_unique_id": "<hue entry unique id>",
               "app_key": "...", "client_key": "..."},
}
options = {
    "output": {"area_id": "..."},
    "brightness": 80, "intensity": 100, "update_rate": "high",
}
```

Adapters are looked up in two small registries:

```python
SOURCES = {"webos": WebOsFrameSource}
OUTPUTS = {"hue_entertainment": HueEntertainmentOutput}
```

Config entry `VERSION = 1`, so later changes get a proper migration.

## 6. Repository layout

```
custom_components/glowcast/
  __init__.py  manifest.json  const.py
  config_flow.py           # setup, options, reauth
  switch.py
  translations/en.json  translations/nl.json
  core/                    # NO homeassistant imports
    models.py  ports.py  color_engine.py  pipeline.py
  sources/webos.py
  outputs/hue_entertainment.py
tests/
  test_color_engine.py
  test_pipeline.py         # fake source + fake output + fake clock
  test_config_flow.py      # minimal: happy path, already configured, reauth
.github/
  workflows/ci.yml         # hassfest, HACS validation, pytest, ruff
  ISSUE_TEMPLATE/  pull_request_template.md
docs/ARCHITECTURE.md
hacs.json  README.md  LICENSE  CLAUDE.md
```

`manifest.json`: `domain`, `name`, `version`, `codeowners`, `documentation`, `issue_tracker`, `config_flow: true`, `iot_class: "local_push"`, `dependencies: ["webostv", "hue"]`, `requirements: ["hue-entertainment==0.1.2"]`. Pillow ships with Home Assistant: do not list or pin it. Do not add `aiowebostv` to `loggers` (its debug logs print raw websocket traffic, including image URLs).

Translations live in `translations/en.json` and `translations/nl.json` (custom integrations do not use `strings.json` at runtime). Dutch shows the levels as Laag / Middel / Hoog / Maximaal.

## 7. Testing

- **ColorEngine:** synthetic frames (solid colours, left/right split, letterbox bars, all black), plus the performance budget.
- **Pipeline:** fake source, fake output and a fake clock: start order (TV off → lights never touched), startup grace, fading over the measured interval, minimal glow, `OutputLost` / `OutputBusy` / `OutputAuthFailed`, and that `stop()` and `close()` run on every exit path.
- **Config flow (minimal in v0.1):** happy path, already configured, reauth. Full coverage is a v0.2 issue.
- **Soak test:** two hours of real viewing; no crash, no memory growth, recovers from a TV standby and a bridge restart.
- **Manual end-to-end:** an LG webOS TV, a Hue bridge and an entertainment area with a gradient lightstrip.

## 8. Room to grow (not built in v0.1)

### 8.1 Loose lights with a position

Planned as a new **output adapter** (`HaLightsOutput`) that drives any HA light through `light.turn_on` (Hue and other brands). Nothing else changes, because:

- The ColorEngine works on **channels with a position**, not on lights or areas.
- A loose light becomes a channel by giving it a position: `{"entity_id": "light.x", "x": -1.0, "z": 0.0}`. The settings screen can offer presets (left, right, top, bottom, ambient) that fill in `x`/`z`, and fine-tuning later.
- It is stored as a new output type: `"output": {"type": "ha_lights", "lights": [ ... ]}` (section 5).
- A mix (entertainment area plus a few loose lights) becomes a `CompositeOutput` that fans out to several outputs.

Rule for v0.1 to keep this open: **no code outside `outputs/` may know about Hue.**

### 8.2 Other TVs

Planned as new **source adapters** behind `FrameSource`. Candidates, each still to be researched:

| Source | How it could capture | Known limits |
|---|---|---|
| Android TV / Google TV | ADB `screencap` | slow (about 1 per second), developer mode needed |
| Any HA camera entity | camera snapshot (HDMI capture card via go2rtc, or a camera aimed at the screen) | works for every TV, needs hardware |
| Samsung (Tizen) | no known local screenshot API | probably not possible |

Rule for v0.1 to keep this open: **no code outside `sources/` may know about webOS.** Each source decides itself whether its TV is on (`FrameSource.is_on()`), so the pipeline never needs a TV-specific entity.

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
| D11 | Minimal glow on a black picture | The room does not go fully dark between scenes or on black screens |
| D12 | Linear position mapping in v0.1 | Simple and predictable; stretching TV-strip channels to the screen edges is a later improvement |
| D13 | Pipeline in `core/` with injected runner and clock | Testable with plain fakes, no Home Assistant test framework needed |
| D14 | Reference other integrations by unique id | Survives removing and re-adding the TV or bridge in HA |
