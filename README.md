# Glowcast

**Let your Philips Hue lights follow what's on your LG webOS TV - no subscription, no HDMI box, nothing leaves your home.**

Glowcast is a [Home Assistant](https://www.home-assistant.io/) custom integration, installable through [HACS](https://hacs.xyz/). It grabs frames from your LG TV over its local network API, works out the average colour of each part of the screen (left, right, top, bottom) and sends those colours to your Philips Hue lights, including gradient lightstrips segment by segment. Support for other lights and other TVs is planned.

> **Status: design phase.** The capture path has been tested on an LG OLED65G36LA (2023, G3); the integration itself is not built yet. See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the design of v0.1. Expect breaking changes until v1.0.

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
- A Philips Hue Bridge with colour lights, set up in Home Assistant with the **Philips Hue** integration
- An entertainment area set up in the Hue app (Hue app → Settings → Entertainment areas)
- [HACS](https://hacs.xyz/)

## Installation

> Not installable yet - the first release is coming. These are the steps once it is.

1. In HACS, open the menu (⋮) → **Custom repositories**.
2. Add `https://github.com/nicohabets/glowcast` with type **Integration**.
3. Install **Glowcast** and restart Home Assistant.
4. Go to **Settings → Devices & services → Add integration → Glowcast**.

## Configuration (planned)

During setup you pick:

1. **TV** - one of your LG webOS TVs in Home Assistant
2. **Hue Bridge** - and press its link button once. Streaming needs a key that Home Assistant's own Hue connection does not have, so Glowcast pairs once itself.
3. **Entertainment area** - set up in the Hue app; it decides which lights take part and where they stand

Under the integration's options you can change the area, brightness, intensity (how vivid the colours are) and the update rate: Low (1 per second), Medium (2), High (3) or Maximum (as fast as the TV allows). The lights always fade smoothly between updates.

Glowcast creates one **switch** to start and stop syncing. It never starts by itself, so start it from a dashboard or an automation, for example when the TV turns on:

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

When the TV turns off, syncing stops and the lights are released. If another app (the Hue Sync app or a Sync Box) is already streaming to the area, Glowcast does not take over; it tells you instead. On a black picture the lights keep a faint glow instead of going dark.

## Known limits

- **About 3 frames per second.** That is the TV's capture limit. It works well as ambient light, but it is not as fast as the HDMI Sync Box.
- **While syncing**, the lights in the entertainment area only listen to Glowcast. The Hue app and Home Assistant get them back when syncing stops.
- **Streaming apps with DRM** (Netflix and similar) and copy-protected HDMI sources may return a black frame. Not tested yet.
- **The TV's capture call is undocumented** by LG and could stop working after a TV firmware update.
- Tested on a 2023 LG G3 so far. Reports from other models are welcome. Please open an issue with your model and webOS version.

## Privacy

Glowcast pairs with your Hue Bridge once and stores that key in Home Assistant. The key gives full access to the bridge, like any app you connect to it. After removing Glowcast, you can also remove it from the bridge: in the Hue app, look for "glowcast" in the list of connected apps (or in your Hue account) and delete it.


The TV's capture call only works on your local network, after the TV pairing you already approved for Home Assistant. Frames are kept in memory and never stored or sent anywhere. Separately: if privacy matters to you, consider turning off **Live Plus** (LG's ACR feature) in your TV settings. That one does send viewing data to LG.

## Disclaimer

Glowcast is an independent project. It is not affiliated with, endorsed by or connected to Signify (Philips Hue) or LG Electronics. All product names are trademarks of their respective owners.

## License

[MIT](LICENSE)
