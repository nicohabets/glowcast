# Research: other TV brands as a Glowcast source (#12)

Status: **desk research done (2026-10-28); hands-on testing postponed.** The questions meant for hands-on testing were answered from online reports instead (see "Open questions answered from online reports"). Nothing in this document has been tested on hardware; hands-on results go into the "Verified facts" table in [`ARCHITECTURE.md`](../ARCHITECTURE.md#2-verified-facts-tested-2026-10-28-on-an-lg-oled65g36la-2023).

## The bar

A source must deliver a picture of what is on screen (architecture 3.2, `FrameSource.capture() -> Frame`):

- **Must:** local (no cloud), about 1 frame per second or more, no extra hardware if possible, set up once from Home Assistant.
- **Better:** 3 or more frames per second, no developer mode, and reuse of an existing Home Assistant integration's pairing (as `WebOsFrameSource` reuses `webostv`).

For comparison, the LG webOS source reaches about 4.1 captures per second with no developer mode and reuses HA's pairing (verified, architecture section 2).

## How to read the evidence labels

- **[verified: source]** - I read the source code (a tag or `dev`/`main` branch, linked).
- **[verified: docs]** - official documentation.
- **[reported]** - forum post, blog, issue or third-party write-up.
- **[inference]** - my own conclusion from the code or documentation, not tested and not stated by any source.

## Comparison

| Candidate | How | Local | Frames per second | Setup effort | DRM (Netflix etc.) | Verdict |
|---|---|---|---|---|---|---|
| **LG webOS** (reference) | `tv/executeOneShot` | yes | about 4.1 (measured) | none, reuses `webostv` | not tested yet | built (v0.1) |
| **Any HA camera entity** backed by MJPEG (USB HDMI grabber through go2rtc, the Hyperion camera entity, a camera with a still-image URL) | `camera.async_get_image()` | yes | several per second likely [inference]; capped by the camera platform | high: extra hardware, splitter, go2rtc setup | grabber: protected HDMI is black unless the splitter strips HDCP; a camera filming the screen sees everything | **feasible** (with hardware) |
| **Any HA camera entity** backed by H.264/RTSP (an IP camera aimed at the screen) | same | yes | about one frame per keyframe interval (often 1-4 s) | low if the camera exists | works (optical) | **not useful** for ambient light: too slow, 1 s or more of delay |
| **Philips with Ambilight** (Saphi and Android) | JointSpace `ambilight/measured` / `processed`: edge colours computed by the TV, not a picture | yes | no measured rate; community projects poll at 8-15 Hz (configurable 0.25-30) [reported] | low: reuses `philips_js` (no developer mode) | likely works, computed by the TV [inference] | **feasible, but not a frame** (product question, see below) |
| **Android TV / Google TV / Fire TV** (Sony, Philips, TCL, Hisense, Chromecast with Google TV, Google TV Streamer) | ADB `screencap` or `screenrecord --output-format=raw-frames` | yes | no TV measurement; about 0.6-0.9 s per raw `screencap` on phones, 2 s with PNG [reported] | high: developer mode, network debugging, accept ADB prompt; on the Google TV Streamer ADB reportedly drops and is off after every reboot | Netflix black [verified: source, reported]; on a Sony Android TV `screencap` returned nothing on the HDMI/TV screen and in Netflix [reported] | **not feasible for TVs** (no video picture); streamers only through an H.264 stream (scrcpy), too heavy for now |
| **LG Netcast** (2012-2013 only) | UDAP `GET /udap/api/data?target=screen_image` (960x540 JPEG) | yes | unknown; the spec says the call "does not occur frequently" | low: reuses `lg_netcast` pairing key | unknown | feasible, **not recommended** (old TVs, small audience) |
| **Enigma2 receivers** (Vu+, Dreambox) with OpenWebif | `GET /grab?mode=all&format=jpg&r=<width>` | yes | unknown: each grab starts a process | low: reuses `enigma2` entry | unknown | feasible, **not recommended** (set-top box, small audience) |
| **Samsung Tizen** | no local capture API; only a sideloaded app sampling 16 points (HyperTizen) | (yes) | unknown | very high: developer mode, Tizen Studio, sideloading, re-signing | unknown | **not feasible** |
| **Roku** | ECP has no capture; developer screenshot only of a sideloaded app, never of playback | - | - | - | - | **not feasible** |
| **Apple TV** | pyatv: now-playing artwork only; Xcode screenshots only for developers | - | - | - | - | **not feasible** |
| **Chromecast (Cast protocol)** | no capture namespace | - | - | - | - | **not feasible** |
| **Sony (non-Android), Panasonic, Hisense VIDAA, Vizio, Sharp** | no capture call in the APIs HA uses | - | - | - | - | **not feasible** |

**Not a new source:** a streamer (Google TV Streamer, Apple TV, Fire TV, game console) plugged into an LG webOS TV over HDMI is already covered by `WebOsFrameSource`. Capturing an HDMI input works on the G3 (verified, architecture section 2, "HDMI input works").

## Recommendation, in order of value

1. **Any HA camera entity** (generic `CameraFrameSource`). It works for every TV brand and every app, uses only HA's public camera API, and the user decides the hardware. It only reaches 3 or more frames per second with low delay when an MJPEG source backs the entity (a USB HDMI grabber through go2rtc, or the Hyperion camera entity). The README must say plainly that RTSP cameras are too slow, and Glowcast should not recommend HDCP-stripping splitters.
2. **Android TV / Google TV over ADB: do not build now.** Online reports say that on an Android TV panel (Sony) `screencap` returns nothing while the HDMI/TV screen or Netflix plays, and Hyperion's Android grabber on a Bravia stays dark in YouTube and Netflix, so the picture people actually watch is not captured. On streamers (Chromecast with Google TV, Shield) only an H.264 screen stream (scrcpy) is fast, which means video decoding inside Home Assistant, and DRM apps are still black. A one-hour hands-on check on the Sony could confirm this; it is not needed to decide.
3. **Philips Ambilight**, after a product decision (below). It is easy to set up and probably works with DRM content, but it gives edge colours rather than a picture, and `philips_js` can already make the TV drive Hue itself ("Ambilight + Hue" switch), which lowers the value for Glowcast.
4. **LG Netcast and Enigma2**: feasible, but small audiences. Only worth building if someone asks.
5. **Samsung, Roku, Apple TV, Chromecast, other brands**: not feasible with any public local API. README note added.

## Per candidate

### Any Home Assistant camera entity

How a custom integration gets frames:

- `homeassistant.components.camera.async_get_image(hass, entity_id, timeout=10, width=None, height=None)` returns an `Image(content_type, content)` and raises `HomeAssistantError` on timeout. [verified: source, [camera/\_\_init\_\_.py](https://github.com/home-assistant/core/blob/dev/homeassistant/components/camera/__init__.py)]
- Resizing there is best effort: for JPEG it uses TurboJPEG DCT scaling, at most 1/8, so 1920x1080 comes back as 240x135, not 64x36. Glowcast still needs its own decode and resize in the executor (as in `WebOsFrameSource`). [verified: source, [camera/img_util.py](https://github.com/home-assistant/core/blob/dev/homeassistant/components/camera/img_util.py)]
- When a camera has `use_stream_for_stills`, HA takes stills from go2rtc (WebRTC provider) or else from its `stream` component. [verified: source, [camera/helper.py](https://github.com/home-assistant/core/blob/dev/homeassistant/components/camera/helper.py)] HA's `stream` stills come from the last **keyframe** only. [verified: source, [stream/core.py](https://github.com/home-assistant/core/blob/dev/homeassistant/components/stream/core.py) `KeyFrameConverter`]

What each camera platform delivers:

- **`generic` with a still-image URL:** one HTTP request per call, cached for `1/framerate` seconds; the framerate defaults to **2**, so the user has to raise it for more. [verified: source, [generic/camera.py](https://github.com/home-assistant/core/blob/dev/homeassistant/components/generic/camera.py), [generic/config_flow.py](https://github.com/home-assistant/core/blob/dev/homeassistant/components/generic/config_flow.py)]
- **`ffmpeg` camera:** starts a new ffmpeg process per snapshot and ignores width and height. [verified: source, [ffmpeg/camera.py](https://github.com/home-assistant/core/blob/dev/homeassistant/components/ffmpeg/camera.py)] Too slow for 3 per second [inference].
- **HA's built-in go2rtc** (HA 2024.11+): `async_get_image` calls go2rtc's `/api/frame.jpeg`. [verified: source, [go2rtc/\_\_init\_\_.py](https://github.com/home-assistant/core/blob/dev/homeassistant/components/go2rtc/__init__.py)] Inside go2rtc each request waits for the next keyframe; for H.264/H.265 it transcodes with ffmpeg, for an MJPEG source it returns the next JPEG directly. [verified: source, [go2rtc internal/mjpeg/init.go](https://github.com/AlexxIT/go2rtc/blob/master/internal/mjpeg/init.go)] So: an MJPEG source gives several frames per second [inference, not measured], an H.264 camera about one per keyframe interval.
- **HA `hyperion` integration** exposes a camera entity with Hyperion's live image (pushed as JPEG). [verified: source, [hyperion/camera.py](https://github.com/home-assistant/core/blob/dev/homeassistant/components/hyperion/camera.py)] Users who already run Hyperion.ng with a grabber get a usable entity for free.
- The HACS "WebRTC Camera" integration has no camera platform, so it is not a frame source. [verified: source, [repo tree](https://github.com/AlexxIT/WebRTC/tree/master/custom_components/webrtc)]

USB HDMI grabbers (MS2109, MS2130):

- About $10, 1080p30 (MS2109) to 1080p120 (MS2130), MJPEG over USB. Capture delay about 50-70 ms on an MS2130. [reported, [HyperHDR discussion #499](https://github.com/awawa-dev/HyperHDR/discussions/499), [VideoHelp](https://forum.videohelp.com/showthread.php?p=2693474)]
- go2rtc reads them with `v4l2:device?video=/dev/video0&input_format=mjpeg` (since 1.9.9); MJPEG costs no CPU. [verified: docs, [go2rtc v4l2 README](https://github.com/AlexxIT/go2rtc/blob/master/internal/v4l2/README.md)] The go2rtc add-on can pass video devices through (`video: true`). [verified: source, [add-on config.yaml](https://github.com/AlexxIT/hassio-addons/blob/master/go2rtc/config.yaml)]
- They only see **external HDMI sources**, never the TV's own apps.
- HDCP-protected sources give black or no signal unless a splitter strips HDCP, which is legally grey. [reported, [OBS forum](https://obsproject.com/forum/threads/hdmi-splitter-which-allows-obs-to-capture-source-as-a-device.9881), [VideoHelp](https://forum.videohelp.com/showthread.php?p=2335201)]

A camera aimed at the screen: works with every app (it films the panel), but RTSP delay is about 1 s at best and can grow to many seconds [reported, [HA community](https://community.home-assistant.io/t/20s-delay-between-two-cameras-reolink-frigate-go2rtc/628075)]; auto exposure, white balance and night IR will distort colours [inference].

Prior art: no HA integration found that turns a camera entity into Hue entertainment colours.

### Android TV / Google TV / Fire TV (ADB)

- **`screencap`** always grabs the full display, as PNG (`-p`) or raw RGBA (about 8 MB at 1080p); it has no scaling option. A JPEG option (`-j`) exists on AOSP `main` (Android 16), but not in the android-12, -14 and -15 release tags. [verified: source, [screencap.cpp](https://android.googlesource.com/platform/frameworks/base/+/refs/heads/main/cmds/screencap/screencap.cpp)]
- **`screenrecord --output-format=raw-frames --size WxH`** streams raw frames, scaled on the device, over one `exec-out` session; `raw-frames` exists since Android 11. The default time limit is 180 s, so older builds need a restart loop. [verified: source, [screenrecord.cpp](https://android.googlesource.com/platform/frameworks/av/+/refs/heads/main/cmds/screenrecord/screenrecord.cpp)] It may only emit a frame when the screen changes [inference]. Not tested on any TV by anyone I could find.
- **Speed:** no measurement on a TV chip found. Phones: about 2.1 s median with `-p`, about 0.7 s raw [reported, [elliotgao2](https://elliotgao2.github.io/handsets/blog/category/performance/)]; 0.3-0.5 s at best [reported, [HeadSpin](https://headspin.io/blog/speeding-up-android-screenshots-with-mjpeg-servers)]. scrcpy (H.264 over ADB) reaches about 30 fps on a Shield TV but needs H.264 decoding [reported, [Hackaday.io](https://hackaday.io/project/205729-diy-ambilight-for-any-tv)].
- **Libraries:** `androidtv` 0.0.75 and `adb-shell` 0.4.4, both pinned by HA core, effectively unmaintained since late 2024 [verified: PyPI]. `androidtv`'s screencap is `shell("screencap -p")` [verified: source, [adb_manager_async.py](https://github.com/JeffLIrion/python-androidtv/blob/master/androidtv/adb_manager/adb_manager_async.py)]. `adb-shell` has **no Wireless Debugging pairing** (no SPAKE2/TLS code) [verified: source].
- **HA `androidtv` integration** (ADB): its screencap feeds the media player's picture, with an interval in **minutes** (default 5) [verified: source, [androidtv/const.py, media_player.py](https://github.com/home-assistant/core/tree/dev/homeassistant/components/androidtv)]. The ADB key is stored in `.storage/androidtv_adbkey`; a Glowcast source could reuse it for its own connection. Whether a second ADB connection next to HA's is allowed is open. The HA docs warn that only one device can be connected [verified: docs, [androidtv](https://www.home-assistant.io/integrations/androidtv)].
- **HA `androidtv_remote` integration** (the one most Google TV users have): remote control only, no capture [verified: source]. Its pairing cannot be reused for ADB.
- **Setup:** the user turns on developer options and network debugging and accepts the "always allow" prompt [verified: docs]. Newer Google TV builds and the Google TV Streamer reportedly only offer Wireless Debugging, with a random port and a pairing code, so users run `adb pair` and `adb tcpip 5555` from a PC, and lose that on every reboot [reported, [Google Nest community](https://www.googlenestcommunity.com/t5/Streaming/Can-t-ADB-to-Streamer-with-Wired-Connection/m-p/739044), [Channels forum](https://community.getchannels.com/t/adbtuner-a-channel-tuning-application-for-networked-google-tv-android-tv-devices/36822?page=95)].
- **DRM:** screen captures skip secure layers by default (`captureSecureLayers = false`) [verified: source, [CaptureArgs.aidl](https://android.googlesource.com/platform/frameworks/native/+/refs/heads/main/libs/gui/aidl/android/gui/CaptureArgs.aidl)]. Netflix "will always show up black", and some TVs put video on a hardware overlay that captures cannot see [reported, [hyperion-android-reborn wiki](https://github.com/evanwhitt/hyperion-android-reborn/wiki)]. HDMI inputs and Live TV on Android TVs (Sony): **no source found**, part 2 question.
- **Other routes need an app installed on the TV** (hyperion-android-reborn with MediaProjection at 10-60 fps, Hue Essentials, Yeelight TV Orchestra) [reported]. Glowcast would then have to receive a Hyperion stream: a different kind of source, out of scope here.

### Philips with Ambilight (JointSpace)

- `ambilight/measured` ("the measurements that serve as input to the algorithm") and `ambilight/processed` ("the pixels after processing") return colours per side and per Ambilight pixel; `ambilight/topology` gives the layout. No screenshot endpoint. [verified: docs, [JointSpace ambilight API](http://jointspace.sourceforge.net/projectdata/documentation/jasonApi/1/doc/API-ambilight.html); source, [ha-philipsjs](https://github.com/danielperna84/ha-philipsjs)]
- HA's `philips_js` integration (`ha-philipsjs` 3.3.0) stores `host`, `api_version`, `username` and `password` in its entry data, polls every 30 s, and does not fetch measured or processed colours in its update. It has an "Ambilight + Hue" switch, so the TV can drive Hue itself. [verified: source, [philips_js](https://github.com/home-assistant/core/tree/dev/homeassistant/components/philips_js)]
- Rate: the "Ambilight Sync" custom integration polls measured or processed colours, recommends 15 Hz (limits 0.25-30 Hz) [verified: source and README, [uccae/Ambilight-Sync-for-Home-Assistant](https://github.com/uccae/Ambilight-Sync-for-Home-Assistant)]; its announcement mentions up to 8 Hz on one lamp [reported, [HA community](https://community.home-assistant.io/t/philips-ambilight-sync-for-any-home-assistant-rgb-light/1025870)]. Another project calls the API "fragile and temperamental" and limits to one request per 0.1 s [reported, [aegjoyce/custom-ambilight](https://github.com/aegjoyce/custom-ambilight)].
- DRM and HDMI: the TV computes the colours from the video itself, so it probably works with Netflix and HDMI [inference].
- Open: whether Titan OS (API 6.1) and Philips Google TV sets still expose these endpoints; Titan OS users report unreliable connections [reported, [HA community](https://community.home-assistant.io/t/philips-tv-with-titan-os/1014128)].

**Product question (not decided here):** `FrameSource` delivers a picture; Philips delivers edge colours. Options:

- **A.** Paint the edge colours into a small synthetic `Frame` (border pixels coloured, the inside black). Fits the port without changes; the ColorEngine already skips near-black pixels, but how well its regions map to the edges needs a test.
- **B.** A second port for sources that deliver colours per position. Architecture change.
- **C.** Do not support Philips: the TV already drives Hue itself through Ambilight + Hue.

### LG Netcast (2012-2013)

`GET /udap/api/data?target=screen_image` returns a 960x540 JPEG; the spec says "since this query does not occur frequently, use Connection: Close". UDAP 2.0 covers NetCast 3.0/4.0 (2012-2013). [verified: docs, [NetCast UDAP 2.0 spec](https://webostv.developer.lge.com/assets/netcast/NetCast-UDAP.pdf), p. 54] HA's `lg_netcast` (pylgnetcast 0.3.9) stores the pairing `access_token`; pylgnetcast parses every data response as XML, so Glowcast would make its own HTTP call [verified: source, [pylgnetcast](https://github.com/Drafteed/python-lgnetcast)]. Rate, DRM and HDMI behaviour unknown.

### Enigma2 receivers (OpenWebif)

`GET /grab?mode=video|osd|all&format=jpg&r=<width>` runs `/usr/bin/grab` on the box and returns a JPEG of the live picture [verified: source, [OpenWebif grab.py](https://github.com/E2OpenPlugins/e2openplugin-OpenWebif)]. `openwebifpy` 4.3.1 (HA `enigma2`) already has `get_screen_grab_url()` [verified: source, [openwebifpy](https://github.com/autinerd/openwebifpy)]. Each grab starts a process, so the rate is unknown; it only covers what the receiver shows.

### Samsung Tizen

- `samsungtvws` 3.0.6 (HA `samsungtv`): no capture or mirror call; `art.get_thumbnail()` returns stored Frame artworks, not the screen [verified: source, [samsung-tv-ws-api](https://github.com/xchwarze/samsung-tv-ws-api)].
- SmartThings is cloud and has no picture capability [reported, [SmartThings community](https://community.smartthings.com/t/samsung-tv-capabilities-samsungvd-ambient/268102)].
- A Tizen web app can only capture its own content [reported, [Samsung developer forum](https://forum.developer.samsung.com/t/screenshot-screen-capture-in-tizen-4-6/24801)].
- HyperTizen samples 16 fixed points through an undocumented system library and pushes them to Hyperion; it needs Tizen 6.5+, developer mode, Tizen Studio and sideloading [verified: source, [reisxd/HyperTizen](https://github.com/reisxd/HyperTizen) `Capturer.cs` and docs]; the Tizen 8 fork "doesn't actually work yet" [verified: README, [iceteaSA/HyperTizen](https://github.com/iceteaSA/HyperTizen)].
- The Hue Sync TV app on Samsung is a native partner app [verified: docs, [Signify press release](https://www.signify.com/global/our-company/news/press-releases/2023/20230104-immerse-yourself-in-surround-lighting-with-the-new-philips-hue-sync-tv-app-for-samsung-tvs)]; there is no public API behind it.

### Roku, Apple TV, Chromecast, other brands

- **Roku:** ECP's only image endpoint is app icons [verified: docs, [ECP](https://developer.roku.com/docs/developer-program/debugging/external-control-api.md)]; the developer screenshot covers "your sideloaded app's UI (you cannot take a screenshot of any content playback)" [verified: docs, [developer setup](https://developer.roku.com/docs/developer-program/getting-started/developer-setup.md)].
- **Apple TV:** pyatv 0.18.0 has now-playing artwork only [verified: source, [pyatv](https://pyatv.dev/documentation/supported_features/)]; Xcode can screenshot a paired Apple TV for developers [reported, [Macworld](https://www.macworld.com/article/226819/how-to-take-screenshots-of-the-apple-tv.html)].
- **Chromecast:** pychromecast 14.1.0 has no capture namespace [verified: source, [pychromecast](https://github.com/home-assistant-libs/pychromecast)].
- **Sony (non-Android) / Panasonic / Hisense VIDAA / Vizio:** no capture in pybravia 0.5.3, panasonic-viera 0.4.6, hisensetv or vizaio 0.7.0 [verified: source]; Sony's Pro Bravia API index lists no capture service [verified: docs, [index](https://pro-bravia.sony.net/develop/integrate/rest-api/spec/index.html), index page only]. Sharp: not checked in depth.
- **Xbox Device Portal** has `/ext/screenshot`, but Device Portal is a developer-mode feature [verified: docs, [Microsoft](https://learn.microsoft.com/en-us/windows/uwp/xbox-apps/wdp-media-capture-api)].

## Open questions answered from online reports

Hands-on testing was postponed by the product owner; these answers come from online reports and are **not verified on hardware**.

- **Does ADB capture show video on an Android TV panel?** Mostly no. On a Sony KDL-49W800G (Android 9) `screencap -p` returned zero bytes "while Sony's native HDMI/TV screen or Netflix was active" and worked again "after returning Home" [reported, [cleanyourtv #92](https://github.com/Gathin23/cleanyourtv/issues/92), 2026]. Users of the Hyperion Android grabber on Bravia TVs report it works "only in menu and turns dark in Kodi, Moonlight, Youtube", stays black in YouTube and Netflix, and switches off when changing HDMI inputs [reported, [Hyperion forum](https://hyperion-project.org/forum/index.php?thread/2708-android-screen-grabber/), about 2018]. On Android TV, HDMI inputs are handled by a dedicated hardware session that draws onto the app's surface [verified: docs, [TvInputService.HardwareSession](https://developer.android.com/reference/android/media/tv/TvInputService.HardwareSession)]; that this video bypasses screen capture would explain these reports [inference].
- **How fast is `screencap`?** No measurement on TV hardware found. On an Android 15 emulator: median 2.1 s with `-p` (PNG), 0.68 s raw; physical devices "typically 600-900 ms" on the raw path [reported, [handsets.dev](https://handsets.dev/blog/why-adb-screencap-is-slow/), May 2026]. That is about 1 frame per second at best, before decoding a full-size raw frame.
- **Is there a faster ADB route?** scrcpy (H.264 screen stream over ADB) gives "a rock-solid 30fps" from an Nvidia Shield TV into HyperHDR [reported, [Hackaday.io](https://hackaday.io/project/205729-diy-ambilight-for-any-tv), 2026]. Glowcast would have to decode H.264 inside Home Assistant. `screenrecord --output-format=raw-frames` (no H.264) exists [verified: source] but nobody reports using it as a live feed.
- **Does it work on Chromecast with Google TV?** The Hyperion Android grabber app (installed on the device, not ADB) is reported to work on a Chromecast with Google TV [reported, [WLED forum](https://wled.discourse.group/t/wled-ambilight-hyperion/102?page=2)]; DRM apps are black [reported, [hyperion-android-reborn wiki](https://github.com/evanwhitt/hyperion-android-reborn/wiki)].
- **Google TV Streamer and ADB:** a user could not keep HA's ADB integration connected to a Google TV Streamer: Wireless Debugging dropped after seconds, cannot be enabled while Ethernet is connected, and was off after a reboot; they gave up [reported, [HA community](https://community.home-assistant.io/t/android-debug-bridge-google-tv-streamer-not-working/955714), Nov 2025 - Jun 2026]. On retail Android TV devices ADB over the network needs `adb tcpip 5555` first [verified: docs, [Google Cast Android TV debugging](https://developers.google.com/cast/docs/android_tv_receiver/debugging)].

## Part 2 - hands-on (postponed)


Hardware available in the test home besides the LG: a Sony BRAVIA with Google TV, Chromecast with Google TV units and a Google TV Streamer (connected through HA's `androidtv_remote` and `cast` integrations, no ADB integration). No Samsung or Philips TVs. If hands-on testing is picked up later: planned measurement, only with the owner's go and with developer mode switched on by the owner:

- From the development machine with Android platform-tools (no change to Home Assistant).
- 30 sequential frames each of `screencap -p`, raw `screencap` and `screenrecord --output-format=raw-frames --size 64x36`, with decoding to 64x36 included, as in #2.
- Check on a Netflix title, on Live TV and (Sony) on an HDMI input whether the frame shows the real picture.
- Note whether the device offers classic network debugging (port 5555) or only Wireless Debugging with a pairing code.

## Draft follow-up stories

Drafts only; the product owner decides which go on the backlog.

### Draft 1 - Story: use any Home Assistant camera entity as a source

**Labels:** `story`, `source` - **Milestone:** Later

```markdown
## User story
**As** a user whose TV has no local capture API (Samsung, Roku, or any other brand),
**I want** Glowcast to take its picture from a Home Assistant camera entity, such as a USB HDMI grabber,
**so that** I can use Glowcast with any TV.

## Context
Research #12 (`docs/research/other-tvs.md`): no other TV brand offers a usable local capture API. HA's `camera.async_get_image()` works for every camera platform, but only MJPEG-backed entities (a USB HDMI grabber through go2rtc, the Hyperion camera entity, a still-image URL with a raised framerate) are fast enough. H.264/RTSP cameras deliver one frame per keyframe interval. See architecture 3.2 and 8.2.

## Acceptance criteria
**Scenario: pick a camera**
- **Given** a camera entity in HA
- **When** I add Glowcast and choose "Camera" as the source
- **Then** I can pick the camera entity and continue with the Hue steps as for webOS

**Scenario: sync from a grabber**
- **Given** an MJPEG camera entity showing the TV picture
- **When** I switch Glowcast on
- **Then** the lights follow the picture at the chosen update rate, up to what the camera delivers

**Scenario: camera unavailable**
- **Given** the camera entity is unavailable or the snapshot times out 5 times in a row
- **When** Glowcast is syncing
- **Then** it stops, releases the lights and turns the switch off (stop reason `TV_OFF`)

## Out of scope
- Decoding RTSP streams ourselves.
- Perspective correction for a camera aimed at the screen.
- Setting up go2rtc or the grabber (documented in the README, done by the user).

## Notes for the developer
- `CameraFrameSource` in `sources/camera.py`, stored as a typed block (`{"type": "camera", ...}`). It references an entity, not a config entry, which is new next to architecture 5: store the entity registry's unique id, not the entity id.
- `camera.async_get_image(hass, entity_id, timeout=5)`; decode and resize to 64x36 in the executor.
- `is_on()`: the camera entity's state is not `unavailable`; there is no TV power state.
- README: which cameras are fast enough, and that Glowcast does not recommend HDCP-stripping splitters.

## Definition of Done
- [ ] All acceptance criteria met
- [ ] Pull request references this issue (`Closes #<n>`), reviewed and merged by the product owner
- [ ] CI green (`pytest`, `ruff check`, hassfest, HACS validation)
- [ ] No `homeassistant` imports in `core/`; no secrets, image URLs or frame contents in logs
- [ ] Verified on real hardware where hardware is involved
- [ ] README and `docs/ARCHITECTURE.md` updated if behaviour visible to users changed

## Depends on
- v0.1 done (#5, #6, #7)
```

### Draft 2 - Story: Android TV / Google TV source over ADB (not recommended; only if a hands-on test contradicts the reports above)

**Labels:** `story`, `source` - **Milestone:** Later

```markdown
## User story
**As** a user with an Android TV or Google TV (Sony, Philips, TCL, Hisense, Chromecast with Google TV),
**I want** Glowcast to capture the picture over ADB,
**so that** my lights follow the TV without extra hardware.

## Context
Research #12: ADB capture is local but needs developer mode and gives black frames for DRM apps. Online reports say ADB capture misses HDMI, tuner and DRM video on Android TV panels and reaches about 1 frame per second at best; this story only makes sense if a hands-on test shows otherwise: <fill in: frames per second, method, HDMI and Live TV behaviour>. HA's `androidtv` integration stores an authorised ADB key in `.storage/androidtv_adbkey`.

## Acceptance criteria
**Scenario: pick an Android TV**
- **Given** an Android TV set up in HA with the Android Debug Bridge (`androidtv`) integration
- **When** I add Glowcast and choose that TV
- **Then** Glowcast connects without a second prompt on the TV

**Scenario: sync**
- **Given** the TV shows a non-protected picture
- **When** I switch Glowcast on
- **Then** the lights follow it at <measured rate> per second or the chosen update rate, whichever is lower

**Scenario: DRM content**
- **Given** a DRM app shows a black frame
- **When** Glowcast is syncing
- **Then** the lights show the minimal glow, and the README explains why

## Out of scope
- Wireless Debugging pairing (pairing codes); only devices that the `androidtv` integration can connect to.
- Apps installed on the TV (Hyperion grabbers).

## Notes for the developer
- Capture method and rate from part 2 of #12.
- Reuse the `androidtv` entry by unique id and its ADB key; open Glowcast's own connection. Check that two ADB connections coexist.
- `adb-shell` is effectively unmaintained; pin the same version as HA core.

## Definition of Done
- [ ] All acceptance criteria met
- [ ] Pull request references this issue (`Closes #<n>`), reviewed and merged by the product owner
- [ ] CI green (`pytest`, `ruff check`, hassfest, HACS validation)
- [ ] No `homeassistant` imports in `core/`; no secrets, image URLs or frame contents in logs
- [ ] Verified on real hardware where hardware is involved
- [ ] README and `docs/ARCHITECTURE.md` updated if behaviour visible to users changed

## Depends on
- A hands-on test showing at least 1 frame per second of real video picture
- v0.1 done
```

### Draft 3 - Story: Philips Ambilight TVs as a source (after the product question)

**Labels:** `story`, `source` - **Milestone:** Later

```markdown
## User story
**As** a user with a Philips Ambilight TV,
**I want** Glowcast to use the colours my TV already measures,
**so that** my Hue lights follow the picture, including streaming apps.

## Context
Research #12: JointSpace `ambilight/measured` gives the edge colours the TV computes, locally; no measured rate, community projects poll at 8-15 Hz (reported). HA's `philips_js` stores the credentials Glowcast can reuse. Not a picture: decide option A, B or C from the research document first.

## Acceptance criteria
**Scenario: pick a Philips TV**
- **Given** a Philips TV set up in HA with the Philips TV integration
- **When** I add Glowcast and choose that TV
- **Then** no extra pairing is needed

**Scenario: sync**
- **Given** the TV shows a picture (also from a streaming app or HDMI)
- **When** I switch Glowcast on
- **Then** the lights follow the TV's measured edge colours at the chosen update rate

## Out of scope
- Philips TVs without Ambilight.
- Controlling the TV's own Ambilight.

## Notes for the developer
- Reuse the `philips_js` entry by unique id (`host`, `api_version`, `username`, `password`), with HA's session.
- Verify on a real TV, including Titan OS, before building.

## Definition of Done
- [ ] All acceptance criteria met
- [ ] Pull request references this issue (`Closes #<n>`), reviewed and merged by the product owner
- [ ] CI green (`pytest`, `ruff check`, hassfest, HACS validation)
- [ ] No `homeassistant` imports in `core/`; no secrets, image URLs or frame contents in logs
- [ ] Verified on real hardware where hardware is involved
- [ ] README and `docs/ARCHITECTURE.md` updated if behaviour visible to users changed

## Depends on
- Product decision on option A, B or C
- Access to a Philips Ambilight TV for testing
- v0.1 done
```

### Not drafted in full: LG Netcast and Enigma2

Both are feasible (local, pairing reuse, no developer mode) but serve small audiences: 2012-2013 LG TVs and Enigma2 set-top boxes. If someone asks, the story is the same shape as Draft 1 with the source call from the sections above, and needs a test device.
