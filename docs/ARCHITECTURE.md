# Glowcast - Architecture

Status: **design for v0.1** (nothing built yet). This document is the source of truth for the first build. Features after v0.1 are tracked as GitHub issues (milestones v0.2 and Later), not here; this document only describes how the design leaves room for them (section 8).

## 1. Goal

Make Hue lights follow the picture on an LG webOS TV, from inside Home Assistant, with no cloud, no subscription and no extra hardware.

### v0.1 scope - the minimum

Glowcast only translates picture into colour into light. Everything Home Assistant or the Hue app already does, stays there.

- **Source:** an LG webOS TV that is already set up in HA through the core `webostv` integration.
- **Output:** one Hue **entertainment area** (for example the living room). Which lights take part, and where they stand, is set up in the Hue app. Gradient lightstrips work per segment.
- **Control:** one switch entity. Starting is done by the user, a dashboard or an **automation** (for example "TV turns on → switch on"). Glowcast never starts by itself.
- **Settings screen:** the integration's options (Settings → Devices & services → Glowcast → Configure): TV, entertainment area, brightness, intensity, update rate.

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
| Not yet tested | DRM apps (Netflix etc.) may return black frames. |

## 3. Architecture: ports and adapters

A pipeline with three stages. The two stages that touch the outside world sit behind a small interface (port). v0.1 has exactly one adapter per port.

```
                 ┌──────────────────────── Pipeline (one asyncio task) ─────────────────────────┐
 LG TV  ──►  FrameSource  ──►  Frame  ──►  ColorEngine  ──►  list[ChannelColor]  ──►  LightOutput  ──►  Hue Bridge
             (port)                        (pure logic)                               (port)
             └ WebOsFrameSource                                                       └ HueEntertainmentOutput
```

### 3.1 Core types (`core/models.py`)

Pure dataclasses, no Home Assistant imports.

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

**A channel with a position is the common language** between outputs and the ColorEngine. The ColorEngine never knows where a position came from. That is what makes loose lights possible later (section 8.1).

The Hue bridge also gives a front/back value (`y`). v0.1 ignores it.

### 3.2 Ports (`core/ports.py`)

```python
class FrameSource(Protocol):
    async def capture(self) -> Frame: ...          # raises SourceUnavailable when the TV is off/unreachable
    async def close(self) -> None: ...

class LightOutput(Protocol):
    async def start(self) -> list[Channel]: ...    # claims the lights, returns the channels to drive
    async def send(self, colors: list[ChannelColor]) -> None: ...
    async def stop(self) -> None: ...              # releases the lights; safe to call twice
    max_rate_hz: float                             # how often send() may be called
```

Brightness is applied before `send()` (in the pipeline), so outputs stay dumb.

### 3.3 ColorEngine (`core/color_engine.py`)

Pure, synchronous, fully unit-testable.

`compute(frame, channels, intensity) -> list[ChannelColor]`

For each channel:
1. **Region:** map `x` to a horizontal band and `z` to a vertical band of the frame (band size: a constant, start with 30%).
2. **Average** the pixels in the region, skipping near-black pixels (letterbox bars). If almost everything is black, the colour is black.
3. **Intensity:** scale saturation (0 = nearly white, 1 = as on screen, up to 2 = boosted).

### 3.4 Adapters (v0.1)

**`WebOsFrameSource`** (`sources/webos.py`)
- Reads `host` and `client_secret` from the chosen `webostv` config entry at runtime. Never copies them.
- `aiowebostv.WebOsClient` (already in HA) for `tv/executeOneShot`; downloads `imageUri` with HA's aiohttp session, `ssl=False`.
- Decodes and downscales (to about 64x36) with Pillow in the executor.

**`HueEntertainmentOutput`** (`outputs/hue_entertainment.py`)
- Library `hue-entertainment` (Apache-2.0, pure-Python DTLS): `EntertainmentSession(host, app_key, client_key)`.
- `start()`: `session.start(area_id)`, map the area's channels to `Channel(x, z)`.
- `send()`: 16-bit RGB `LightColorCommand`s. `max_rate_hz = 25`.

### 3.5 Pipeline (`pipeline.py`)

One asyncio task per config entry, started and stopped by the switch. Not a `DataUpdateCoordinator`.

```
start:  channels = output.start()
loop:   every 1/update_rate seconds: frame = source.capture() → target = engine.compute(...)
        meanwhile, at output.max_rate_hz: fade current → target, apply brightness, output.send()
stop:   output.stop() → source.close()        (always: switch off, TV off, error, unload, HA stop)
```

The fade takes one capture interval, so a lower update rate gives slower, calmer transitions.

### 3.6 Update rate

A setting in the options, shown as a level:

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
2. **Hue bridge:** pick a loaded `hue` config entry (for the host), then "press the link button" → `HueEntertainmentAPI.pair(device_type="glowcast#<ha-name>")` → store `app_key` and `client_key`. (HA's own Hue pairing has no client key, so this one-time step is needed.)
3. **Entertainment area:** pick from the bridge's list.

### 4.2 Options (the settings screen)

| Option | Values | Default |
|---|---|---|
| Entertainment area | list from the bridge | chosen at setup |
| Brightness | 1-100 % | 80 % |
| Intensity | 0-200 % | 100 % |
| Update rate | Low / Medium / High / Maximum | High |

Changing options while syncing restarts the pipeline.

### 4.3 Entities

One device "Glowcast <TV name>" with one entity: **switch** (start/stop syncing). Off after a HA restart. The switch can be used in automations, dashboards and voice.

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

### 4.4 Lifecycle

- **TV off** (`SourceUnavailable` 5 times in a row, or its `media_player` goes `off`): stop, release the lights, switch off.
- **Hue stream lost:** one retry after 5 s; if that fails, stop and switch off. Errors go to the HA log.
- **Always** call `output.stop()` on every exit path.

## 5. Configuration data model - built for growth

Config entry `data` and `options` store source and output as **typed blocks**, even though v0.1 has only one type of each:

```python
data = {
    "source": {"type": "webos", "entry_id": "<webostv entry id>"},
    "output": {"type": "hue_entertainment", "bridge_entry_id": "...", "app_key": "...", "client_key": "..."},
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

Set the config entry `VERSION = 1` so later changes get a proper migration.

## 6. Repository layout

```
custom_components/glowcast/
  __init__.py  manifest.json  const.py
  config_flow.py           # setup + options
  pipeline.py
  switch.py
  strings.json  translations/en.json  translations/nl.json
  core/                    # NO homeassistant imports
    models.py  ports.py  color_engine.py
  sources/webos.py
  outputs/hue_entertainment.py
tests/
  test_color_engine.py
  test_pipeline.py         # fake source + fake output
docs/ARCHITECTURE.md
hacs.json  README.md  LICENSE  CLAUDE.md
```

`manifest.json`: `dependencies: ["webostv", "hue"]`, `requirements: ["hue-entertainment==0.1.2", "Pillow"]`.

The Dutch translation stays in v0.1 so the levels show as Laag / Middel / Hoog / Maximaal.

## 7. Testing

- **ColorEngine:** synthetic frames (solid colours, left/right split, letterbox bars, all black).
- **Pipeline:** fake source and output; start/stop, TV off, stream lost, and that `stop()` is always called.
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

Rule for v0.1 to keep this open: **no code outside `sources/` may know about webOS.** The `media_player` used to detect "TV off" is stored with the source, not hard-coded.

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
