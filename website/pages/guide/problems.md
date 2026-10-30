# When something is wrong

When Glowcast stops by itself, its switch turns off and the **Status** sensor shows **Picture source off** or **Error**. Its attribute **Stop reason** says why, and so does the Home Assistant log. [Troubleshooting](../docs/troubleshooting.md) lists every reason; these are the common ones.

## Another app is streaming

The switch turns off again soon after you switch it on, and the stop reason is **Another app is streaming** (`output_busy`). A Hue bridge streams to one entertainment area at a time, and something else is using it: the Hue Sync app, a Hue Play HDMI Sync Box, or another Glowcast entry on the same bridge. Glowcast does not take over from it.

Stop the other app first (in that app, or by switching off the other Glowcast), then switch Glowcast on again.

With [an entertainment area and Home Assistant lights together](lights.md#both-together), Glowcast does not stop: the Home Assistant lights follow the picture without the area, and the status shows which part dropped out; see [Part of the lights drops out](#part-of-the-lights-drops-out).

## No entertainment areas

During setup, the step **Choose the entertainment area** says "This Hue bridge has no entertainment areas yet". [Make one in the Hue app](lights.md#make-an-entertainment-area-in-the-hue-app), then click **Submit** on the same step: you do not need to pair again. Under **Configure**, when Glowcast is already paired with the bridge, a similar message closes the settings screen; make an area and open **Configure** again. When you switch from Home Assistant lights and Glowcast has not paired yet, Configure pairs first and the message shows on **Choose the entertainment area**, as during setup: make an area, then click **Submit**.

If setup says "No Hue bridge that can stream found", set up a Hue Bridge v2 or Hue Bridge Pro with the **Philips Hue** integration first. The original round bridge (sold 2012-2015) cannot stream. Without a bridge that can stream, use [Home Assistant lights](lights.md).

## The lights stay dim

- **A dark scene:** the status shows **Black picture** and the lights keep a faint warm glow on purpose. They follow again when the picture brightens.
- **A streaming app with copy protection (DRM):** some streaming apps in the TV's own apps give Glowcast only a black picture (tested with Prime Video). After 7 seconds without a picture Glowcast lets go of the lights and the status shows **No picture**. Home Assistant lights are put back as they were by Glowcast; an entertainment area does what its **When syncing stops** setting in the Hue app says (with **Pause on the last colour** the lights keep the last, dark colour). Glowcast stays on and follows again as soon as there is a picture. The same app on a streaming stick over HDMI works.
- **The brightness setting:** **Brightness** sets how bright the lights are at most (default 80 %). Raise it on the device page, or raise **Intensity** for more vivid colours; see [Settings](../docs/settings.md).
- **After Glowcast stopped:** if the lights stay on a dark colour, that is the entertainment area's **When syncing stops** setting in the Hue app. See [The lights stay on the last colour](#the-lights-stay-on-the-last-colour).

## Glowcast asks to pair again

When the Hue bridge no longer accepts Glowcast's key (for example after you removed the "glowcast" app in the Hue app), Glowcast stops with the stop reason **Key rejected** (`auth_failed`), and Home Assistant asks you to pair again, under **Settings → Devices & services**. **Pair with the Hue bridge again** asks you to press the link button on the bridge, then click **Submit**. Your settings are kept.

With [an entertainment area and Home Assistant lights together](lights.md#both-together), Glowcast does not stop: the area is dropped without a retry, the Home Assistant lights keep following the picture, and **Dropped lights** shows `auth_failed` for the area. Home Assistant still asks you to pair again; after pairing, switch Glowcast off and on to bring the area back.

For a computer screen it is **Pair with the screen helper again**: enter the 6-digit code the screen helper shows now.

## The TV is not found

- **During setup**, "No LG webOS TV found": set up the TV with Home Assistant's **LG webOS Smart TV** integration first, then add Glowcast again. "Glowcast is already set up for every LG webOS TV in Home Assistant" means each TV already has its Glowcast entry; open that one instead.
- **When you switch Glowcast on**, it waits up to 10 seconds for the TV's picture. If the TV is off or cannot be reached, it stops and the status shows **Picture source off**. Glowcast cannot turn the TV on: turn the TV on first, or [start Glowcast with the TV](first-evening.md#start-it-with-the-tv).
- **A repair "Glowcast *name*: the TV is not set up"** means the TV is no longer set up in Home Assistant, or is not loaded. Set it up again with the LG webOS Smart TV integration (Settings → Devices & services), then switch Glowcast on again.

## The lights stutter

- **An entertainment area:** one light in the area passes the stream on to the others. In the Hue app, the area's **Performance** setting decides which light that is; try another light there.
- **Home Assistant lights:** each light changes about once per second, and less often with more than 4 lights on one Hue bridge or Thread network; see [How fast Home Assistant lights are](lights.md#how-fast-loose-lights-are). For Hue lights, an entertainment area is much smoother.
- The TV gives about 3 pictures per second, so the lights follow scenes, not every flash or cut. That is a [known limit](../docs/limits.md).

## Part of the lights drops out

This is about [an entertainment area and Home Assistant lights together](lights.md#both-together). If one of the two cannot start (for example another app streams to the area, or none of the Home Assistant lights is available), the other starts without it. If one is lost while syncing (the bridge stops answering, or the Home Assistant lights become unavailable), the other keeps following the picture, and Glowcast tries the lost one again after 5 seconds. If that fails, or it is lost again within a minute, it stays off until you switch Glowcast on again. A part whose key the bridge rejects gets no retry; see [Glowcast asks to pair again](#glowcast-asks-to-pair-again).

While a part is out, the **Status** sensor's attribute **Dropped lights** (`dropped_outputs`) lists it with the reason, for example `{"hue_entertainment": "output_busy"}` while another app streams to the area. The log says what happened too. Fix the cause (stop the other app, or bring the lights back), then switch Glowcast off and on. Only when both parts are lost does Glowcast stop. See [Status and diagnostics](../docs/status.md#why-it-stopped).

## The lights stay on the last colour

Home Assistant lights are put back by Glowcast itself. After Glowcast stops, the lights in an entertainment area may keep the last colour, while Home Assistant shows their earlier state. That is the area's **When syncing stops** setting in the Hue app: change it to **Last on-state**, and set the lights again from Home Assistant or the Hue app this once. See ["When syncing stops"](lights.md#when-syncing-stops-last-on-state).

## An automation does nothing while Glowcast syncs

That is how an entertainment area works: while Glowcast streams, its lights only listen to Glowcast, so automations, scenes and the Hue app cannot change them. A Home Assistant light changed by an automation is overruled at Glowcast's next change of colour. When Glowcast stops, Glowcast puts Home Assistant lights back as they were, and the entertainment area's lights return with the Hue app's **Last on-state** setting; see [The first evening](first-evening.md#while-glowcast-syncs).

## Still stuck?

Download Glowcast's diagnostics and open an issue; see [Reporting a problem](../docs/troubleshooting.md#reporting-a-problem).

## Uninstalling

Remove Glowcast's entry in Home Assistant, remove its app from the Hue bridge in the Hue app (**Settings → Apps → Network-based**, the info button next to **glowcast**; Dutch: Instellingen → Toepassingen → Netwerk-gebaseerd), and remove Glowcast from HACS. [Uninstalling](../docs/uninstalling.md) has each step.

## Known limits

The lights follow about 3 pictures per second, one Hue bridge streams to one entertainment area at a time, and Glowcast is tested on one TV so far (an LG OLED65G36LA, 2023, G3, with a Hue Bridge Pro and a gradient TV lightstrip). All limits are on [Known limits](../docs/limits.md).
