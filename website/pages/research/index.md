# Light research

Before Glowcast changes what the lights do, a question is often answered first: measured on the TV or on real Hue lights, simulated, or drawn in [the room](../testing/index.md#the-room). These pages show what came out, with the GIFs and stills that show it. Each section gives the question, the answer, the key numbers, what Glowcast does since, and links to the work on GitHub.

Numbers come from the project's own measurements. Where something was emulated or estimated, the section says so.

## Colours and fades

| Question | Answer in one line |
|---|---|
| [Fades on scene cuts](colours.md#fades-on-scene-cuts) | A cut fades in 80 ms; everything else fades softly over the time between two captures (since v0.4.0). |
| [Livelier colours](colours.md#livelier-colours) | Colourful, bright pixels count most in a region's colour; the dominant colour and the median flicker (since v0.4.0). |
| [Busy content and the cut fade](colours.md#busy-content) | Sparkly content can make a light snap up and fade down; real-looking content barely shows it, so it stays as it is. |
| [The fade-in when Glowcast takes the lights](colours.md#fade-in) | The lights fade in over about 2 s from how they were (since v0.3.0). |

## Where each light looks

| Question | Answer in one line |
|---|---|
| [TV strips reach the edges of the picture](positions.md#tv-strips-reach-the-edges) | A lightstrip around the screen is stretched to the picture's edges (since v0.4.0). |
| [Lights far from the TV follow more softly](positions.md#far-lights) | A far light takes part of the whole picture's colour, and stays dim in a dark scene (since v0.4.0). |
| [Letterbox bars](positions.md#letterbox-bars) | The lights follow the picture inside black bars, once the bars have stayed for 2 s (since v0.4.0). |
| [Dark bands near an edge](positions.md#dark-bands) | A dark band that stays for 2 s counts as a bar; the effect is mild. |

## Dark pictures and no picture

| Question | Answer in one line |
|---|---|
| [Streaming apps with DRM](dark.md#drm-apps) | A protected app in the TV's own apps gives black captures; over HDMI the picture comes through. |
| [All-zero frames in films and series](dark.md#all-zero-frames) | Short all-zero moments happen after hard cuts, so the lights are let go only after 7 s without a picture (since v0.2.0). |
| [Black before the TV turns off](dark.md#black-before-off) | The TV shows almost black for about 3 s before it reports off, so a sudden black picture holds the colours for 3.5 s. |
| [When syncing stops](dark.md#when-syncing-stops) | What the lights do after a stream is the area's setting in the Hue app. |

## Lights, bridges and other apps

| Question | Answer in one line |
|---|---|
| [How fast Home Assistant lights can follow](lights.md#home-assistant-lights-rate) | About once per second per light, and at most 4 lights per Hue bridge or Thread network (since v0.3.0). |
| [One stream per bridge](lights.md#one-stream-per-bridge) | A Hue bridge streams one entertainment area at a time; a second Glowcast says the bridge is busy. |
| [Next to a Hue Sync Box](lights.md#next-to-a-sync-box) | Glowcast can drive Home Assistant lights beside a Sync Box; with the box's area sensor as trigger the notice works every time. |
| [The light test and a late state report](lights.md#light-test-late-report) | Not reproducible: switching on right after the light test keeps each light's own state. |
| [A whole evening](lights.md#a-whole-evening) | Yes: it runs for hours; one measured run of 95 minutes had no crash or error, and clean stops and restarts. |

## Speed and hardware

| Question | Answer in one line |
|---|---|
| [The delay from the picture to the light](speed.md#delay) | About a third of a second on the test bench since the cut fade; not yet measured on the TV. |
| [Overlapping captures](speed.md#overlapping-captures) | No gain on the bench; it depends on how the TV handles two requests at once. |
| [A Raspberry Pi 4](speed.md#raspberry-pi-4) | Likely works at every update rate (an estimate from an emulated Pi 4). |
| [The capture rate on the TV](speed.md#capture-rate-on-the-tv) | About 4 captures per second as fast as it goes, about 3 at update rate High. |

## Other questions, answered without a section here

- **Does the Hue library install cleanly in Home Assistant?** Yes, without changing any of Home Assistant's own packages ([#1](https://github.com/nicohabets/glowcast/issues/1)).
- **Can a computer's screen be the picture?** Yes, with a small screen helper on the computer that Glowcast takes frames from (since v0.2.0; [#106](https://github.com/nicohabets/glowcast/issues/106), see [a computer screen](../docs/computer-screen.md)).
- **Can a camera check a light's colour?** Yes: a USB webcam tells red, green, blue, warm white and off apart on a wall lit by a Hue light, which became the test bench ([#133](https://github.com/nicohabets/glowcast/issues/133)).
- **Which LG TVs support the capture?** Probably webOS 4 to 25 (2018-2025), unknown for older ones, probably not webOS 1 ([#185](https://github.com/nicohabets/glowcast/issues/185)).
- **What about other TV brands?** Answered from online reports, not tested; the follow-ups are a capture card and an app on Android TV streamers (below) ([#12](https://github.com/nicohabets/glowcast/issues/12)).
- **What do users of Hue Sync and similar products miss?** Their lessons led to the Test lights button, continuing after a Home Assistant restart, and the questions below ([#159](https://github.com/nicohabets/glowcast/issues/159)).

## Open questions

- [Which Hue app settings of an entertainment area apply to Glowcast's stream?](https://github.com/nicohabets/glowcast/issues/46)
- [HDR and Dolby Vision: does the TV's capture show the colours as on screen?](https://github.com/nicohabets/glowcast/issues/160)
- [A USB HDMI capture card as the picture for any TV](https://github.com/nicohabets/glowcast/issues/179)
- [More pictures per second and a shorter delay from the LG TV](https://github.com/nicohabets/glowcast/issues/180)
- [An app on Android TV and Google TV streamers as the picture](https://github.com/nicohabets/glowcast/issues/186)
