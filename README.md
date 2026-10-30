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

Glowcast is a [Home Assistant](https://www.home-assistant.io/) custom integration, installable through [HACS](https://hacs.xyz/). It grabs frames from your LG TV over its local network API, works out the average colour of each part of the screen (left, right, top, bottom) and sends those colours to your Philips Hue lights, including gradient lightstrips segment by segment. Support for other lights and other TVs is planned.

**The full documentation is on Glowcast's website: [nicohabets.github.io/glowcast](https://nicohabets.github.io/glowcast/).** This README is the short version.

> **Status: v0.2.0 released - an early version.** Tested on an LG OLED65G36LA (2023, G3) with a Hue Bridge Pro and a gradient TV lightstrip. See the [release notes](https://github.com/nicohabets/glowcast/blob/main/docs/releases/v0.2.0.md) and [docs/ARCHITECTURE.md](https://github.com/nicohabets/glowcast/blob/main/docs/ARCHITECTURE.md) for the design. Expect breaking changes until v1.0.

## How it works

```
LG TV (webOS)  --local, port 3001-->  Glowcast (inside Home Assistant)  --local-->  Hue Bridge  -->  your lights
```

Glowcast reuses the pairing of Home Assistant's **LG webOS Smart TV** integration (no extra prompt on the TV) and asks the TV for a small screen capture about 3 times per second. Each light gets the average colour of the part of the screen that matches its position, ignoring black bars, and the colours stream to an entertainment area on your Hue Bridge, fading smoothly between frames; a gradient lightstrip shows several colours along its length. On a black picture the lights keep a faint warm glow instead of going dark. Everything stays on your local network.

At the time of writing (2026), Philips' own Hue Sync TV app only runs on LG TVs from 2024 onwards (webOS 24+), and costs €129.99 once or €2.99 a month. The alternative is the Hue Play HDMI Sync Box of around €300. If you already run Home Assistant with the LG webOS and Philips Hue integrations, you have everything you need.

More: [how it works](https://nicohabets.github.io/glowcast/docs/), with illustrations of [what the lights do](https://nicohabets.github.io/glowcast/).

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

<img width="400" alt="The Glowcast setup flow in Home Assistant: choose the TV, choose Hue entertainment area, choose the Hue bridge, press the link button and wait for the pairing, choose the entertainment area, done." src="https://raw.githubusercontent.com/nicohabets/glowcast/main/docs/images/setup-flow.gif">

*Setting up Glowcast: the TV, Hue entertainment area, the Hue bridge and its link button, then the entertainment area.*

**HACS does not find Glowcast?** Add it as a custom repository: in HACS, open the menu (⋮) in the top right corner → **Custom repositories**, enter `https://github.com/nicohabets/glowcast`, choose type **Integration** and click **Add**. Close the dialog and search again.

Updates show up in HACS and under Settings → System → Updates.

From Home Assistant 2026.11, HACS is built into Home Assistant as the [Marketplace](https://hacs.xyz/docs/marketplace/) (Settings → Marketplace); custom repositories added in HACS carry over. Updated steps follow in [#57](https://github.com/nicohabets/glowcast/issues/57).

More: [installation](https://nicohabets.github.io/glowcast/docs/installation/), and [uninstalling](https://nicohabets.github.io/glowcast/docs/uninstalling/).

## Configuration

During setup you pick:

1. **What the lights follow** - **LG webOS TV**, then one of your LG webOS TVs in Home Assistant (or **Computer screen**, see [A computer screen](https://nicohabets.github.io/glowcast/docs/computer-screen/))
2. **Hue Bridge** - and press its link button when asked, then click Submit within 30 seconds. Streaming needs a key that Home Assistant's own Hue connection does not have, so Glowcast pairs once itself. It shows up in the Hue app as an app called "glowcast".
3. **Entertainment area** - set up in the Hue app; it decides which lights take part and where they stand

Each TV can have one Glowcast entry. If the bridge later stops accepting Glowcast's key (for example because you removed the app in the Hue app), Home Assistant asks you to press the link button again; your settings are kept.

Under **Configure**, or on Glowcast's device page (see [Settings](https://nicohabets.github.io/glowcast/docs/settings/#settings-on-the-device-page)), you can change the area, brightness (1-100 %, default 80 %), intensity (how vivid the colours are, 0-200 %, default 100 %) and the update rate: Low (1 per second), Medium (2), High (3, default) or Maximum (as fast as the TV allows). The lights always fade smoothly between updates. The TV and bridge are fixed; to change them, remove Glowcast and add it again.

<img width="400" alt="The last page of Glowcast's settings screen in Home Assistant: a note that the same settings are on the device page under Configuration, then brightness 80 %, intensity 100 %, update rate High (3 per second)." src="https://raw.githubusercontent.com/nicohabets/glowcast/main/docs/images/settings.png">

*The last page of the settings screen under Configure, after the lights, with the default settings.*

More: [setup](https://nicohabets.github.io/glowcast/docs/setup/), [settings](https://nicohabets.github.io/glowcast/docs/settings/) (also as entities on the device page, to change from an automation), and [a computer screen](https://nicohabets.github.io/glowcast/docs/computer-screen/) as the picture (a preview).

## Starting and stopping

Glowcast adds one device, "Glowcast *TV name*", with a **switch** (for example `switch.glowcast_living_room_tv`): turn it on to start syncing and off to stop. It never starts by itself; the switch is off after a restart of Home Assistant. The ready-made blueprint "Start Glowcast when the TV turns on" asks for the TV and the Glowcast switch, and can also turn the switch off when the TV turns off:

[![Open your Home Assistant instance and show the blueprint import dialog with a specific blueprint pre-filled.](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fraw.githubusercontent.com%2Fnicohabets%2Fglowcast%2Fmain%2Fblueprints%2Fautomation%2Fglowcast%2Fstart_with_tv.yaml)

When syncing stops, what the lights do follows the entertainment area's "When syncing stops" setting in the Hue app; choose "Last on-state" to get them back as they were. Glowcast also stops by itself when the picture source turns off (the TV, or a computer's screen), when another app streams to the bridge, or when the light stream is lost: the switch turns off, the [status](#seeing-what-glowcast-does) says why, and where you can fix it, Home Assistant shows a repair.

More: [starting automatically](https://nicohabets.github.io/glowcast/docs/starting/) (with an automation to write yourself), and [troubleshooting](https://nicohabets.github.io/glowcast/docs/troubleshooting/).

## Seeing what Glowcast does

The **Status** sensor on Glowcast's device shows **Idle**, **Starting**, **Streaming**, **Black picture** (a dark scene), **No picture** (the TV gives no picture, for example a streaming app with DRM; the lights are released until the picture returns), **Picture source off** (the TV, or a computer's screen helper, turned off or cannot be reached) or **Error**. For Picture source off and Error, the **Stop reason** attribute says why, as a fixed key for automations. A diagnostic **Capture rate** sensor (off by default) shows how many pictures per second Glowcast gets from the TV.

More: [status and diagnostics](https://nicohabets.github.io/glowcast/docs/status/), with every status and stop reason.

## Known limits

- **About 3 captures per second**, the TV's limit: the lights follow scenes, not every flash or cut.
- **Only LG webOS TVs** for now, tested on one model (a 2023 LG G3); other webOS TVs should work the same way.
- **Streaming apps with DRM** in the TV's own apps give no picture (tested with Prime Video); the same app on a streaming stick over HDMI works.
- **One stream per bridge at a time**, so two TVs on one bridge cannot sync together.
- **Only the lights in the entertainment area** follow the picture; other lamps are planned for v0.3 ([#76](https://github.com/nicohabets/glowcast/issues/76)).
- **The TV's capture call is undocumented** by LG and could stop working after a firmware update.

More: [known limits](https://nicohabets.github.io/glowcast/docs/limits/).

## Reporting a problem

Open an issue on [GitHub](https://github.com/nicohabets/glowcast/issues) and attach Glowcast's diagnostics: **Settings → Devices & services → Glowcast → ⋮ → Download diagnostics**. Keys, addresses, ids, serial numbers and names are left out, but have a look before you post it: issues on GitHub are public. **Tried it on your TV?** Please [open a hardware report](https://github.com/nicohabets/glowcast/issues/new?template=hardware-report.md), also when it just works.

More: [troubleshooting](https://nicohabets.github.io/glowcast/docs/troubleshooting/).

## Privacy

Everything stays on your local network: frames are kept in memory and never stored or sent anywhere. Glowcast's key gives full access to your Hue Bridge, and removing Glowcast from Home Assistant does not remove its app from the bridge; Home Assistant shows the steps to remove it in the Hue app.

More: [privacy](https://nicohabets.github.io/glowcast/docs/privacy/) and [uninstalling](https://nicohabets.github.io/glowcast/docs/uninstalling/).

## Disclaimer

Glowcast is an independent project. It is not affiliated with, endorsed by or connected to Signify (Philips Hue) or LG Electronics. All product names are trademarks of their respective owners.

## License

[MIT](https://github.com/nicohabets/glowcast/blob/main/LICENSE)
