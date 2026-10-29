<h1>
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="custom_components/glowcast/brand/dark_logo.png">
    <img src="custom_components/glowcast/brand/logo.png" alt="Glowcast" width="420">
  </picture>
</h1>

**Let your Philips Hue lights follow what's on your LG webOS TV - no subscription, no HDMI box, nothing leaves your home.**

Glowcast is a [Home Assistant](https://www.home-assistant.io/) custom integration, installable through [HACS](https://hacs.xyz/). It grabs frames from your LG TV over its local network API, works out the average colour of each part of the screen (left, right, top, bottom) and sends those colours to your Philips Hue lights, including gradient lightstrips segment by segment. Support for other lights and other TVs is planned.

> **Status: v0.1 released - an early version.** Tested on an LG OLED65G36LA (2023, G3) with a Hue Bridge Pro and a gradient TV lightstrip. See the [release notes](docs/releases/v0.1.0.md) and [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the design. Expect breaking changes until v1.0.

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

## Requirements

- Home Assistant 2025.7.2 or newer
- An LG TV running webOS, set up in Home Assistant with the **LG webOS Smart TV** integration
- A Philips Hue Bridge v2 or Hue Bridge Pro with colour lights, set up in Home Assistant with the **Philips Hue** integration. The original round bridge cannot stream.
- An entertainment area set up in the Hue app (Hue app → Settings → Entertainment areas)
- [HACS](https://hacs.xyz/)

## Installation

Glowcast is not in the default HACS list yet; add it as a custom repository:

1. Open **HACS**, then the menu (⋮) in the top right corner → **Custom repositories**.
2. Enter `https://github.com/nicohabets/glowcast` as the repository, choose type **Integration** and click **Add**. Close the dialog.
3. Search for **Glowcast**, open it and click **Download** (bottom right), then **Download** again in the dialog. The newest version is selected.
4. Restart Home Assistant (Settings → System → the restart button in the top right, or the repair HACS shows).
5. Go to **Settings → Devices & services → Add integration → Glowcast**.

Updates show up in HACS and under Settings → System → Updates.

From Home Assistant 2026.11, HACS is built into Home Assistant as the [Marketplace](https://hacs.xyz/docs/marketplace/) (Settings → Marketplace); custom repositories added in HACS carry over. Updated steps follow in [#57](https://github.com/nicohabets/glowcast/issues/57).

## Configuration

During setup you pick:

1. **TV** - one of your LG webOS TVs in Home Assistant
2. **Hue Bridge** - and press its link button when asked, then click Submit within 30 seconds. Streaming needs a key that Home Assistant's own Hue connection does not have, so Glowcast pairs once itself. It shows up in the Hue app as an app called "glowcast".
3. **Entertainment area** - set up in the Hue app; it decides which lights take part and where they stand

Each TV can have one Glowcast entry. If the bridge later stops accepting Glowcast's key (for example because you removed the app in the Hue app), Home Assistant asks you to press the link button again; your settings are kept.

Under **Configure** you can change the area, brightness (1-100 %, default 80 %), intensity (how vivid the colours are, 0-200 %, default 100 %) and the update rate: Low (1 per second), Medium (2), High (3, default) or Maximum (as fast as the TV allows). The lights always fade smoothly between updates. The TV and bridge are fixed; to change them, remove Glowcast and add it again.

## Starting and stopping

Glowcast adds one device, "Glowcast *TV name*", with one **switch**, for example `switch.glowcast_living_room_tv`. Turn it on to start syncing and off to stop; when it stops, the Hue bridge restores the lights (tested with the Hue app's default "when syncing stops" setting). Glowcast never starts by itself: the switch is off after a restart of Home Assistant. Start it from a dashboard, by voice, or with an automation, for example when the TV turns on:

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

Glowcast waits up to 10 seconds for the TV to show a picture, so it can start the moment the TV reports "on". Changing the settings while it runs restarts syncing with the new settings; the switch stays on.

**When it stops by itself**, the switch turns off and the reason is in the Home Assistant log:

- **The TV turns off** (or is not reachable): the lights are released. When you switch the TV off with the remote, the lights go straight from the picture's colours to the state the bridge restores.
- **Another app is streaming** to the bridge (the Hue Sync app, a Sync Box, another Glowcast): Glowcast does not take over and does not start.
- **The light stream is lost** (bridge restarted, another app took over): one retry after 5 seconds; if that does not work, Glowcast stops. The next switch-on works once the bridge is back.
- **The bridge rejects Glowcast's key** (for example after removing the app in the Hue app): Home Assistant asks you to press the bridge's link button again; your settings are kept.
- **The entertainment area was removed, or has more than 20 channels**: Home Assistant shows a repair with what to change in Glowcast's settings or in the Hue app.
- **The TV or the bridge is no longer set up in Home Assistant**: Home Assistant shows a repair saying which integration to set up again.

When Home Assistant shuts down or restarts while syncing, Glowcast releases the lights first.

On a black picture the lights keep a faint warm glow instead of going dark. Dark colours blend into that glow; brighter colours are shown pure, without the glow mixed in.

## Known limits

- **About 3 frames per second.** That is the TV's capture limit. It works well as ambient light, but it is not as fast as the HDMI Sync Box.
- **While syncing**, the lights in the entertainment area only listen to Glowcast. The Hue app and Home Assistant get them back when syncing stops.
- **One stream per bridge at a time** (as far as known; being checked for the Bridge Pro, #37). A Hue bridge streams to one entertainment area at a time, so two Glowcast entries (two TVs) on one bridge cannot sync together.
- **Changing a setting while syncing** briefly restarts the light stream (a few seconds).
- **TV lightstrip segments sample about 30 % in from the picture's edges**, not the edges themselves, because of where the Hue app places them. Improving this is planned (#23).
- **Streaming apps with DRM** in the TV's own apps return a black frame (tested with Prime Video), so the lights show only the minimal glow. The same app on a streaming stick over HDMI works. Other apps and copy-protected HDMI sources are not tested yet.
- **The TV's capture call is undocumented** by LG and could stop working after a TV firmware update.
- **Glowcast's icon** in Settings → Devices & services only shows on Home Assistant 2026.3 or newer; older versions show no icon for Glowcast.
- Tested on a 2023 LG G3 so far. Reports from other models are welcome. Please open an issue with your model and webOS version.

## Privacy

Glowcast pairs with your Hue Bridge once and stores that key in Home Assistant. The key gives full access to the bridge, like any app you connect to it. Removing Glowcast from Home Assistant does not remove its app from the bridge (the bridge does not allow that), so Home Assistant shows a notification with these steps when you remove Glowcast. To remove it from the bridge: in the Hue app, go to **Settings → Apps → Network-based** (Dutch: Instellingen → Toepassingen → Netwerk-gebaseerd), tap the info button next to **glowcast** and remove it.

The TV's capture call only works on your local network, after the TV pairing you already approved for Home Assistant. Frames are kept in memory and never stored or sent anywhere. Separately: if privacy matters to you, consider turning off **Live Plus** (LG's ACR feature) in your TV settings. That one does send viewing data to LG.

## Disclaimer

Glowcast is an independent project. It is not affiliated with, endorsed by or connected to Signify (Philips Hue) or LG Electronics. All product names are trademarks of their respective owners.

## License

[MIT](LICENSE)
