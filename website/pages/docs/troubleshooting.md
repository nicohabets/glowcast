# Troubleshooting

## When Glowcast stops by itself

When it stops by itself, the switch turns off, the [status](status.md) shows Picture source off or Error, and the reason is in the Home Assistant log:

- **The picture source turns off** (or is not reachable): the TV turns off, or for a computer screen the screen helper stops or the screen is locked; the lights are released. When you switch the TV off with the remote, the lights go straight from the picture's colours to the state the bridge restores.
- **Another app is streaming** to the bridge (the Hue Sync app, a Sync Box, another Glowcast): Glowcast does not take over and does not start.
- **The light stream is lost** (bridge restarted, another app took over): one retry after 5 seconds; if that does not work, Glowcast stops. The next switch-on works once the bridge is back.
- **The bridge rejects Glowcast's key** (for example after removing the app in the Hue app): Home Assistant asks you to press the bridge's link button again; your settings are kept.
- **The entertainment area was removed, or has more than 20 channels**: Home Assistant shows a repair with what to change in Glowcast's settings or in the Hue app.
- **The Home Assistant lights have no colour lights left to follow** (the area or light group was removed, or has no colour lights left): Home Assistant shows a repair; choose other lights in Glowcast's settings. It goes away after the next start that works.
- **None of the Home Assistant lights is available**: Glowcast does not start (Error, `output_unavailable`); it starts again once a light is back.
- **The TV or the bridge is no longer set up in Home Assistant**: Home Assistant shows a repair saying which integration to set up again.
- **The TV does not allow screenshots**: Home Assistant shows a repair; see [below](#the-tv-does-not-allow-screenshots).

## The TV does not allow screenshots { #the-tv-does-not-allow-screenshots }

Glowcast sees the picture through the TV's screenshot call, which LG does not document. When the TV refuses that call, Glowcast stops: at once while it syncs, or after about 10 seconds of asking again when it starts, the [status](status.md) shows Error, and Home Assistant shows a repair. The TV is on; it is not a connection problem. There are two answers:

- **The TV does not offer the call** ("no such service or method", stop reason `source_not_supported`): this model or its software does not have it, so Glowcast cannot work on it for now.
- **The TV does not permit the call** ("insufficient permissions", stop reason `source_not_permitted`): the pairing the LG webOS TV integration made with the TV does not allow screenshots. Pairing the TV again may help: delete the TV in the LG webOS TV integration (**Settings → Devices & services**), add it again and accept the request on the TV, then switch Glowcast on again. Glowcast finds the TV again by itself. **Reconfigure** in that integration keeps the existing pairing; it does not ask the TV again.

In both cases, please run the [hardware check](#the-hardware-check), then [open a hardware report](https://github.com/nicohabets/glowcast/issues/new?template=hardware-report.md) with your TV model and webOS version and attach Glowcast's diagnostics (see [below](#reporting-a-problem)), so that we can look at supporting it.

## The lights let go during a streaming app

Streaming apps with DRM in the TV's own apps return a black frame (tested with Prime Video), so Glowcast cannot follow them. After 7 s without a picture it lets go of the lights, so they return to how they were before syncing (about 8 s after the app starts), and the status shows No picture. Glowcast stays on and follows the picture again as soon as there is one. The same app on a streaming stick over HDMI works.

## Home Assistant lights follow slowly

Home Assistant lights change about once per second, and less often with more than 4 lights on one Hue bridge or Thread network; see [How fast](home-assistant-lights.md#how-fast). For Hue lights, an [entertainment area](home-assistant-lights.md#make-an-entertainment-area-in-the-hue-app) is much smoother.

## The lights stay on the last colour after stopping

Home Assistant lights are put back as they were by Glowcast. For an entertainment area, that is the area's "When syncing stops" setting in the Hue app. Choose "Last on-state" to have the bridge restore the lights; see [The switch](starting.md#the-switch).

## HACS does not find Glowcast

Add it as a custom repository; see [Installation](installation.md#hacs-does-not-find-glowcast).

## Reporting a problem

Open an issue on [GitHub](https://github.com/nicohabets/glowcast/issues) and attach Glowcast's diagnostics: **Settings → Devices & services → Glowcast → ⋮ → Download diagnostics**. The download shows the versions, your settings, the TV's and bridge's model and software version, and what Glowcast was doing last (also after it stopped, with the reason). Keys, addresses, ids, serial numbers and names are left out, but have a look before you post it: issues on GitHub are public.

**Tried it on your TV?** Please tell us how it went, also when it just works: run the [hardware check](#the-hardware-check), then [open a hardware report](https://github.com/nicohabets/glowcast/issues/new?template=hardware-report.md) with your TV model, webOS version and bridge, and attach the diagnostics.

## The hardware check { #the-hardware-check }

To make Glowcast work on a TV we have not tested, we need facts you cannot read off a settings screen. The **Run hardware check** button collects them. It is on Glowcast's device page under **Diagnostic** (**Settings → Devices & services → Glowcast**, then the device), when Glowcast uses an LG webOS TV or a Hue entertainment area: with a computer screen and Home Assistant lights only, there is nothing for it to check, so the button is not there.

1. Turn the TV on and show a picture, for example an HDMI input or the app you watch with. Glowcast may be on or off.
2. Press **Run hardware check**. It takes up to about 30 seconds; a notification says when it is done.
3. Download the diagnostics (**⋮ → Download diagnostics** on Glowcast's integration page) and attach the file to a [hardware report](https://github.com/nicohabets/glowcast/issues/new?template=hardware-report.md).

What it collects:

- **The TV**: its model, webOS and firmware version, whether it answers the screenshot call (and if not, how it refuses), the size and format of a screenshot, how long a screenshot takes (shortest, middle and longest of about 10) and how many per second, whether the screenshots were all black, and the app or input on screen (its id, such as `com.webos.app.hdmi1`, so we can recognise apps with copy protection).
- **The Hue bridge**: its model, software and API version; for your entertainment area: the number of channels, where each sits, and the product type of each light or gradient segment in it (a model number, never its name); whether the area or the bridge is streaming, and whether that is Glowcast or another app (not which one).

It only reads: it does not start a stream or change a light. To see where your lights sit, use **Test lights** ([Test the lights](settings.md#test-lights)). While Glowcast syncs, the check takes no screenshots of its own, so it never disturbs syncing; it reports Glowcast's own capture rate instead. Switching Glowcast on while the check takes screenshots ends the check: its result then only says it was interrupted, so run it again. A TV that is off or a bridge that does not answer is recorded as such; the check still finishes.

No picture is kept, and no names, addresses, keys, ids or serial numbers. The result stays in Home Assistant's memory until the next check, a reload of Glowcast (also when you change the kind of lights in the settings) or a restart of Home Assistant, and is only in the diagnostics you download yourself.
