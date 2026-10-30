# Settings

## The settings screen

Under **Configure** (Settings → Devices & services → Glowcast), or on Glowcast's device page (see [below](#settings-on-the-device-page)), you can change:

- the **entertainment area**;
- **brightness** (1-100 %, default 80 %);
- **intensity**: how vivid the colours are (0-200 %, default 100 %);
- the **update rate**: Low (1 per second), Medium (2), High (3, default) or Maximum (as fast as the TV allows).

The lights always fade smoothly between updates. The TV and bridge are fixed; to change them, remove Glowcast and add it again.

![Glowcast's settings screen in Home Assistant: a note that the same settings are on the device page under Configuration, then entertainment area TV area, brightness 80 %, intensity 100 %, update rate High (3 per second).](../images/settings.png){ width="400" }

*The settings screen under Configure, with the default settings.*

## Settings on the device page

The settings are also entities on Glowcast's device, under **Configuration**: **Brightness** and **Intensity** (sliders, in %), **Update rate** (Low, Medium, High, Maximum) and **Entertainment area** (the bridge's areas by name). Change them on the device page, from a dashboard, by voice or with an automation; the settings screen under **Configure** shows the same values.

![Glowcast's device page in Home Assistant while syncing: the switch on under Controls, the status Streaming under Sensors, and the settings under Configuration.](../images/device-page.png){ width="400" }

*Glowcast's device page: the switch under Controls, the status under Sensors, the settings under Configuration.*

While syncing, a new brightness reaches the lights at once; a new intensity and update rate apply from the next capture (the lights then fade to the new colours as usual), without a restart. Only a new entertainment area restarts syncing (a few seconds); the switch stays on.

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
