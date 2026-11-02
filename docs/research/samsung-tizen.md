# Can a current Samsung TV give Glowcast a picture?

Research for spike #321, done on 2026-11-02. Desk research and code reading only; no Samsung TV, bridge or Home Assistant was used. No new hardware facts of our own were found, so nothing was added to "Verified facts" in `docs/ARCHITECTURE.md`. This updates the Samsung part of research #12.

## The short answer

- **#12's "not feasible" is out of date.** Since mid-2026, people report edge colours from Samsung TVs on Tizen 9 and 10 (2024-2026 models), through a measuring function in the TV's video processor. Two of them report driving a Hue entertainment area this way, through Hyperion or HyperHDR.
- **It is not a picture.** The TV gives the colour of a small block at a chosen position, two blocks at a time. In practice that is 6 to 16 points on the edges, at about 2 to 9 updates per second. The middle of the picture is not measured.
- **Protected apps are reported to work.** One owner reports Netflix on this route, unlike the TV's screenshot on LG. This is the route's biggest advantage.
- **Nobody reports the S90F.** It probably has the function (see "A hint for the S90F" below), but that is our inference. Only a test on the TV can settle it.
- **It is not a supported route.** The function was found by reverse engineering, is meant for Samsung's partners, and the user must sideload an app with their own Samsung certificate. Samsung has already renamed the function once.
- **Recommendation:** do not build it now. Keep pointing Samsung users to the camera source (#45). Options are at the end; the choice is the product owner's.

## Evidence levels

As in #12:

- **verified: hardware** - measured by Glowcast on real hardware ("Verified facts" in `docs/ARCHITECTURE.md`). Nothing in this document is.
- **verified: source** - code or a project's own manifest that we read.
- **verified: docs** - official documentation.
- **reported** - forum post, issue, pull request or README, with its date.
- **inference** - our own conclusion, not tested and not stated by a source.

## How it works

- `/usr/lib/libvideoenhance.so` on the TV has three functions: ask the conditions, set a measuring position, read the average 10-bit RGB of a block at that position. **reported** ([tizen-os-ambilight research notes](https://github.com/exzos28/tizen-os-ambilight/blob/main/docs/RESEARCH.md))
- The usual conditions are 2 blocks per cycle, each 16x16 pixels of a 3840x2160 picture, with a wait of 20 ms per cycle. **reported** (same notes; [HyperTizen #15](https://github.com/reisxd/HyperTizen/pull/15), August 2026). A Q60D reports a wait of 40 ms. **reported** ([tizen-os-ambilight #3](https://github.com/exzos28/tizen-os-ambilight/pull/3), October 2026)
- So the rate is bound by the number of points: 14 points at 20 ms is about 7 updates per second, and 8 points is about 9. **reported** ([tizen-os-ambilight #1](https://github.com/exzos28/tizen-os-ambilight/issues/1), [Block-Build fork](https://github.com/Block-Build/HyperTizen))
- According to the author of tizen-os-ambilight, this is the function behind Philips' Hue Sync TV app, found by decompiling that app. **reported** (research notes above; #1, June 2026). We did not check this, and did not decompile anything.
- Samsung renamed the functions: `ve_*` on Tizen 7 and 8, `ppi_ve_*` on Tizen 9 and 10. Older builds of HyperTizen gave black frames or would not start on newer firmware for this reason. **reported** ([HyperTizen #14](https://github.com/reisxd/HyperTizen/pull/14), July 2026; #15)

## Which TVs

| TV | Tizen | Result | Source |
|---|---|---|---|
| **S90F (2025)** | 9 | **no report** | Processor NQ4 AI Gen3 per retailers; the Tizen version per [tab-tv](https://en.tab-tv.com/samsung-oled-tvs-2025-s85f-s90f-s95f) |
| R95H Micro RGB (2026) | 10 | Works, drives a Hue entertainment area through HyperHDR | **reported**, #14, July 2026 |
| The Frame (`24_PTM_FTV_T09`, so 2024 by its name) | 9 | Works, drives a Hue entertainment area through Hyperion | **reported**, #15, August 2026 |
| QN90D, QN92D (2024) | not stated | Works | **reported**, [tizen-os-ambilight's list](https://github.com/exzos28/tizen-os-ambilight#supported-tvs) |
| Q60D (2024) | 9 | Works: about 2 updates per second with 24 points, also in Netflix and YouTube | **reported**, tizen-os-ambilight #3, October 2026 |
| QN90C (2023) | 9 | Works: 8 points, about 9 per second | **reported**, Block-Build fork |
| Q6F (2025, Q4 Lite processor) | not stated | **Fails:** the functions exist, the conditions call returns an error | **reported**, [tizen-os-ambilight #2](https://github.com/exzos28/tizen-os-ambilight/issues/2), June 2026 |
| Q80A (2021) | 6 | Measuring does not work; a different full-frame library does (480x270) | **reported**, [lowryn/HyperTizen #2](https://github.com/lowryn/HyperTizen/pull/2), September 2026 |

**A hint for the S90F (inference).** The one failing TV, the Q6F, is also outside Samsung's Hue Sync list, and the Q60D that works is on it. The list says "Q7F and above" for 2025 and "as of April 2026", without naming the S series ([Samsung NL](https://www.samsung.com/nl/support/tv-audio-video/set-up-hue-sync-on-your-samsung-smart-tv/), **verified: docs**). So the hardware function may follow that list, and the S90F, sold with Samsung's top 4K processor rather than a Q4 Lite, probably has it. An S95F owner [could not find the Hue Sync app](https://us.community.samsung.com/t5/LED-and-OLED-TVs/Can-t-find-Philips-Hue-Sync-on-2025-S95F-OLED-TV/td-p/3392237) (**reported**, unverified), so Hue Sync support on the S series is not settled either.

**Full picture on Tizen 8 and later: not usable so far.** A second library could give 480x270 frames at about 25 per second, but it is missing on the QN90C and failed to load on another Tizen 9 TV. **reported** (Block-Build fork; a comment of March 2026 on [HyperTizen #8](https://github.com/reisxd/HyperTizen/pull/8)). That route also reports an error on Netflix and Widevine content (#8, March 2025). So the advantage with protected apps belongs to the measuring function, not to full frames.

## The projects

| Project | Last active | What it does | Install |
|---|---|---|---|
| [tizen-os-ambilight](https://github.com/exzos28/tizen-os-ambilight) | 2026-11-01 | 14 points to an ESP32, or a 64x36 image to HyperHDR (that output is untested on a real TV, by its own README) | Apps2Samsung, or build it with Tizen Studio |
| [TizenGlow](https://github.com/DuckieOnQuacks/TizenGlow) | 2026-10-21 | 6-14 points to WLED or HyperHDR; black bar detection; restarts itself within 10 minutes through a system alarm | TizenBrew plus its own TPK, re-signed |
| [HyperTizen](https://github.com/reisxd/HyperTizen) | main 2024-12-01; open pull requests up to 2026 | The original; Tizen 9 only in open pull requests #14 and #15 | TizenBrew |
| [Block-Build fork](https://github.com/Block-Build/HyperTizen) | 2026-09-27 | 8 points, about 9 per second | Tizen Studio, re-signed |
| [JensorX fork](https://github.com/JensorX/HyperTizen) | 2026-10-03 | "Doesn't actually work yet", by its own README | - |

## What it costs the user

- **Developer mode, a PC and a Samsung certificate.** TizenBrew's installer is the easiest route, and on Tizen 7 and later it needs a Samsung account to create a certificate for that TV. **verified: source** ([TizenBrew guide](https://github.com/reisxd/TizenBrew/blob/main/docs/README.md))
- **After a full restart of the TV, the service may not run.** #15 measured that automatic start at boot needs a certificate third parties cannot get, so it starts from the TizenBrew screen. **reported**. TizenGlow works around this with a repeating system alarm, by its own guide. **reported**
- **A store release looks unlikely (inference).** The manifest of tizen-os-ambilight declares a privilege under `developer.samsung.com/privilege/contentanalysis`, which its notes describe as a Samsung partner API. **verified: source** ([manifest](https://github.com/exzos28/tizen-os-ambilight/blob/main/tv/service/tizen-manifest.xml)), **reported**.

## Limits

- **Edges only.** Glowcast's colour engine reads a band of 30 % of the picture per light and the whole picture for far lights (architecture 3.3), from 64x36 pixels. With 6 to 16 small blocks on the edges, a light has only a few points, and the whole picture is an estimate. Whether that looks good enough is **unknown** (inference: fine for a strip behind the TV, weaker for lights further back).
- **Very small blocks.** A 16x16 block is about 0.003 % of a 4K picture, so one bright or dark spot can swing a point (inference). tizen-os-ambilight notes black border detection in HyperHDR should be off, as a dark edge looks like a bar. **reported** ([HYPERHDR.md](https://github.com/exzos28/tizen-os-ambilight/blob/main/docs/HYPERHDR.md))
- **Black bars.** The fixed points fall on the bars in a letterboxed film unless they move. TizenGlow detects this and follows the picture. **reported**. Glowcast's own letterbox handling works on full frames and would not apply as it is (inference).
- **Colours.** The values are 10-bit and converted to 8-bit, and #15 calls the accuracy approximate. **reported**. Nothing we found measures HDR or Dolby Vision on this route.
- **Protected apps.** Works according to HyperTizen's author (December 2024, older function names), tizen-os-ambilight's author and the Q60D report. **reported**. Only one of these names Netflix on a tested TV.
- **Firmware updates.** The rename on Tizen 9 shows that an update can break the route, and that it can look like a working stream with black frames. A partner API gives no promise to us. **inference**
- **Samsung's terms.** tizen-os-ambilight says it was made "for educational purposes through reverse engineering" and that it takes no responsibility for violating third-party terms of service. Whether a published product may use this function is not a technical question and **not answered here**.
- **Security flaws are not a route.** A 2026 advisory describes a low-severity flaw in Tizen's developer tools ([Bishop Fox](https://bishopfox.com/blog/samsung-tizen-os-version-through-9-0)). We do not use or describe such flaws; the projects above do not need them. It does mean developer mode is not something to ask of a user lightly, and Bishop Fox advises against leaving it on. **verified: docs** (the advisory)

## What it could mean for Glowcast

This is what fits the design, without building it:

- **Glowcast pulls; these apps push.** All projects above send frames to a server. Nothing on the TV answers Glowcast's request (architecture 8.2, 8.3). **verified: source**
- **The screen helper's protocol is the natural fit (inference).** An app on the TV could answer `GET /v1/frame` with a 64x36 image built from the points (tizen-os-ambilight already renders such an image for HyperHDR). Home Assistant then needs no new source type, as with the Android idea in #186. It means Glowcast maintains a .NET Tizen app, outside this repository's Python world.
- **No Glowcast needed for the enthusiast.** With a HyperHDR server, TV to HyperHDR to Hue is already reported to work (#14). Glowcast's part (Home Assistant control, placement, the status) would be the added value, not the capture.

## Options for the product owner

1. **Do nothing now (recommended).** Keep Samsung as "use the camera source" (#45, v0.7). Update the wording from "not possible" to "not supported: edge colours only through an unofficial route". Look again when a project reports the S90F or when #45 is done.
2. **Hands-on spike, when a Samsung TV on Tizen 9 is available.** Without building anything: install TizenGlow or tizen-os-ambilight, and measure on the S90F whether the function answers, the rate, Netflix and HDMI, and the colours next to a bench run. Needs the owner of the TV's go, developer mode and a Samsung account. Should come before option 3.
3. **A Glowcast app for Samsung TVs,** speaking the helper protocol. Only after option 2, and after a decision on Samsung's terms, on the maintenance of a Tizen app and on the certificate step for users.

## For the question that started this

For someone choosing between an S90F and an LG C5 who wants Glowcast: the C5 is still the better choice. The C5's capture call is what Glowcast already uses, though not tested on that model and open on webOS 26 (`docs/research/webos-devices.md`). On the S90F, Glowcast does not work today; only the enthusiast's route without Glowcast could, and nobody reports it on that TV.
