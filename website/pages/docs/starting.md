# Starting automatically

## The switch

Glowcast adds one device, "Glowcast *TV name*", with a **switch** under Controls (and its settings under Configuration), for example `switch.glowcast_living_room_tv`. Turn it on to start syncing and off to stop. [Loose lights](loose-lights.md#when-glowcast-stops) go back to how they were when it stops.

![Glowcast's device page in Home Assistant while syncing: the switch on under Controls, the status Streaming under Sensors, and the settings under Configuration.](../images/device-page.png){ width="400" }

*Glowcast's device page: the switch under Controls, the status under Sensors, the settings under Configuration.*

With an entertainment area, what the lights do when it stops follows the area's "When syncing stops" setting in the Hue app: with "Last on-state" the bridge restores them; with "Pause on the last colour" they keep the last colour Glowcast sent, while Home Assistant still shows their earlier state. Choose "Last on-state" to get them back, or set the lights again from Home Assistant or the Hue app.

Glowcast never starts by itself: the switch is off after a restart of Home Assistant. Start it from a dashboard, by voice, or with an automation, for example when the TV turns on.

## The blueprint

The ready-made blueprint "Start Glowcast when the TV turns on" asks for the TV and the Glowcast switch, and can also turn the switch off when the TV turns off. The button opens the blueprint import dialog in your own Home Assistant:

[Import the blueprint](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fraw.githubusercontent.com%2Fnicohabets%2Fglowcast%2Fmain%2Fblueprints%2Fautomation%2Fglowcast%2Fstart_with_tv.yaml){ .md-button .md-button--primary }

## Your own automation

Or write the automation yourself:

```yaml
triggers:
  - trigger: state
    entity_id: media_player.living_room_tv
    from:
      - "off"
      - "unavailable"
    to: "on"
actions:
  - action: switch.turn_on
    target:
      entity_id: switch.glowcast_living_room_tv
```

Glowcast waits up to 10 seconds for the TV to show a picture, so it can start the moment the TV reports "on". Brightness, intensity and the update rate change while it runs without interrupting the lights. Choosing another entertainment area restarts syncing on the new area (a few seconds); the switch stays on.

When the TV turns off, Glowcast stops by itself and releases the lights; see [Troubleshooting](troubleshooting.md#when-glowcast-stops-by-itself) for this and the other reasons it stops. When Home Assistant shuts down or restarts while syncing, Glowcast releases the lights first.
