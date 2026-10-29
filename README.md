<h1>
  <!-- GitHub ends this comment here, HACS only at the end of the next line, so HACS shows just the icon below --!><picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/nicohabets/glowcast/main/custom_components/glowcast/brand/dark_logo.png">
    <source media="(prefers-color-scheme: light)" srcset="https://raw.githubusercontent.com/nicohabets/glowcast/main/custom_components/glowcast/brand/logo.png"><!-- -->
    <img src="https://raw.githubusercontent.com/nicohabets/glowcast/main/custom_components/glowcast/brand/icon.png" alt="Glowcast" height="100">
  <!-- --!></picture><!-- -->
</h1>

**Let your Philips Hue lights follow what's on your LG webOS TV - no subscription, no HDMI box, nothing leaves your home.**

<img width="400" alt="An illustrated living room: the picture on the TV changes colour and the light strip and lamps follow it." src="https://raw.githubusercontent.com/nicohabets/glowcast/main/docs/images/illustrations/scene-1-picture-leads-loop.webp">

*The picture changes; a moment later the lights around the TV and in the room follow it.*

<!-- PHOTO OF THE LIGHTS (#87): the product owner adds a photo or short animation here of a TV with the lights following the picture, as docs/images/lights.jpg (or .gif / .webp, under about 5 MB), in the same form as the images below: an img with width="400", alt text and an absolute raw.githubusercontent.com URL, then a one-line caption in italics. -->

Glowcast is a [Home Assistant](https://www.home-assistant.io/) custom integration, installable through [HACS](https://hacs.xyz/). It grabs frames from your LG TV over its local network API, works out the average colour of each part of the screen (left, right, top, bottom) and sends those colours to your Philips Hue lights, including gradient lightstrips segment by segment. Support for other lights and other TVs is planned.

> **Status: v0.1.1 released - an early version.** Tested on an LG OLED65G36LA (2023, G3) with a Hue Bridge Pro and a gradient TV lightstrip. See the [release notes](https://github.com/nicohabets/glowcast/blob/main/docs/releases/v0.1.1.md) and [docs/ARCHITECTURE.md](https://github.com/nicohabets/glowcast/blob/main/docs/ARCHITECTURE.md) for the design. Expect breaking changes until v1.0.

## Why

At the time of writing (2026), Philips' own Hue Sync TV app only runs on LG TVs from 2024 onwards (webOS 24+), and costs €129.99 once or €2.99 a month. The alternative is the Hue Play HDMI Sync Box of around €300. If you already run Home Assistant with the LG webOS and Philips Hue integrations, you have everything you need.

## How it works

```
LG TV (webOS)  --local, port 3001-->  Glowcast (inside Home Assistant)  --local-->  Hue Bridge  -->  your lights
```

1. Glowcast reuses the pairing of Home Assistant's existing **LG webOS Smart TV** integration, so there is no extra pairing prompt on the TV.
2. It asks the TV for a screen capture through its local network API and downloads the small JPEG the TV serves on your LAN, about 3 times per second.
3. Each light gets the average colour of the part of the screen that matches its position, ignoring black bars.
4. The colours go to the Hue Bridge: Glowcast streams to an entertainment area you set up in the Hue app, up to 25 times per second, fading smoothly between frames. Lights use the positions you gave them in the Hue app, left/right and floor/ceiling. A gradient lightstrip shows several colours along its length.

Everything stays on your local network.

<img width="400" alt="The same room with a dark picture on the TV: the lights keep a faint warm glow instead of going off." src="https://raw.githubusercontent.com/nicohabets/glowcast/main/docs/images/illustrations/scene-2-dark-scene-800.png">

*When the picture is dark, the lights keep a faint warm glow instead of going off.*

<img width="400" alt="The same room with a red picture on the TV: the light strip and lamps show the same pure red." src="https://raw.githubusercontent.com/nicohabets/glowcast/main/docs/images/illustrations/scene-3-saturated-colour-800.png">

*A strong colour on the screen shows as that pure colour on the lights, not mixed with white.*

<img width="400" alt="The same room with extra lamps beside the sofa, each following its own side of the picture." src="https://raw.githubusercontent.com/nicohabets/glowcast/main/docs/images/illustrations/scene-4-loose-lights-800.png">

*Coming in v0.3 ([#76](https://github.com/nicohabets/glowcast/issues/76)): lamps outside the entertainment area will each follow their own side of the picture; today only the lights in the entertainment area follow it.*

## Requirements

- Home Assistant 2025.7.2 or newer
- An LG TV running webOS, set up in Home Assistant with the **LG webOS Smart TV** integration
- A Philips Hue Bridge v2 or Hue Bridge Pro with colour lights, set up in Home Assistant with the **Philips Hue** integration. The original round bridge (sold 2012-2015) cannot stream.
- An entertainment area set up in the Hue app (Hue app → Settings → Entertainment areas)
- [HACS](https://hacs.xyz/)

## Installation

[![Open Glowcast in HACS or the Marketplace in your Home Assistant.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=nicohabets&repository=glowcast&category=integration)

1. Open **HACS**, search for **Glowcast** and open it. Or click the button above: it opens Glowcast in HACS (from Home Assistant 2026.11: the Marketplace) in your own Home Assistant.
2. Click **Download** (bottom right), then **Download** again in the dialog. The newest version is selected.
3. Restart Home Assistant (Settings → System → the restart button in the top right, or the repair HACS shows).
4. Go to **Settings → Devices & services → Add integration → Glowcast**.

<img width="400" alt="The Glowcast setup flow in Home Assistant: choose the TV, choose the Hue bridge, press the link button and wait for the pairing, choose the entertainment area, done." src="https://raw.githubusercontent.com/nicohabets/glowcast/main/docs/images/setup-flow.gif">

*Setting up Glowcast: the TV, the Hue bridge and its link button, then the entertainment area.*

**HACS does not find Glowcast?** Add it as a custom repository: in HACS, open the menu (⋮) in the top right corner → **Custom repositories**, enter `https://github.com/nicohabets/glowcast`, choose type **Integration** and click **Add**. Close the dialog and search again.

Updates show up in HACS and under Settings → System → Updates.

From Home Assistant 2026.11, HACS is built into Home Assistant as the [Marketplace](https://hacs.xyz/docs/marketplace/) (Settings → Marketplace); custom repositories added in HACS carry over. Updated steps follow in [#57](https://github.com/nicohabets/glowcast/issues/57).

## Configuration

During setup you pick:

1. **What the lights follow** - **LG webOS TV**, then one of your LG webOS TVs in Home Assistant (or **Computer screen**, see below)
2. **Hue Bridge** - and press its link button when asked, then click Submit within 30 seconds. Streaming needs a key that Home Assistant's own Hue connection does not have, so Glowcast pairs once itself. It shows up in the Hue app as an app called "glowcast".
3. **Entertainment area** - set up in the Hue app; it decides which lights take part and where they stand

Each TV can have one Glowcast entry. If the bridge later stops accepting Glowcast's key (for example because you removed the app in the Hue app), Home Assistant asks you to press the link button again; your settings are kept.

Under **Configure**, or on Glowcast's device page (see [Settings on the device page](#settings-on-the-device-page)), you can change the area, brightness (1-100 %, default 80 %), intensity (how vivid the colours are, 0-200 %, default 100 %) and the update rate: Low (1 per second), Medium (2), High (3, default) or Maximum (as fast as the TV allows). The lights always fade smoothly between updates. The TV and bridge are fixed; to change them, remove Glowcast and add it again.

<img width="400" alt="Glowcast's settings screen in Home Assistant: entertainment area TV area, brightness 80 %, intensity 100 %, update rate High (3 per second)." src="https://raw.githubusercontent.com/nicohabets/glowcast/main/docs/images/settings.png">

*The settings screen under Configure, with the default settings.*

### A computer screen (in development)

Glowcast can also follow the screen of a PC or Mac, through a small screen helper on that computer. For now the helper is a developer tool that you start from a clone of this repository; see [the screen helper](https://github.com/nicohabets/glowcast/blob/main/screen_helper/README.md).

1. Start the helper on the computer. It shows a 6-digit pairing code.
2. Home Assistant finds it on your network by itself: **Settings → Devices & services** shows **Discovered: Glowcast - *computer name***. Click **Add**.
3. Enter the pairing code, then pick the Hue bridge and the entertainment area, as for a TV.

If the helper is not discovered (for example when Home Assistant and the computer are on different networks), add it by hand: **Add integration → Glowcast → Computer screen**, enter the computer's IP address or host name and the helper's port (8488 unless you started it with another one), then the pairing code. Each computer can have one Glowcast entry; when it gets a new address, Home Assistant finds it again and updates the address. If the helper no longer accepts Glowcast's key (for example after `--unpair`), Home Assistant asks for a new pairing code; your settings are kept. A screen is followed at up to 4 pictures per second.

## Starting and stopping

<img width="400" alt="Glowcast's device page in Home Assistant while syncing: the switch on under Controls, the status Streaming under Sensors, and the settings under Configuration." src="https://raw.githubusercontent.com/nicohabets/glowcast/main/docs/images/device-page.png">

*Glowcast's device page: the switch under Controls, the status under Sensors, the settings under Configuration.*

Glowcast adds one device, "Glowcast *TV name*", with a **switch** under Controls (and its settings under Configuration), for example `switch.glowcast_living_room_tv`. Turn it on to start syncing and off to stop; when it stops, the Hue bridge restores the lights (tested with the Hue app's default "when syncing stops" setting). Glowcast never starts by itself: the switch is off after a restart of Home Assistant. Start it from a dashboard, by voice, or with an automation, for example when the TV turns on. The ready-made blueprint "Start Glowcast when the TV turns on" asks for the TV and the Glowcast switch, and can also turn the switch off when the TV turns off:

[![Open your Home Assistant instance and show the blueprint import dialog with a specific blueprint pre-filled.](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fraw.githubusercontent.com%2Fnicohabets%2Fglowcast%2Fmain%2Fblueprints%2Fautomation%2Fglowcast%2Fstart_with_tv.yaml)

Or write the automation yourself:

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

Glowcast waits up to 10 seconds for the TV to show a picture, so it can start the moment the TV reports "on". Brightness, intensity and the update rate change while it runs without interrupting the lights. Choosing another entertainment area restarts syncing on the new area (a few seconds); the switch stays on.

**When it stops by itself**, the switch turns off, the [status](#seeing-what-glowcast-does) shows TV off or Error, and the reason is in the Home Assistant log:

- **The TV turns off** (or is not reachable): the lights are released. When you switch the TV off with the remote, the lights go straight from the picture's colours to the state the bridge restores.
- **Another app is streaming** to the bridge (the Hue Sync app, a Sync Box, another Glowcast): Glowcast does not take over and does not start.
- **The light stream is lost** (bridge restarted, another app took over): one retry after 5 seconds; if that does not work, Glowcast stops. The next switch-on works once the bridge is back.
- **The bridge rejects Glowcast's key** (for example after removing the app in the Hue app): Home Assistant asks you to press the bridge's link button again; your settings are kept.
- **The entertainment area was removed, or has more than 20 channels**: Home Assistant shows a repair with what to change in Glowcast's settings or in the Hue app.
- **The TV or the bridge is no longer set up in Home Assistant**: Home Assistant shows a repair saying which integration to set up again.

When Home Assistant shuts down or restarts while syncing, Glowcast releases the lights first.

On a black picture the lights keep a faint warm glow instead of going dark. Dark colours blend into that glow; brighter colours are shown pure, without the glow mixed in.

## Settings on the device page

The settings are also entities on Glowcast's device, under **Configuration**: **Brightness** and **Intensity** (sliders, in %), **Update rate** (Low, Medium, High, Maximum) and **Entertainment area** (the bridge's areas by name). Change them on the device page, from a dashboard, by voice or with an automation; the settings screen under **Configure** shows the same values. While syncing, a new brightness reaches the lights at once; a new intensity and update rate apply from the next capture (the lights then fade to the new colours as usual), without a restart. Only a new entertainment area restarts syncing.

For example, softer lights late in the evening:

```yaml
triggers:
  - trigger: time
    at: "22:00:00"
actions:
  - action: number.set_value
    target:
      entity_id: number.glowcast_living_room_tv_brightness
    data:
      value: 40
```

The entity ids follow Home Assistant's language when the entities are created (in Dutch, for example, `number.glowcast_living_room_tv_helderheid`). If the bridge cannot be reached, the entertainment area keeps showing the area Glowcast uses; the list of areas is read again once the bridge is back, or when you open the settings screen.

## Seeing what Glowcast does

The **Status** sensor on Glowcast's device (for example `sensor.glowcast_living_room_tv_status`) shows what it is doing:

| Status | Meaning |
|---|---|
| Idle | Not syncing: never started since Home Assistant started, or switched off. Also when the TV or the bridge is not set up in Home Assistant, so syncing cannot start; a repair says which. |
| Starting | Waiting for the TV's picture (up to 10 seconds) and claiming the lights, also during the retry after a lost light stream. |
| Streaming | The lights follow the picture. |
| Black picture | Syncing, but the picture is black (a dark scene): the lights keep the last colours for a moment, then show the faint glow. |
| No picture | The TV gives no picture (for example a streaming app with DRM, see [Known limits](#known-limits)): the lights are released; Glowcast stays on and follows again when the picture returns. Before this, the status shows Black picture for about 3 seconds. |
| TV off | Stopped because the TV turned off or could not be reached. |
| Error | Stopped for another reason; Home Assistant shows a repair or asks to pair again where you can fix it, and the log says why. |

For TV off and Error, the attribute **Stop reason** (`stop_reason`) says why, as a fixed key: `tv_off`, `output_lost` (the light stream was lost), `output_busy` (another app is streaming), `output_not_found` (the entertainment area no longer exists), `output_too_many_channels`, `output_unavailable` (the bridge could not be reached), `auth_failed` (the bridge rejected Glowcast's key), `source_unavailable` (the TV source failed) or `source_auth_failed` (the computer's screen helper rejected Glowcast's key; Home Assistant asks for a new pairing code). Use it in automations, for example to get a notification when Glowcast stops with an error.

The **Capture rate** sensor shows how many pictures per second Glowcast gets from the TV while it streams (about 3-4 on a 2023 LG G3 at Maximum). It is a diagnostic sensor and off by default: on the device page, under **Diagnostic**, open **+1 disabled entity**, then **Capture rate**, its settings (the gear), and turn on **Enabled**. Home Assistant adds it after about 30 seconds. It is unknown when Glowcast is not streaming or has not measured yet. In automations and templates the unit is always `captures/s`; the screens show it in your language.

## Known limits

- **About 3 captures per second.** That is the TV's capture limit, so the lights follow scenes, not every flash or cut. It works well as ambient light, but it is not as fast as the HDMI Sync Box.
- **While syncing**, the lights in the entertainment area only listen to Glowcast. The Hue app and Home Assistant get them back when syncing stops.
- **One stream per bridge at a time** (as far as known; being checked for the Bridge Pro, #37). A Hue bridge streams to one entertainment area at a time, so two Glowcast entries (two TVs) on one bridge cannot sync together.
- **Choosing another entertainment area while syncing** briefly restarts the light stream (a few seconds). Brightness, intensity and the update rate change without a restart.
- **TV lightstrip segments sample about 30 % in from the picture's edges**, not the edges themselves, because of where the Hue app places them. Improving this is planned (#23).
- **Streaming apps with DRM** in the TV's own apps return a black frame (tested with Prime Video), so Glowcast cannot follow them. After 3 s without a picture it lets go of the lights, so they return to how they were before syncing; Glowcast stays on and follows the picture again as soon as there is one. The same app on a streaming stick over HDMI works. Other apps and copy-protected HDMI sources are not tested yet.
- **The TV's capture call is undocumented** by LG and could stop working after a TV firmware update.
- **Glowcast's icon** in Settings → Devices & services only shows on Home Assistant 2026.3 or newer; older versions show no icon for Glowcast.
- **Tested on one TV model so far**, a 2023 LG G3. Other webOS TVs should work the same way, but that is not confirmed yet.

**Tried it on your TV?** Please tell us how it went, also when it just works: [open a hardware report](https://github.com/nicohabets/glowcast/issues/new?template=hardware-report.md) with your TV model, webOS version, bridge and how many captures per second you get (the [Capture rate](#seeing-what-glowcast-does) sensor shows it).

## Reporting a problem

Open an issue on [GitHub](https://github.com/nicohabets/glowcast/issues) and attach Glowcast's diagnostics: **Settings → Devices & services → Glowcast → ⋮ → Download diagnostics**. The download shows the versions, your settings, the TV's and bridge's model and software version, and what Glowcast was doing last (also after it stopped, with the reason). Keys, addresses, ids, serial numbers and names are left out, but have a look before you post it: issues on GitHub are public.

## Privacy

Glowcast pairs with your Hue Bridge once and stores that key in Home Assistant. The key gives full access to the bridge, like any app you connect to it. Removing Glowcast from Home Assistant does not remove its app from the bridge (the bridge does not allow that), so Home Assistant shows a notification with these steps when you remove Glowcast. To remove it from the bridge: in the Hue app, go to **Settings → Apps → Network-based** (Dutch: Instellingen → Toepassingen → Netwerk-gebaseerd), tap the info button next to **glowcast** and remove it.

The TV's capture call only works on your local network, after the TV pairing you already approved for Home Assistant. Frames are kept in memory and never stored or sent anywhere. Separately: if privacy matters to you, consider turning off **Live Plus** (LG's ACR feature) in your TV settings. That one does send viewing data to LG.

## Disclaimer

Glowcast is an independent project. It is not affiliated with, endorsed by or connected to Signify (Philips Hue) or LG Electronics. All product names are trademarks of their respective owners.

## License

[MIT](https://github.com/nicohabets/glowcast/blob/main/LICENSE)
