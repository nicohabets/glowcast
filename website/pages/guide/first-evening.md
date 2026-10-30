# The first evening

## Put the switch on a dashboard

Glowcast's switch (for example `switch.glowcast_living_room_tv`) starts and stops syncing. Put it where you can reach it: edit a dashboard and add a card with the switch, for example a tile card. Add the **Status** sensor (for example `sensor.glowcast_living_room_tv_status`) too if you like to see what Glowcast is doing. You can also switch it on by voice, or on Glowcast's device page under **Controls**.

![Glowcast's device page in Home Assistant while syncing: the switch on under Controls, the status Streaming under Sensors, and the settings under Configuration.](../images/device-page.png){ width="400" }

*Glowcast's device page: the switch under Controls, the status under Sensors, the settings under Configuration.*

## Switch it on

1. Turn the TV on and start something to watch.
2. Turn Glowcast's switch on. The status shows **Starting** while Glowcast waits for the TV's picture (up to 10 seconds) and claims the lights, then **Streaming**: the lights follow the picture.

In a dark scene the status shows **Black picture**, and the lights keep a faint warm glow instead of going dark. Some streaming apps in the TV's own apps give no picture at all (copy protection); then the status shows **No picture** and Glowcast lets go of the lights until there is a picture again. See [The lights stay dim](problems.md#the-lights-stay-dim).

## Make it your own

Brightness, intensity and the update rate change while Glowcast syncs, without interrupting the lights: on the device page under **Configuration**, from a dashboard, or under **Configure**. A new entertainment area, area or light group restarts syncing for a few seconds; the switch stays on. All settings and their defaults are on [Settings](../docs/settings.md).

## While Glowcast syncs

- **Lights in an entertainment area** only listen to Glowcast. Automations, scenes and the Hue app cannot change them until Glowcast stops: a motion automation that turns on a light in the same area does nothing during a film.
- **Loose lights** can be changed by an automation or a scene, but Glowcast overrules that at its next change of colour for that light.
- Other lights in the house are not touched.

## When it stops

Switch Glowcast off, or turn the TV off: Glowcast stops by itself when the TV turns off, and releases the lights. When Home Assistant shuts down or restarts while syncing, Glowcast releases the lights first.

- **An entertainment area** returns to how it was before, if the area's **When syncing stops** setting in the Hue app is **Last on-state**. With **Pause on the last colour** the lights keep the last colour Glowcast sent; see ["When syncing stops"](lights.md#when-syncing-stops-last-on-state).
- **Loose lights**: Glowcast puts each light back as it was before: on or off, its brightness and its colour or colour temperature.
- **Both together**: the area follows its Hue app setting, and Glowcast puts every loose light back.

Glowcast never starts by itself: the switch is off after a restart of Home Assistant.

## Start it with the TV

The ready-made blueprint **Start Glowcast when the TV turns on** switches Glowcast on when the TV turns on.

1. Open [Starting automatically](../docs/starting.md#the-blueprint) and click **Import the blueprint**. It opens the import dialog in your own Home Assistant.
2. Create an automation from the blueprint: choose the **TV** and the **Glowcast switch**. Turn on **Also stop when the TV turns off** if you want the switch to go off the moment the TV reports "off" (Glowcast stops by itself anyway).
3. Save the automation.

Glowcast waits up to 10 seconds for the TV to show a picture, so it can start the moment the TV reports "on". To write the automation yourself instead, use the example on [Your own automation](../docs/starting.md#your-own-automation).

Something not working? See [When something is wrong](problems.md).
