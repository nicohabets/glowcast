# Documentation

Glowcast is a [Home Assistant](https://www.home-assistant.io/) custom integration, installable through [HACS](https://hacs.xyz/). It grabs frames from your LG TV over its local network API, works out the average colour of each part of the screen (left, right, top, bottom) and sends those colours to your Philips Hue lights, including gradient lightstrips segment by segment.

!!! note "An early version"
    Glowcast is at v0.2. It is tested on an LG OLED65G36LA (2023, G3) with a Hue Bridge Pro and a gradient TV lightstrip. Expect breaking changes until v1.0. See the [releases](https://github.com/nicohabets/glowcast/releases) for what changed.

## Getting started

1. [Installation](installation.md): what you need, and installing Glowcast with HACS
2. [Setup](setup.md): choosing the TV, the Hue bridge and the entertainment area
3. [Starting automatically](starting.md): the switch, and the blueprint that starts Glowcast with the TV

## Using Glowcast

- [Settings](settings.md): brightness, intensity, the update rate and the entertainment area, on the settings screen and the device page
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
3. Each light gets the average colour of the part of the screen that matches its position, ignoring black bars.
4. The colours go to the Hue Bridge: Glowcast streams to an entertainment area you set up in the Hue app, up to 25 times per second, fading smoothly between frames. Lights use the positions you gave them in the Hue app, left/right and floor/ceiling. A gradient lightstrip shows several colours along its length.

Everything stays on your local network.
