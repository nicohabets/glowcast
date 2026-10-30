# Setup

After [installing Glowcast](installation.md), go to **Settings → Devices & services → Add integration → Glowcast**. During setup you pick:

1. **What the lights follow** - **LG webOS TV**, then one of your LG webOS TVs in Home Assistant (or **Computer screen**, see [A computer screen](computer-screen.md))
2. **Which lights follow the picture** - **Hue entertainment area** (the smoothest, for Hue lights in an entertainment area) or **Loose lights** (any colour light in Home Assistant, about one change per second, no Hue bridge needed; see [Loose lights](loose-lights.md)). For an entertainment area:
    1. **Hue Bridge** - and press its link button when asked, then click Submit within 30 seconds. Streaming needs a key that Home Assistant's own Hue connection does not have, so Glowcast pairs once itself. It shows up in the Hue app as an app called "glowcast".
    2. **Entertainment area** - set up in the Hue app; it decides which lights take part and where they stand. No area yet? See [Make an entertainment area in the Hue app](loose-lights.md#make-an-entertainment-area-in-the-hue-app).

    For loose lights: what to follow (single lights, an area or a light group), the lights, and a place for each light.

![The Glowcast setup flow in Home Assistant: choose the TV, choose Hue entertainment area, choose the Hue bridge, press the link button and wait for the pairing, choose the entertainment area, done.](../images/setup-flow.gif){ width="400" }

*Setting up Glowcast: the TV, Hue entertainment area, the Hue bridge and its link button, then the entertainment area.*

Each TV can have one Glowcast entry. If the bridge later stops accepting Glowcast's key (for example because you removed the app in the Hue app), Home Assistant asks you to press the link button again; your settings are kept.

The TV is fixed, and so is the bridge once Glowcast has paired with it; to change them, remove Glowcast and add it again. Everything else can be changed later, also from loose lights to an entertainment area and back; see [Settings](settings.md).

Glowcast adds one device, "Glowcast *TV name*", with a switch to start and stop syncing. Next: [start it automatically](starting.md) when the TV turns on.
