# Choosing your lights

Glowcast sends the picture's colours to a **Hue entertainment area**, to **Home Assistant lights**, or to [both together](#both-together). You choose one of the first two during setup, and you can change it later under **Configure**, where you can also choose both.

| | Hue entertainment area | Home Assistant lights |
|---|---|---|
| Which lights | Hue colour lights that can join an entertainment area, on a Hue Bridge v2 or Bridge Pro | Any colour light in Home Assistant: Hue, Zigbee bulbs and strips, WLED, Matter and others |
| How smooth | Up to 25 changes per second, fading between pictures | About one change per second per light, with a fade |
| Gradient lightstrip | Several colours along its length | One colour |
| Where each light stands | The places you give the lights in the Hue app | The places you give the lights in Glowcast |
| Set up | Pairing with the bridge once (its link button) | Nothing extra |
| While Glowcast syncs | Only Glowcast controls the lights | An automation that changes a light is overruled at the next change of colour |
| When it stops | The area's "When syncing stops" setting in the Hue app decides | Glowcast puts each light back as it was |

[Home Assistant lights](../docs/loose-lights.md) in the documentation has all the details of Home Assistant lights.

## Which one to choose

- **Hue lights that can join an entertainment area:** use an entertainment area. It follows the TV much more smoothly, and each light (and each part of a gradient lightstrip) gets its own colour.
- **Lights on a Hue bridge that cannot join an entertainment area** (older Hue lights, other brands' Zigbee lights paired to the Hue bridge): use them as Home Assistant lights.
- **Lights of other brands** (Zigbee through ZHA or Zigbee2MQTT, WLED, Matter and others): use them as Home Assistant lights.

When you pick Home Assistant lights and some of them are Hue lights that could join an entertainment area, setup tells you so with the page **An entertainment area is smoother**. Click **Submit** to go on with Home Assistant lights anyway.

## Both together

One Glowcast can drive an entertainment area and Home Assistant lights at the same time, with one switch: for example the Hue lights round the TV in an entertainment area, and a lamp of another brand beside the sofa as a Home Assistant light. The area keeps its own pace (up to 25 changes per second) and the Home Assistant lights theirs (about one per second); the Home Assistant lights never slow the area down.

Set it up under **Configure**: choose **Entertainment area and Home Assistant lights**. You go through the [entertainment area's steps](setup.md#an-entertainment-area) first, then the [Home Assistant lights' steps](setup.md#loose-lights), then brightness, intensity and the update rate. See [Both together](setup.md#both-together) on the next page.

A light cannot be in both. If a Home Assistant light you pick is already in the entertainment area, the settings screen says so: "... already follow the TV through the entertainment area, so they cannot also be Home Assistant lights". Pick the other lights one by one, or an area or group without them.


## How fast Home Assistant lights are { #how-fast-loose-lights-are }

Each Home Assistant light changes about once per second and fades in between. A Hue bridge and a Thread network (Matter) handle only a few commands per second for the whole house, so Glowcast sends at most 4 commands per second to each Hue bridge and to each Thread network. With more than 4 Home Assistant lights on one of them, they share those 4 commands and each changes less often. Setup then shows the page **Many lights on one network**, with how often these lights will change; click **Submit** to save anyway.

- A Hue room or zone that you pick as one light counts as one: the bridge changes its lights together, and they all get the same colour.
- A light group made in Home Assistant counts as its lights, because Home Assistant sends a command to each of them.
- Home Assistant does not show which Thread network a Matter light is on, so Glowcast counts all lights of one Matter integration as one network.

See [How fast](../docs/loose-lights.md#how-fast) for the details.

## Make an entertainment area in the Hue app

1. Open the Hue app, go to **Settings → Entertainment areas** and tap **Create entertainment area** (or **+**).
2. Choose **TV**, pick the colour lights around the TV and tap **Continue**.
3. Place each light on the map where it stands, and run the test the app offers. Glowcast uses these places: each light gets the colour of the part of the picture on its side (left or right, near the floor or the ceiling).
4. In Glowcast's setup choose **Hue entertainment area**, or later in Glowcast's settings (**Configure**), and pick the new area.

An area can have up to 20 channels (a gradient TV lightstrip counts as 4). With more, Glowcast cannot stream to it and Home Assistant shows a repair; make a smaller area.

### "When syncing stops": Last on-state

Each entertainment area has a setting in the Hue app for what its lights do when a stream ends: **When syncing stops**. Glowcast follows it:

- **Last on-state**: the bridge puts the lights back as they were before Glowcast started, within about 2 seconds. Choose this one.
- **Pause on the last colour**: the lights keep the last colour Glowcast sent, also when that was a dark one.

Home Assistant and the bridge report the lights' earlier state either way, so only looking at the lights tells you which one you have. If they stay on the last colour, set the lights again from Home Assistant or the Hue app, and change the area to **Last on-state**.

### "Performance": smoother lights

In an entertainment area, one light passes the stream on to the others. If the lights stutter, have a look at the area's **Performance** setting in the Hue app, which decides which light that is. See [The lights stutter](problems.md#the-lights-stutter).

## What happens to the lights while Glowcast syncs

- **In an entertainment area**, the stream takes the lights over: other automations, scenes and the Hue app cannot change them until Glowcast stops. A motion automation that turns on a light in the same area, for example, does nothing during a film. After the stop, the lights return to how they were (with **Last on-state**, see above).
- **Home Assistant lights** can still be changed as usual. An automation or scene that changes one while Glowcast syncs works for a moment, until Glowcast's next change of colour for that light. After the stop, Glowcast puts each light back as it was before: on or off, its brightness and its colour or colour temperature. For a light group picked as a single light, each light in it goes back to its own state.
- Lights that are not part of Glowcast are not touched.

Next: [installing and setting up](setup.md).
