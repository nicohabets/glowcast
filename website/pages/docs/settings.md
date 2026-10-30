# Settings

## The settings screen

Under **Configure** (Settings → Devices & services → Glowcast), or on Glowcast's device page (see [below](#settings-on-the-device-page)), you can change:

- **which lights follow the picture**: the **entertainment area**, or the [loose lights](loose-lights.md) and their places;
- **brightness** (1-100 %, default 80 %);
- **intensity**: how vivid the colours are (0-200 %, default 100 %);
- the **update rate**: Low (1 per second), Medium (2), High (3, default) or Maximum (as fast as the TV allows).

The lights always fade smoothly between updates.

**Configure** has three steps: first which lights follow the picture (**Hue entertainment area**, **Loose lights**, or both: **Entertainment area and loose lights**, see [Together with an entertainment area](loose-lights.md#together-with-an-entertainment-area)), then their own settings (the area, or the loose lights and their places), then brightness, intensity and the update rate. Switching from loose lights to an entertainment area pairs with the bridge then, if Glowcast has not paired with it before; switching back and forth later needs no new pairing. Switching between these reloads Glowcast, so syncing stops; switch it on again. The TV and the bridge are fixed; to change them, remove Glowcast and add it again.

![The last page of Glowcast's settings screen in Home Assistant: a note that the same settings are on the device page under Configuration, then brightness 80 %, intensity 100 %, update rate High (3 per second).](../images/settings.png){ width="400" }

*The last page of the settings screen under Configure, after the lights, with the default settings.*

## Settings on the device page

The settings are also entities on Glowcast's device, under **Configuration**: **Brightness** and **Intensity** (sliders, in %), **Update rate** (Low, Medium, High, Maximum) and what follows the picture: **Entertainment area** (the bridge's areas by name), or with loose lights **Lights** (both when you use the two together) (the Home Assistant areas and light groups that have colour lights, or *Chosen lights* while you use single lights, which you pick under **Configure**). Change them on the device page, from a dashboard, by voice or with an automation; the settings screen under **Configure** shows the same values.

![Glowcast's device page in Home Assistant while syncing: the switch on under Controls, the status Streaming under Sensors, and the settings under Configuration.](../images/device-page.png){ width="400" }

*Glowcast's device page: the switch under Controls, the status under Sensors, the settings under Configuration.*

While syncing, a new brightness reaches the lights at once; a new intensity and update rate apply from the next capture (the lights then fade to the new colours as usual), without a restart. Only a new entertainment area, area or light group restarts syncing (a few seconds); the switch stays on. Lights of a new area or group that have no place yet follow the middle of the picture until you place them under **Configure**.

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
