# Home Assistant lights

With **Home Assistant lights**, any colour light in Home Assistant follows the picture: Hue lights, Zigbee bulbs and strips, WLED, Matter lights and others. No Hue bridge is needed. (Up to v0.3 this was called loose lights.) Choose **Home Assistant lights** when Glowcast asks which lights follow the picture, during [setup](setup.md) or later under **Configure** (see [Settings](settings.md)).

![The same room with extra lamps beside the sofa, each following its own side of the picture.](../images/illustrations/scene-4-home-assistant-lights.svg){ width="400" loading=lazy }

*Lamps outside an entertainment area, each following its own side of the picture.*

## What the lights follow

- **Single lights** - pick the colour lights. A light group picked here (for example a Hue room, which Home Assistant shows as one light) gets one colour for all its lights.
- **A Home Assistant area** - every colour light in the area, each as a light of its own.
- **A light group** - every colour light in the group (for example a Hue room or zone, or a light group made in Home Assistant), each as a light of its own; groups inside the group are included too.

Only colour lights are offered. A light set up only in YAML, without a unique id, cannot be picked.

## Where each light stands

Each light gets its place, one light per page: **Left of the screen**, **Right of the screen**, **Above the screen**, **Below the screen**, or **Ambient** (the middle of the picture). Or choose **Fine-tune** and set the place with two sliders: left - right and bottom - top, from -100 % (the left or bottom edge of the picture) to 100 % (the right or top edge).

A light that joins the area or group later follows the middle of the picture until you place it. A light that leaves is no longer used. The settings screen lists both before you go on, and the places of the lights that left are removed when you save. A light that is unavailable when Glowcast starts is skipped until the next start. If the area or group has no colour lights left, or was removed, Glowcast does not start and a repair says so.

## When Glowcast stops

When Glowcast stops (switched off, the picture source off, no picture, an error, or Home Assistant restarting), each light goes back to how it was before: on or off, its brightness and its colour or colour temperature. For a light group picked as a single light, each light in it goes back to its own state.

## How fast

Each light changes about once per second, fading in between.

A Hue bridge and a Thread network (Matter) handle only a few commands per second for the whole house (measured in [#125](https://github.com/nicohabets/glowcast/issues/125)). Above that a Hue bridge falls behind, and other lights on it stop responding. So Glowcast sends at most 4 commands per second to each Hue bridge and to each Thread network; the two do not share this limit. Home Assistant does not show which Thread network a Matter light is on, so all lights of one Matter integration count as one network.

- With more than 4 lights on one bridge or network, they share those 4 commands: each changes less often than once per second, and fades over that longer time. Glowcast warns you when you save.
- A Hue room or zone picked as a single light counts as one: the bridge changes its lights together.
- A light group made in Home Assistant counts as its lights, because it sends a command to each of them.
- Lights of other kinds (Zigbee through ZHA or Zigbee2MQTT, WLED and others) each change once per second; their limits are not measured yet.

A gradient lightstrip gets one colour as a Home Assistant light. An automation that changes a Home Assistant light while Glowcast syncs is overruled at the next change of colour.

## Hue lights: an entertainment area is smoother

An entertainment area follows the picture much more smoothly: up to 25 changes per second instead of about one, and a gradient lightstrip shows several colours. During setup, Glowcast tells you when some of the Hue lights you picked can join an entertainment area. Hue lights that cannot (older Hue lights, other brands' lights on a Hue bridge) work fine as Home Assistant lights.

While Glowcast streams to an entertainment area, its lights only listen to Glowcast, so automations and the Hue app cannot control them until syncing stops.

## Together with an entertainment area

One Glowcast can drive an entertainment area and Home Assistant lights together, with one switch: for example Hue lights round the TV in an area, and a non-Hue strip or a Matter lamp beside the sofa. Under **Configure**, choose **Entertainment area and Home Assistant lights**: first the area (pairing with the bridge if Glowcast has not done that yet), then the Home Assistant lights and their places, then brightness, intensity and the update rate.

- The area keeps its own pace (the update rate, up to 25 changes per second), and the Home Assistant lights theirs (about once per second); the Home Assistant lights never slow the area down.
- A light that is in the entertainment area cannot also be a Home Assistant light: it already follows the picture through the area. The settings screen says which lights, and so does the **Lights** or **Entertainment area** entity on the device page when you pick an area, group or entertainment area that would put a light in both. Pick the other lights one by one, or a group without them.
- If one of the two cannot start (for example another app streams to the area, or none of the Home Assistant lights is available), the other starts without it. If one is lost while syncing (the bridge stops answering, or the Home Assistant lights become unavailable), the other keeps following the picture, and Glowcast tries the lost one again after 5 seconds. If that fails, or it is lost again within a minute, it stays off until you switch Glowcast on again. The log says what happened, and the status shows the lights that dropped out (see [Status](status.md)). A rejected bridge key, a removed area or a Hue integration that is not set up gets its own repair or re-pairing, as for an area alone.
- Only when both are lost does Glowcast stop, as it does for one.
- Switching off (or the picture source off, or Home Assistant stopping) releases the area and puts every Home Assistant light back as it was.

## Make an entertainment area in the Hue app

1. Open the Hue app, go to **Settings → Entertainment areas** and tap **Create entertainment area** (or **+**).
2. Choose **TV**, pick the colour lights around the TV and tap **Continue**.
3. Place each light on the map where it stands, and run the test the app offers.
4. In Glowcast's settings (**Configure**), choose **Hue entertainment area**, pair with the bridge if asked, and pick the new area.
