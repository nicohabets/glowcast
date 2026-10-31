# Settings

## The settings screen

Under **Configure** (Settings → Devices & services → Glowcast), or on Glowcast's device page (see [below](#settings-on-the-device-page)), you can change:

- **which lights follow the picture**: the **entertainment area**, or the [Home Assistant lights](home-assistant-lights.md) and their places;
- **brightness** (1-100 %, default 80 %);
- **intensity**: how vivid the colours are (0-200 %, default 100 %);
- the **update rate**: Low (1 per second), Medium (2), High (3, default) or Maximum (as fast as the TV allows).

The lights always fade smoothly between updates; a scene cut shows at once. When Glowcast takes them over, they first fade in from how they were, in about 2 seconds; a new setting while syncing applies without that fade-in.

**Configure** has three steps: first which lights follow the picture (**Hue entertainment area**, **Home Assistant lights**, or both: **Hue area and Home Assistant lights**, see [Together with an entertainment area](home-assistant-lights.md#together-with-an-entertainment-area)), then their own settings (the area, or the Home Assistant lights and their places), then brightness, intensity and the update rate. Switching from Home Assistant lights to an entertainment area pairs with the bridge then, if Glowcast has not paired with it before; switching back and forth later needs no new pairing. Switching between these reloads Glowcast, so syncing stops; switch it on again. The TV and the bridge are fixed; to change them, remove Glowcast and add it again.

![The first step of Glowcast's settings screen in Home Assistant: Which lights should follow the picture?, with three options: Hue entertainment area - the smoothest, Home Assistant lights - any brand, slower, and Hue area and Home Assistant lights, with the line Pick lights one by one, or a whole area or light group from Home Assistant; each light gets its own place.](../images/settings-menu.png){ width="400" }

*The first step of the settings screen under Configure: which lights follow the picture.*

![The last page of Glowcast's settings screen in Home Assistant: a note that the same settings are on the device page under Configuration, then brightness 80 %, intensity 100 %, update rate High (3 per second).](../images/settings.png){ width="400" }

*The last page of the settings screen under Configure, after the lights, with the default settings.*

## Settings on the device page

The settings are also entities on Glowcast's device, under **Configuration**: **Brightness** and **Intensity** (sliders, in %), **Update rate** (Low, Medium, High, Maximum) and what follows the picture: **Entertainment area** (the bridge's areas by name), or with Home Assistant lights **Lights** (both when you use the two together) (the Home Assistant areas and light groups that have colour lights, or *Chosen lights* while you use single lights, which you pick under **Configure**). Change them on the device page, from a dashboard, by voice or with an automation; the settings screen under **Configure** shows the same values.

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

## Test the lights { #test-lights }

The **Test lights** button on Glowcast's device, under **Configuration**, shows where each light sits, without waiting for the right scene on TV. Press it and the lights show, for about 3 seconds each:

1. **Left red, right blue.** A light in the middle (ambient) takes from both halves and shows purple. Lights far from the TV show the colour of their place too: the test leaves out the softer, whole-picture colour they get while syncing.
2. **Top green, bottom white.** A light in the middle (ambient) shows white too.

Then they return to what they did before:

- **While Glowcast syncs**, the test replaces the picture's colours, and the picture's colours then fade back in. The lights stay claimed, so there is no new connection to the bridge.
- **While Glowcast is off**, it claims the lights for the test only and releases them afterwards, as when it stops ([When it stops](../guide/first-evening.md#when-it-stops)). The TV does not need to be on.

The switch and the status do not change. The test uses your brightness, but not the intensity, so the colours stay clear. Pressing the button again during a test does nothing. Switching Glowcast on during a test ends the test first. If another app is streaming to the lights, or the lights cannot be reached, the button does nothing and the log says why. With an entertainment area and Home Assistant lights together, an area that another app streams is left out, and the Home Assistant lights are still tested. While Glowcast syncs but has let go of the lights (**Starting** or **No picture**), the button does nothing either.

A light in the wrong colour has the wrong place: change it under **Configure** (Home Assistant lights) or in the Hue app (an entertainment area). Home Assistant lights change about once per second, and more slowly with more than 4 lights on one network ([How fast](home-assistant-lights.md#how-fast)), so with many lights some may show a step late or not at all.
