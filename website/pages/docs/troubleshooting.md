# Troubleshooting

## When Glowcast stops by itself

When it stops by itself, the switch turns off, the [status](status.md) shows Picture source off or Error, and the reason is in the Home Assistant log:

- **The picture source turns off** (or is not reachable): the TV turns off, or for a computer screen the screen helper stops or the screen is locked; the lights are released. When you switch the TV off with the remote, the lights go straight from the picture's colours to the state the bridge restores.
- **Another app is streaming** to the bridge (the Hue Sync app, a Sync Box, another Glowcast): Glowcast does not take over and does not start.
- **The light stream is lost** (bridge restarted, another app took over): one retry after 5 seconds; if that does not work, Glowcast stops. The next switch-on works once the bridge is back.
- **The bridge rejects Glowcast's key** (for example after removing the app in the Hue app): Home Assistant asks you to press the bridge's link button again; your settings are kept.
- **The entertainment area was removed, or has more than 20 channels**: Home Assistant shows a repair with what to change in Glowcast's settings or in the Hue app.
- **The loose lights have no colour lights left to follow** (the area or light group was removed, or has no colour lights left): Home Assistant shows a repair; choose other lights in Glowcast's settings. It goes away after the next start that works.
- **None of the loose lights is available**: Glowcast does not start (Error, `output_unavailable`); it starts again once a light is back.
- **The TV or the bridge is no longer set up in Home Assistant**: Home Assistant shows a repair saying which integration to set up again.

## The lights let go during a streaming app

Streaming apps with DRM in the TV's own apps return a black frame (tested with Prime Video), so Glowcast cannot follow them. After 7 s without a picture it lets go of the lights, so they return to how they were before syncing (about 8 s after the app starts), and the status shows No picture. Glowcast stays on and follows the picture again as soon as there is one. The same app on a streaming stick over HDMI works.

## Loose lights follow slowly

Loose lights change about once per second, and less often with more than 4 lights on one Hue bridge or Thread network; see [How fast](loose-lights.md#how-fast). For Hue lights, an [entertainment area](loose-lights.md#make-an-entertainment-area-in-the-hue-app) is much smoother.

## The lights stay on the last colour after stopping

Loose lights are put back as they were by Glowcast. For an entertainment area, that is the area's "When syncing stops" setting in the Hue app. Choose "Last on-state" to have the bridge restore the lights; see [The switch](starting.md#the-switch).

## HACS does not find Glowcast

Add it as a custom repository; see [Installation](installation.md#hacs-does-not-find-glowcast).

## Reporting a problem

Open an issue on [GitHub](https://github.com/nicohabets/glowcast/issues) and attach Glowcast's diagnostics: **Settings → Devices & services → Glowcast → ⋮ → Download diagnostics**. The download shows the versions, your settings, the TV's and bridge's model and software version, and what Glowcast was doing last (also after it stopped, with the reason). Keys, addresses, ids, serial numbers and names are left out, but have a look before you post it: issues on GitHub are public.

**Tried it on your TV?** Please tell us how it went, also when it just works: [open a hardware report](https://github.com/nicohabets/glowcast/issues/new?template=hardware-report.md) with your TV model, webOS version, bridge and how many captures per second you get (the [Capture rate](status.md#the-capture-rate) sensor shows it).
