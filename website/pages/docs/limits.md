# Known limits

- **About 3 captures per second.** That is the TV's capture limit, so the lights follow scenes, not every flash or cut. It works well as ambient light, but it is not as fast as the HDMI Sync Box.
- **While syncing**, the lights in the entertainment area only listen to Glowcast. The Hue app and Home Assistant get them back when syncing stops.
- **One stream per bridge at a time** (as far as known; being checked for the Bridge Pro, [#37](https://github.com/nicohabets/glowcast/issues/37)). A Hue bridge streams to one entertainment area at a time, so two Glowcast entries (two TVs) on one bridge cannot sync together.
- **Home Assistant lights change about once per second**, with a fade, and at most 4 commands per second go to each Hue bridge and each Thread network, so with more lights each changes less often (see [How fast](home-assistant-lights.md#how-fast)). A gradient lightstrip gets one colour as a Home Assistant light.
- **An entertainment area and Home Assistant lights together** are chosen in the settings (**Configure**), not during setup, and an entry has at most one entertainment area. A light in the entertainment area cannot also be a Home Assistant light. See [Together with an entertainment area](home-assistant-lights.md#together-with-an-entertainment-area).
- **Choosing another entertainment area while syncing** briefly restarts the light stream (a few seconds). Brightness, intensity and the update rate change without a restart.
- **Streaming apps with DRM** in the TV's own apps return a black frame (tested with Prime Video), so Glowcast cannot follow them. After 7 s without a picture it lets go of the lights, so they return to how they were before syncing (about 8 s after the app starts); Glowcast stays on and follows the picture again as soon as there is one. The same app on a streaming stick over HDMI works. Other apps and copy-protected HDMI sources are not tested yet.
- **The TV's capture call is undocumented** by LG and could stop working after a TV firmware update.
- **Glowcast's icon** in Settings → Devices & services only shows on Home Assistant 2026.3 or newer; older versions show no icon for Glowcast.
- **Tested on one TV model so far**, a 2023 LG G3. Other webOS TVs should work the same way, but that is not confirmed yet.

**Tried it on your TV?** Please tell us how it went, also when it just works: [open a hardware report](https://github.com/nicohabets/glowcast/issues/new?template=hardware-report.md) with your TV model, webOS version, bridge and how many captures per second you get (the [Capture rate](status.md#the-capture-rate) sensor shows it).
