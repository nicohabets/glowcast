# Installing and setting up

## 1. Install Glowcast with HACS

[Open Glowcast in HACS](https://my.home-assistant.io/redirect/hacs_repository/?owner=nicohabets&repository=glowcast&category=integration){ .md-button .md-button--primary }

Open Glowcast in HACS (the button above opens it in your own Home Assistant), click **Download**, then **Download** again in the dialog, and restart Home Assistant. [Installation](../docs/installation.md) has each step, and what to do when HACS does not find Glowcast.

## 2. Add Glowcast and choose the TV

1. Go to **Settings → Devices & services → Add integration** and choose **Glowcast**.
2. **What should the lights follow?** Choose **LG webOS TV**.
3. **Choose the TV**: pick your TV and click **Submit**.

Glowcast uses the TV's pairing from the LG webOS Smart TV integration, so the TV asks nothing. Each TV can have one Glowcast entry. If setup says "No LG webOS TV found", set up the TV with the **LG webOS Smart TV** integration first; see [The TV is not found](problems.md#the-tv-is-not-found).

## 3. Choose the lights

**Which lights should follow the picture?** Choose **Hue entertainment area - the smoothest** or **Home Assistant lights - any brand, slower**. [Choosing your lights](lights.md) explains the difference; you can change it later.

### An entertainment area

1. **Choose the Hue bridge**: pick the bridge and click **Submit**.
2. **Pair with the Hue bridge**: press the link button on the bridge, then click **Submit** within 30 seconds. Glowcast shows "Waiting for the link button on the Hue bridge (up to 30 seconds)" until the bridge answers. Streaming needs a key that Home Assistant's own Hue connection does not have, so Glowcast pairs once itself; it shows up in the Hue app as an app called "glowcast".
3. **Choose the entertainment area**: pick the area around the TV and click **Submit**.

If the button was not pressed in time, press it and click **Submit** again. If the bridge has no entertainment area yet, setup says so on this step: [make one in the Hue app](lights.md#make-an-entertainment-area-in-the-hue-app), then click **Submit**.

![The Glowcast setup flow in Home Assistant: choose the TV, choose Hue entertainment area, choose the Hue bridge, press the link button and wait for the pairing, choose the entertainment area, done.](../images/setup-flow.gif){ width="400" }

*Setting up Glowcast with an entertainment area: the TV, Hue entertainment area, the Hue bridge and its link button, then the entertainment area.*

### Home Assistant lights

1. **Choose the Home Assistant lights**: under **Follow**, choose what Glowcast follows and click **Submit**:
    - **Single lights**: you pick the colour lights. A light group picked here (for example a Hue room, which Home Assistant shows as one light) gets one colour for all its lights.
    - **A Home Assistant area**: every colour light in the area, each at its own place.
    - **A light group**: every colour light in the group (for example a Hue room or zone, or a light group made in Home Assistant), each at its own place.
2. Pick the lights, the area or the light group, and click **Submit**. Only colour lights are listed.
3. If some of them are Hue lights that could join an entertainment area, **An entertainment area is smoother** says so. Click **Submit** to go on with Home Assistant lights.
4. **Where is *light*?** For each light in turn, choose the part of the picture it follows, under **Place**: **Left of the screen**, **Right of the screen**, **Above the screen**, **Below the screen**, or **Ambient (the middle of the picture)**. Click **Submit** for the next light.
5. Or choose **Fine-tune** to set the place yourself with two sliders, **Left - right** and **Bottom - top**: -100% is the left or bottom edge of the picture, 100% the right or top edge, 0% the middle.
6. With more than 4 lights on one Hue bridge or Thread network, **Many lights on one network** says how often they will change; see [How fast Home Assistant lights are](lights.md#how-fast-home-assistant-lights-are). Click **Submit** to save anyway.

A light that joins the area or group later follows the middle of the picture until you place it under **Configure**.

![The Glowcast setup flow with Home Assistant lights in Home Assistant: choose LG webOS TV and the TV, choose Home Assistant lights, follow a Home Assistant area, choose the area Living Room, put Floor lamp left of the screen, fine-tune Light bar with the two sliders, done.](../images/ha-lights-setup.gif){ width="400" }

*Setting up Glowcast with Home Assistant lights: the TV, Home Assistant lights, a Home Assistant area, then a place for each light (a preset for the first, Fine-tune for the second).*

### Both together

An entertainment area and Home Assistant lights together are set up under **Configure**, after setup:

1. Open **Settings → Devices & services → Glowcast → Configure** and choose **Hue area and Home Assistant lights**.
2. The entertainment area's steps: **Choose the entertainment area** (first **Choose the Hue bridge** and **Pair with the Hue bridge** if Glowcast has not paired with the bridge yet).
3. The Home Assistant lights' steps, as above. A light that is already in the entertainment area cannot also be a Home Assistant light: the step says which lights, and you pick others.
4. **Glowcast settings**: brightness, intensity and the update rate, which apply to both.

![Glowcast's settings screen in Home Assistant: choose Hue area and Home Assistant lights, keep the entertainment area TV area, follow the Home Assistant area Living Room, put Floor lamp left and Light bar right of the screen, keep brightness, intensity and the update rate, save, then the device page with Entertainment area and Lights under Configuration.](../images/combined-settings-ha-lights.gif){ width="400" }

*An entertainment area and Home Assistant lights together, under Configure: the area, the Home Assistant lights and their places, brightness, intensity and the update rate, then both on the device page.*

[Choosing your lights](lights.md#both-together) says when this is useful.

## 4. Done

Glowcast adds one device, "Glowcast *TV name*", with:

- a **switch** to start and stop syncing, for example `switch.glowcast_living_room_tv`;
- a **Status** sensor that says what Glowcast is doing ([Status and diagnostics](../docs/status.md));
- its settings under **Configuration**: **Brightness**, **Intensity**, **Update rate**, and **Entertainment area** or, with Home Assistant lights, **Lights** (both when you use the two together) ([Settings](../docs/settings.md));
- a **Test lights** button under **Configuration**, which shows red on the left, blue on the right, then green at the top and white below, so you can check where each light sits ([Test the lights](../docs/settings.md#test-lights)).
- a **Run hardware check** button under **Diagnostic**, for a hardware report, when Glowcast uses an LG webOS TV or a Hue entertainment area ([The hardware check](../docs/troubleshooting.md#the-hardware-check)).

The switch is off. Next: [the first evening](first-evening.md).

## Changing it later

Open **Settings → Devices & services → Glowcast → Configure**. It first asks **Which lights should follow the picture?** (**Hue entertainment area - the smoothest**, **Home Assistant lights - any brand, slower**, or **Hue area and Home Assistant lights**), then the lights' own settings (the area, the Home Assistant lights and their places, or both), then brightness, intensity and the update rate.

- Switching from Home Assistant lights to an entertainment area pairs with the bridge then, if Glowcast has not paired with it before. Switching back and forth later needs no new pairing.
- Switching between these reloads Glowcast, so syncing stops; switch it on again.
- The TV is fixed, and so is the bridge once Glowcast has paired with it. To change them, remove Glowcast and add it again.
