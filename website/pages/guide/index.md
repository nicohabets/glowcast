# User guide

From installing Glowcast to the first evening with the lights following your TV, step by step. The [documentation](../docs/index.md) has the details of each setting; this guide takes you through it in order.

## What you need

- **Home Assistant** 2025.7.2 or newer, with [HACS](https://hacs.xyz/).
- An **LG TV running webOS**, already set up in Home Assistant with the **LG webOS Smart TV** integration.
- **Colour lights**, one of:
    - **Hue lights in an entertainment area**: a Philips Hue Bridge v2 or Hue Bridge Pro, set up in Home Assistant with the **Philips Hue** integration, and an entertainment area made in the Hue app. This is the smoothest.
    - **Loose lights**: any colour lights in Home Assistant, of any brand. No Hue bridge needed.

Not sure which lights to use? [Choosing your lights](lights.md) explains the difference.

## The steps

1. [Choosing your lights](lights.md): an entertainment area or loose lights, and how to make an entertainment area in the Hue app.
2. [Installing and setting up](setup.md): Glowcast from HACS, then the TV, the lights and the bridge's link button.
3. [The first evening](first-evening.md): the switch on a dashboard, what the lights do while Glowcast syncs and after, and starting it with the TV.
4. [When something is wrong](problems.md): the common problems and what to do about them, uninstalling, and the known limits.

![An illustrated living room: the picture on the TV changes colour and the light strip and lamps follow it.](../images/illustrations/scene-1-picture-leads-loop.webp){ width="400" }

*The picture changes; a moment later the lights around the TV and in the room follow it.*

## Tested hardware

Glowcast is an early version. It is tested on an LG OLED65G36LA (2023, G3) with a Hue Bridge Pro and a gradient TV lightstrip. Other webOS TVs should work the same way, but that is not confirmed yet; see [Known limits](../docs/limits.md).
