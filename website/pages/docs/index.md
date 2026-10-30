# Documentation

Glowcast is a [Home Assistant](https://www.home-assistant.io/) custom integration, installable through [HACS](https://hacs.xyz/). It grabs frames from your LG TV over its local network API, works out the colour of each part of the screen (left, right, top, bottom) and sends those colours to your Philips Hue lights, including gradient lightstrips segment by segment. Other colour lights in Home Assistant can follow too, as [Home Assistant lights](home-assistant-lights.md).

!!! note "An early version"
    Glowcast is at v0.3. It is tested on an LG OLED65G36LA (2023, G3) with a Hue Bridge Pro and a gradient TV lightstrip. Expect breaking changes until v1.0. See the [releases](https://github.com/nicohabets/glowcast/releases) for what changed.

## Getting started

New to Glowcast? The [user guide](../guide/index.md) takes you from installing to the first evening, step by step.

1. [Installation](installation.md): what you need, and installing Glowcast with HACS
2. [Setup](setup.md): choosing the TV, then an entertainment area on the Hue bridge or Home Assistant lights
3. [Starting automatically](starting.md): the switch, and the blueprint that starts Glowcast with the TV

## Using Glowcast

- [Home Assistant lights](home-assistant-lights.md): any colour light, each at its own place, alone or together with an entertainment area; how fast they follow
- [Settings](settings.md): brightness, intensity, the update rate and which lights follow, on the settings screen and the device page
- [Status and diagnostics](status.md): what Glowcast is doing, why it stopped, and the diagnostics for a problem report
- [Troubleshooting](troubleshooting.md): when Glowcast stops by itself, and how to report a problem
- [Known limits](limits.md)
- [A computer screen](computer-screen.md): following a PC or Mac instead of a TV (a preview)
- [Uninstalling](uninstalling.md)
- [Privacy](privacy.md)

## How it works

```
LG TV (webOS)  --local, port 3001-->  Glowcast (inside Home Assistant)  --local-->  Hue Bridge  -->  your lights
```

1. Glowcast reuses the pairing of Home Assistant's existing **LG webOS Smart TV** integration, so there is no extra pairing prompt on the TV.
2. It asks the TV for a screen capture through its local network API and downloads the small JPEG the TV serves on your LAN, about 3 times per second.
3. Each light gets the colour of the part of the screen that matches its position. The colourful, bright pixels of that part count most: spotlights on a dark stage or signs in a city at night give their colour, not a grey average, while a white or evenly coloured part keeps its own colour. Black bars above and below the picture (or beside it) are left out: once they have been there for about 2 seconds, the parts are taken from the picture inside them, so the lights at the top and bottom show the picture's edge instead of turning grey. Subtitles in the bar, a short dark scene or a fade to black do not move them.
4. The colours go to the Hue Bridge: Glowcast streams to an entertainment area you set up in the Hue app, up to 25 times per second, fading smoothly between frames; at a scene cut the lights change at once. Lights use the positions you gave them in the Hue app, left/right and floor/ceiling. Lights further from the TV (front/back in the Hue app) follow more softly: their colour is blended with the colour of the whole picture, up to half for a light at the back of the room. A gradient lightstrip shows several colours along its length: its segments follow the edges of the picture right next to them, as if the picture spills over the edge of the screen.
   Or, with [Home Assistant lights](home-assistant-lights.md), the colours go to any colour light in Home Assistant (Hue, Zigbee, WLED, Matter and others) through Home Assistant, each at the place you give it, about once per second with a fade.

Everything stays on your local network.

On a black picture the lights keep a faint warm glow instead of going dark. Dark colours blend into that glow; brighter colours are shown pure, without the glow mixed in.
