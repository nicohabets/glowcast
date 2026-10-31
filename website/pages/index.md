---
title: Lights that follow your LG TV
description: Glowcast lets your lights follow what's on your LG webOS TV, from Home Assistant, with a Hue entertainment area or any colour light - no subscription, no HDMI box, nothing leaves your home.
hide:
  - navigation
  - toc
---

<div class="gc-hero" markdown>

# Your lights follow the picture on your LG TV

<p class="gc-lead">Glowcast is a free Home Assistant integration that sends the colours on your LG webOS TV to a Hue entertainment area, the smoothest option, or to any colour light in Home Assistant. No subscription, no HDMI box, nothing leaves your home.</p>

![An illustrated living room: the picture on the TV changes colour and the light strip and lamps follow it.](images/illustrations/scene-1-picture-leads-loop.webp)

[Install with HACS](docs/installation.md){ .md-button .md-button--primary }
[Read the documentation](docs/index.md){ .md-button }

</div>

## What you need

- An **LG TV running webOS**, set up in Home Assistant with the **LG webOS Smart TV** integration
- **Colour lights**, one of:
    - A **Hue bridge with an entertainment area**: a Philips Hue Bridge v2 or Bridge Pro, set up in Home Assistant with the **Philips Hue** integration, and an entertainment area set up in the Hue app
    - Or **Home Assistant lights**: any colour light in Home Assistant, of any brand, no Hue bridge needed
- **Home Assistant 2026.8.3** or newer (TVs with webOS 26 need it to pair)
- **[HACS](https://hacs.xyz/)** to install Glowcast

If you already run Home Assistant with the LG webOS integration and colour lights, you have everything you need.

<hr class="gc-strip">

## What it does

<div class="grid cards gc-features" markdown>

-   ![The picture on the TV changes colour and the lights follow it.](images/illustrations/scene-1-picture-leads.svg){ .gc-feature loading=lazy }

    **The lights follow the picture**

    Each light gets the colour of the part of the screen beside it: left, right, top and bottom. A gradient lightstrip shows several colours along its length, and its ends follow the very edges of the picture.

-   ![A dark picture on the TV: every light keeps a faint warm glow.](images/illustrations/scene-2-dark-scene.svg){ .gc-feature loading=lazy }

    **Dark scenes stay cosy**

    When the picture goes dark, the lights keep a faint warm glow instead of going off.

-   ![A strong red picture on the TV: the lights show the same pure red.](images/illustrations/scene-3-saturated-colour.svg){ .gc-feature loading=lazy }

    **Pure colours**

    A strong colour on the screen shows as that pure colour on the lights, not mixed with white.

-   ![Glowcast's device page in Home Assistant: the switch on under Controls and the status Streaming under Sensors.](images/device-page.png){ .gc-feature .gc-shot loading=lazy }

    **Part of Home Assistant**

    A switch, a status and its settings on one device page. Start it with the TV, by voice or from a dashboard, and change the brightness while it runs.

</div>

## How it works

```
LG TV (webOS)  --local-->  Glowcast (in Home Assistant)  --local-->  Hue Bridge  -->  your lights
```

Glowcast reuses Home Assistant's pairing with the TV, asks the TV for a small screen capture about 3 times per second, works out the colour of each part of the picture and streams it to an entertainment area on your Hue bridge, fading smoothly between frames. Everything stays on your local network.

At the time of writing (2026), Philips' own Hue Sync TV app only runs on LG TVs from 2024 onwards (webOS 24+), and costs €129.99 once or €2.99 a month. The alternative is the Hue Play HDMI Sync Box of around €300. Glowcast is free, and it is tested on a TV from before 2024 (a 2023 LG G3).

## Honest limits

- **Only LG webOS TVs for now.** Other TVs and sources are planned. So far Glowcast is tested on one TV, a 2023 LG G3.
- **About 3 pictures per second.** That is the TV's limit, so the lights follow scenes, not every flash. Fine as ambient light; not as fast as an HDMI Sync Box.
- **Protected apps in the TV's own apps give no picture.** Streaming apps with DRM (tested with Prime Video) return a black frame, so the lights let go. The same app on a streaming stick over HDMI works.
- **One stream per bridge.** A Hue bridge streams to one entertainment area at a time, so two TVs on one bridge cannot sync together.
- **Lamps outside the entertainment area are slower.** Other colour lamps can follow as [Home Assistant lights](docs/home-assistant-lights.md), alone or together with the area, at about one change per second.

All limits are on the [Known limits](docs/limits.md) page.

## Install

Glowcast installs through HACS. The button opens Glowcast in HACS in your own Home Assistant:

[Open Glowcast in HACS](https://my.home-assistant.io/redirect/hacs_repository/?owner=nicohabets&repository=glowcast&category=integration){ .md-button .md-button--primary }

Then follow the [installation steps](docs/installation.md).

## More

- [User guide](guide/index.md): from installing to the first evening, step by step
- [Documentation](docs/index.md): installation, setup, settings, starting it with the TV, troubleshooting
- [Releases](https://github.com/nicohabets/glowcast/releases): what changed in each version
- [Glowcast on GitHub](https://github.com/nicohabets/glowcast): the code, and the place to [report a problem](https://github.com/nicohabets/glowcast/issues) or [tell how it works on your TV](https://github.com/nicohabets/glowcast/issues/new?template=hardware-report.md)

Glowcast is an independent project. It is not affiliated with, endorsed by or connected to Signify (Philips Hue) or LG Electronics.
