# What users complain about in Hue Sync and similar products

Research for spike #159, done on 2026-10-30. Desk research on the public web; no hardware was used.

Glowcast is compared as it is on `main` on that date (v0.2 work merged, except the test bench PR #154), using the README and `docs/ARCHITECTURE.md` (section 2, "Verified facts").

## Method and limits

- **Products:**
  - Philips Hue Play HDMI Sync Box (4K, 2019) and Sync Box 8K (2024);
  - the Hue Sync TV app (Samsung, LG);
  - Hue Sync for PC and Mac;
  - Philips Ambilight + Hue and its successor AmbiScape;
  - Govee and Nanoleaf TV sync, and LIFX;
  - Hyperion, HyperHDR and PicCap/hyperion-webos on LG;
  - Home Assistant integrations for the Sync Box and Hue entertainment.
- **Sources:** about 130 searches and page reads, by three research agents (one per product group) and by hand. The key sources were opened again in a browser before use.
- **Counts:** rough. They count distinct threads, reviews or articles that mention a theme, not individual users. High means the theme came up in most product groups or many sources; low means one or two.
- **Reddit:** could not be opened directly, so Reddit is under-represented. Mirror sites were used only to confirm themes that other sources also show, and are not linked here.
- **Other gaps:**
  - app-store reviews of the TV apps are not reachable (the apps live in the TV stores);
  - GitHub issue searches of other projects were blocked by robots rules;
  - the Philips Hue FAQ pages show the questions but not the answers.
- **Older sources:** sources from before 2023 are marked with their year.
- **Wording:** everything is summarised in our own words, with at most one short quote per theme, attributed to the site.

## Top 10 lessons

1. **Philips left most LG owners out, and they are angry about it.** Hue first announced its Sync TV app for every LG TV on webOS 24, then limited it to 2024 models and apologised for the "misleading communication". Owners of 2022-2023 sets (C2, C3, G3) feel let down; some root their TV to get any sync at all. This is Glowcast's clearest audience, but Glowcast is tested on one 2023 model only. **Explain**, and get reports from those models (#115, #60).
2. **Price is the most common complaint about every Hue product.**
   - The Sync TV app costs 129.99 for one TV, or 2.99 a month.
   - The Sync Box 8K costs about 350.
   - Users call the app a rental, and dislike that its licence does not move to a new TV.

   Glowcast is free, has no subscription and no limit on TVs. **Explain.**
3. **What happens to the lights after a sync is a pain everywhere, also in Home Assistant.** Lights stay on in dim white, come back on by themselves, or report "off" while they are on, which breaks turn-off automations. Glowcast has the same root cause: the area's "When syncing stops" setting in the Hue app decides, and Home Assistant shows the state from before the sync (#142). **Explain** better, and decide whether Glowcast should own the restore (open question 1).
4. **Most Sync Box complaints come from sitting in the HDMI chain.**
   - Dolby Vision that does not sync or does not pass through;
   - eARC soundbars and CEC that switch devices on or off;
   - 4K120 and VRR limits.

   Glowcast is not in the HDMI chain, so none of this applies. **Explain.**
5. **The Sync Box's speed is its most praised quality, and Glowcast is slower.** Reviewers report no visible lag on the box. Glowcast gets about 3-4 pictures per second from the TV and fades between them, so it follows scenes, not cuts. We have no measured picture-to-light delay to quote. **Test** (new issue), then state it plainly.
6. **HDR and Dolby Vision make captured colours pale.** This is the top complaint about the closest open-source alternative on LG (PicCap with HyperHDR). We do not know how the TV's own capture call handles HDR and Dolby Vision. **Test** (new issue).
7. **Washed-out colours and "too busy" changes both come up.** People ask for a saturation slider, which Glowcast has (intensity 0-200 %). Others find constant colour changes tiring and prefer a subtle mode. This is evidence for #40 (livelier colours) and #16 (calm or lively). **Nothing new**; comments added there.
8. **Dark scenes go badly in other products.** Lights cut out on black (Sync Box), or flash and pick up reflections (cameras). Glowcast holds the last colours on sudden black, then keeps a faint warm glow. **Explain.**
9. **Home Assistant users want sync to be part of their automations.**
   - They ask for a sync status entity.
   - They want to start sync from Home Assistant, which the Hue TV app does not allow.
   - They want to start only for certain content or after dark, and build long debounce automations for this.

   Glowcast has the switch, the status sensor and the start-with-TV blueprint. Starting only after dark or for certain inputs is not offered. **Build:** a story for blueprint conditions.
10. **Camera products win on sources and lose on quality.** Philips launched its own TV camera in September 2026 (Hue Play Screen Sync, 119.99). Govee and Nanoleaf cameras work with the TV's own apps and DRM content. Reviewers fault them for:
    - weaker colours;
    - reflections in the camera image;
    - calibration;
    - no automatic start.

    Glowcast reads the picture's pixels (no reflections, no calibration) but gets no picture from DRM apps in the TV's own apps. **Explain**, and keep #45 (a camera as source) for the DRM case.

## Themes

Glowcast verdicts: **already better**, **the same problem**, **not relevant**, **a gap**.

Recommendations: **build**, **explain**, **test**, **nothing**.

### 1. Price, subscription and licence

| | |
|---|---|
| How often | about 12 sources - **high**, every Hue product, also Govee |
| Typical complaint | The Sync TV app costs 129.99 one-off per TV or 2.99 a month; the licence is tied to one TV. The Sync Box 8K (349.99 at launch) is seen as overpriced, on top of the lights and bridge. |
| Links | [hueblog: Sync Box 8K unveiled (2024)](https://hueblog.com/2024/09/05/for-349-99-hue-sync-box-8k-officially-unveiled/) - [hueblog: Sync TV app for LG, prices (2025)](https://hueblog.com/2025/01/07/hue-sync-tv-app-for-lg-is-now-official/) - [MacRumors forum on the LG app (2025)](https://forums.macrumors.com/threads/philips-hue-sync-app-expands-to-lg-tvs.2450185/page-2) |
| Glowcast today | **already better**: free (MIT), no subscription, one entry per TV with no limit on TVs (README, "Configuration"). It does need Home Assistant, a Hue bridge and an entertainment area. |
| Recommendation | **explain** - the strongest single message for the README and promotion. |

### 2. TV brand and model support

| | |
|---|---|
| How often | about 9 sources - **high** for LG owners |
| Typical complaint | The Hue Sync TV app was announced for all webOS 24 TVs, then limited to 2024 models and later; 2022-2023 owners were left out. Philips Hue said there are "no plans" for older LG TVs (hueblog). The app also misses some countries, and broke on some new Samsung models. |
| Links | [hueblog: not for older LG TVs after all (2025)](https://hueblog.com/2025/03/05/hue-sync-tv-app-not-for-older-lg-televisions/) - [Philips Hue: LG TVs from 2024 with webOS 24](https://www.philips-hue.com/nl-nl/explore-hue/works-with/lg-smart-tvs) - [Samsung Community: Sync TV app not working (2024-2025)](https://eu.community.samsung.com/t5/tv/the-philips-hue-sync-tv-app-is-not-working/td-p/9635079) |
| Glowcast today | **already better** for LG on paper: it uses the TV's local capture call, which does not depend on the model year. **But** it is confirmed on one model (2023 G3) only, and the capture call is undocumented by LG and could stop working after a firmware update (README, "Known limits"). It does not support other brands. |
| Recommendation | **explain** - "works on a 2023 LG G3; other webOS models should work, please report". Reports from 2020-2023 models are the most valuable feedback (comment on #115). |

### 3. Start and stop behaviour

| | |
|---|---|
| How often | about 12 sources - **high**, all products |
| Typical complaint | After a sync, lights stay on in dim white, turn back on by themselves, or stay on the last colour. In Home Assistant, lights report "off" while syncing and come back on seconds after sync stops, which breaks "turn off" automations. Auto start fails with sources that never really switch off; Hue's new camera has no auto start at all; Govee and Nanoleaf do not follow the TV. |
| Links | [Home Assistant Community: Sync Box integration (2024-2026)](https://community.home-assistant.io/t/philips-hue-play-hdmi-sync-box-custom-integration/204772?page=6) - [SmartThings Community: lightstrip turns on without reason (2025)](https://community.smartthings.com/t/hue-sync-tv-app-phillips-hue-white-color-ambiance-play-gradient-lightstrip-turn-on-without-reason/294241) - [hueblog: Play Screen Sync review (2026)](https://hueblog.com/2026/10/01/philips-hue-play-screen-sync-review-of-the-tv-camera/) |
| Glowcast today | Starting: **already better**. Glowcast starts from a switch, a dashboard or an automation, and the blueprint starts it when the TV turns on and can stop it when the TV turns off. When the TV turns off, the lights are released within about 1.2 s (architecture 2, #7). Restoring: **the same problem**. What the lights do after a stop follows the area's "When syncing stops" setting in the Hue app; with "Pause on the last colour" they keep the last colour, and Home Assistant shows their earlier state (README, "Starting and stopping"; #142). |
| Recommendation | **explain** - the README already says to choose "Last on-state"; put it in the user guide (#20) as a setup step, not a footnote. Whether Glowcast should restore the lights itself is open question 1. |

### 4. HDMI chain: Dolby Vision passthrough, eARC, CEC, 4K120, VRR

| | |
|---|---|
| How often | about 12 sources - **high** for the Sync Box |
| Typical complaint | Dolby Vision from an Apple TV does not sync, or only with a compatibility mode that lowers the picture. Soundbars on eARC and CEC switch devices on and off or crash the box. The first box had no 4K120; the 8K box needed firmware fixes for CEC and receivers. The box takes an HDMI port and cannot see the TV's own apps. |
| Links | [Apple Community: Dolby Vision and Hue Sync (2023-2024)](https://discussions.apple.com/thread/254520111) - [hueblog: Sync Box 8K firmware fixes (2025)](https://hueblog.com/2025/04/08/numerous-improvements-for-the-philips-hue-play-hdmi-sync-box-8k/) - [Sonos Community: Beam stops Hue sync (2022)](https://en.community.sonos.com/home-theater-229129/beam-gen-2-stops-my-philips-hue-synch-working-6865050) |
| Glowcast today | **not relevant**: Glowcast is not in the HDMI chain. It reads the picture from the TV over the network, so it sees what the screen shows (HDMI inputs tested; the TV's own apps without DRM not yet tested, DRM apps give no picture, theme 6) and cannot affect the picture, sound or CEC. |
| Recommendation | **explain** - "nothing between your devices and your TV". |

### 5. Delay and sync

| | |
|---|---|
| How often | about 12 sources - **medium**: mostly about cameras and apps; the Sync Box is praised |
| Typical complaint | Camera products (Govee AI mode, Nanoleaf 4D) and Matter-based AmbiScape (about 0.5 s) visibly trail the picture. The Sync Box is reviewed as having no noticeable lag; a few users report lag after a while. Users doubt a TV app keeps up with fast content. |
| Links | [TechRadar: Sync Box 8K review (2024)](https://www.techradar.com/home/small-appliances/philips-hue-play-hdmi-sync-box-8k-review) - [hueblog: AmbiScape details (2026)](https://hueblog.com/2026/03/18/ambiscape-all-the-details-on-the-successor-of-ambilighthue/) - [PC Gamer: Nanoleaf 4D review (2024)](https://www.pcgamer.com/hardware/lighting/nanoleaf-4d-screen-mirror-kit-review) |
| Glowcast today | **a gap** compared with the Sync Box: about 3-4 captures per second from the TV, with fades between them at 25 updates per second; it follows scenes, not every cut or flash (README, "Known limits"). The delay from a picture change to the light is not measured. |
| Recommendation | **test** - measure the picture-to-light delay (new issue) and publish the number, so a post can say how far behind the lights are. |

### 6. DRM apps and protected content

| | |
|---|---|
| How often | about 8 sources - **medium** |
| Typical complaint | HDMI boxes never see the TV's own apps. Hue Sync for PC and Mac and other software sync cannot capture Netflix and similar. On rooted LG TVs, PicCap cannot capture DRM apps either. Camera products are bought precisely because they work with everything. |
| Links | [MacRumors: Nanoleaf 4D review (2023)](https://www.macrumors.com/review/nanoleaf-4d-camera-and-light-strip) - [hyperion-webos issue #28 (2021-2022)](https://github.com/webosbrew/hyperion-webos/issues/28) - [hueblog: Play Screen Sync review (2026)](https://hueblog.com/2026/10/01/philips-hue-play-screen-sync-review-of-the-tv-camera/) |
| Glowcast today | **the same problem** as software sync: DRM apps in the TV's own apps give a black picture (tested with Prime Video); the same app on a streaming stick over HDMI works. Glowcast then lets the lights go after 7 s and follows again when a picture returns (README, "Known limits"; #8, #139). Other apps are left to user reports (decision on #8). |
| Recommendation | **explain** - say it up front, with the HDMI workaround. A camera as source (#45) would cover this case later; evidence added there. |

### 7. Colour accuracy and liveliness

| | |
|---|---|
| How often | about 13 sources - **high** |
| Typical complaint | Colours look pale or mostly white and yellow; users ask for a saturation setting. Camera products add wrong colours from reflections and need calibration. Some users find the constant colour changes tiring and prefer a subtle mode. |
| Links | [Hue Essentials Community: saturation (2021)](https://community.hueessentials.com/t/hue-sync-saturation/2002) - [9to5Google: Govee TV Backlight 3 Pro review (2025)](https://9to5google.com/2025/11/13/govee-tv-backlight-3-pro-review/) - [TechRadar: Nanoleaf 4D review (2024)](https://www.techradar.com/home/smart-lights/nanoleaf-4d-screen-mirror-and-lightstrip-kit-review) |
| Glowcast today | Saturation: **already better** than the requests - intensity runs from 0 to 200 % (README, "Configuration"). Reflections and calibration: **not relevant** - Glowcast reads the picture's pixels. Liveliness: **the same problem** - averaging a region tends towards grey (#40), and there is no calm or lively mode yet (#16). |
| Recommendation | **nothing** new: #40 and #16 cover it; evidence added on both. |

### 8. HDR and Dolby Vision colours

| | |
|---|---|
| How often | about 8 sources - **medium** (high for the LG open-source route) |
| Typical complaint | Captured HDR and Dolby Vision content looks pale, grey or dim. Fixing it on PicCap and HyperHDR means tone mapping, lookup tables and model-specific settings; the better setting adds delay. On the Sync Box, Dolby Vision needs a compatibility mode. |
| Links | [hyperion-webos issue #118 (2024)](https://github.com/webosbrew/hyperion-webos/issues/118) - [HyperHDR discussion #1016 (2024-2025)](https://github.com/awawa-dev/HyperHDR/discussions/1016) - [Apple Community: Dolby Vision and Hue Sync (2023-2024)](https://discussions.apple.com/thread/254520111) |
| Glowcast today | **unknown**: the TV's capture call returns a small JPEG of what the screen shows, but it was never checked with HDR10 or Dolby Vision content, from HDMI or from the TV's own apps. |
| Recommendation | **test** - new spike. If the capture is pale for HDR, this is the same problem as PicCap; if not, it is a point in Glowcast's favour. |

### 9. Dark scenes and flicker

| | |
|---|---|
| How often | about 8 sources - **medium** |
| Typical complaint | On black scenes the lights cut out (Sync Box), which distracts. Camera products flash randomly or light up from reflections when they should be dark. Noisy input gives rapid colour jumps. |
| Links | [MacRumors: Sync Box review (2019)](https://macrumors.com/review/philips-hue-play-hdmi-sync-box) - [Gizmodo: Nanoleaf 4D review (2023)](https://gizmodo.com/nanoleaf-4d-review-immersive-lighting-that-watches-you-1850783283) - [Hyperion docs: Philips Hue device](https://deploy-preview-28--hyperiondocs.netlify.app/user/leddevices/network/philipshue) |
| Glowcast today | **already better**: on sudden black the lights keep the last colours for 3.5 s, then fade to a faint warm glow instead of going off; a switch-off animation does not count as a picture (architecture 3.5, #38, #39). Every update fades over the capture interval. |
| Recommendation | **explain**. |

### 10. Reliability and disconnects

| | |
|---|---|
| How often | about 15 sources - **high** |
| Typical complaint | The Sync Box drops the picture or restarts; the Samsung Sync TV app hangs on "Something went wrong" after updates; Ambilight + Hue TVs lose or forget the bridge and do not pair with the Bridge Pro; grabbers and PicCap drop out after a while. |
| Links | [Samsung Community: Sync TV app not working (2024-2025)](https://eu.community.samsung.com/t5/tv/the-philips-hue-sync-tv-app-is-not-working/td-p/9635079) - [hueblog: Ambilight + Hue does not work with the Bridge Pro (2025)](https://hueblog.com/2025/09/15/ambilighthue-does-not-work-with-the-hue-bridge-pro/) - [HyperHDR issue #898 (2024)](https://github.com/awawa-dev/HyperHDR/issues/898) |
| Glowcast today | **already better on paper**: tested with a Bridge Pro; a lost stream is retried once and otherwise stops with a reason in the status sensor; a rejected key asks to pair again with the settings kept; a 95-minute soak test ran without errors (architecture 2, #19). One TV model and one long run only, so not proven at scale. |
| Recommendation | **nothing** - the user reports asked for in #60 are the test. |

### 11. Number of lights and entertainment area limits

| | |
|---|---|
| How often | about 10 sources - **medium** |
| Typical complaint | An entertainment area takes at most 10 lights, which frustrates people with many bulbs. Only one area can stream per bridge, so two rooms cannot sync at once. Lights in a streaming area cannot be used by other apps. AmbiScape syncs only 4 lights. |
| Links | [hueblog: bypassing the 10-lamp limit (2022, comments 2024)](https://hueblog.com/2022/04/05/hue-entertainment-how-the-10-lamp-limit-could-be-bypassed/) - [Hue Essentials Community: two areas at once (2022)](https://community.hueessentials.com/t/using-hue-sync-box-with-2-entertainment-areas-at-the-same-time/4059) - [hueblog: AmbiScape details (2026)](https://hueblog.com/2026/03/18/ambiscape-all-the-details-on-the-successor-of-ambilighthue/) |
| Glowcast today | **the same problem**: Glowcast streams to one entertainment area, one stream per bridge, and lights in the area only listen to Glowcast while it streams (README, "Known limits"). Loose lights outside the area are planned for v0.3 (#76), at a slower rate (#125). |
| Recommendation | **explain** - say these are Hue limits; #76 and #37 already cover what Glowcast can do about them. |

### 12. Setup and calibration

| | |
|---|---|
| How often | about 12 sources - **medium** |
| Typical complaint | Camera products need calibration again after every move, plus fiddly strips and cables. The open-source route on LG needs a rooted TV; rooting is patched on most recent webOS firmware, and DIY guides go out of date. The Sync Box itself is mostly praised as easy. |
| Links | [webOS Homebrew: rooting](https://www.webosbrew.org/rooting) - [faultmanager-autoroot status (2025)](https://github.com/throwaway96/faultmanager-autoroot) - [PC Gamer: Nanoleaf 4D review (2024)](https://www.pcgamer.com/hardware/lighting/nanoleaf-4d-screen-mirror-kit-review) |
| Glowcast today | **already better** than the open-source route: no rooting, no developer mode, and it reuses Home Assistant's existing pairing with the TV, so there is no prompt on the TV (README, "How it works"). **A gap** compared with plug-and-play products for people without Home Assistant, which is outside Glowcast's audience. |
| Recommendation | **explain** - "no rooting, no extra hardware". |

### 13. Privacy, cloud and accounts

| | |
|---|---|
| How often | about 7 sources - **low** |
| Typical complaint | The Hue account became mandatory in 2023; the Sync TV app needs an online TV; Govee's sync box keeps its settings in the cloud; some people dislike a camera pointed into the living room. |
| Links | [Home Assistant Community: Hue cloud account (2023)](https://community.home-assistant.io/t/philips-hue-will-force-users-to-upload-their-data-to-hue-cloud/617391?page=2) - [igor'sLAB: Govee AI Sync Box 2 test](https://www.igorslab.de/en/govee-ai-sync-box-2-test-ambilight-to-go-or-why-i-dont-regret-buying-it-despite-minor-hurdles/) |
| Glowcast today | **already better**: everything stays on the local network; frames are kept in memory and never stored or sent (README, "Privacy"). Glowcast's bridge key does give full access to the bridge, which the README says. |
| Recommendation | **explain**. |

### 14. Home Assistant and automation

| | |
|---|---|
| How often | about 14 sources - **medium**, but the most relevant for Glowcast |
| Typical complaint | Matter and HomeKit only give on, off or a fixed colour, and setting a colour cancels sync (Govee, Nanoleaf). The Hue Sync TV app cannot be started from Home Assistant. Users ask for a sync status entity, and build long automations to start sync only for films, only after dark, and without bouncing. |
| Links | [Home Assistant Community: overriding Hue light sync (2025-2026)](https://community.home-assistant.io/t/overriding-philips-hue-light-sync-with-home-assistant/892474) - [Home Assistant Community: Sync Box integration (2024-2026)](https://community.home-assistant.io/t/philips-hue-play-hdmi-sync-box-custom-integration/204772?page=6) - [Matter Alpha: Govee TV Backlight 3 Pro review (2025)](https://www.matteralpha.com/review/govee-tv-backlight-3-pro-review) |
| Glowcast today | **already better** for the basics: a switch, a status sensor with the stop reason, all settings as entities, and the start-with-TV blueprint (README, "Seeing what Glowcast does"). **A gap** for content and time: the blueprint cannot start only after dark or only for chosen inputs or apps. |
| Recommendation | **build** - a story for blueprint conditions (new issue). |

### 15. The app and the user interface

| | |
|---|---|
| How often | about 8 sources - **low** |
| Typical complaint | Separate apps (the old Hue Sync app, Govee's app with ads), entertainment areas that clutter the room list, crashes without error messages. |
| Links | [MacRumors: Sync Box review (2019)](https://macrumors.com/review/philips-hue-play-hdmi-sync-box) - [Notebookcheck: Nanoleaf PC Screen Mirror review (2025)](https://www.notebookcheck.net/Nanoleaf-PC-Screen-Mirror-Lightstrip-review-Monitor-backlighting-that-doesn-t-hesitate.997947.0.html) |
| Glowcast today | **not relevant**: Glowcast has no app of its own; it lives in Home Assistant's UI. It still needs an entertainment area set up in the Hue app. |
| Recommendation | **nothing**. |

## What users praise, and whether Glowcast has it

| Praise | Products | Glowcast |
|---|---|---|
| Immersion: the room feels part of the film or game | all | yes - that is the product |
| No visible lag, accurate colours | Sync Box, Govee HDMI box | partly: accurate pixels, but 3-4 pictures per second (theme 5) |
| Works with the TV's own apps, no HDMI port taken | Sync TV app, cameras, Ambilight | partly: no HDMI port taken; the TV's own apps without DRM not yet tested, DRM apps give no picture (theme 6) |
| Easy setup, all in one app, automatic input switching | Sync Box, Sync TV app | partly: easy for Home Assistant users; no input switching needed |
| A subtle or intensity mode for everyday viewing | Sync Box | partly: intensity 0-200 %; no calm or lively mode yet (#16) |
| Automatic start with TV power | Home Assistant Sync Box integration | yes: the blueprint |
| Free | Hue Sync for PC and Mac | yes |
| Gradient strips with several colours | Sync Box 8K, HyperHDR v2 | yes: segment by segment (README) |
| Extending sync to other lights in the room | Govee DreamView, Nanoleaf | yes within the area (up to Hue's 10 lights); more in v0.3 (#76) |
| Brightness and colour tuning per segment | Govee | partly: brightness and intensity for the whole area |

## Points for the promotion (#60) and the README

Wording only; the posts themselves stay with the product owner.

### Frustrations Glowcast already solves

- **"My 2022 or 2023 LG cannot run Hue Sync."** Philips Hue confirmed it will not come to those models (theme 2). Glowcast runs on a 2023 LG G3, without rooting.
- **"129.99 or a subscription, per TV."** Glowcast is free and open source, for as many TVs as you have (theme 1).
- **"The Sync Box breaks my Dolby Vision, eARC or CEC."** Glowcast is not in the HDMI chain at all (theme 4).
- **"Sync does not start with the TV, or cannot be started from Home Assistant."** Glowcast is a Home Assistant switch with a ready-made blueprint, a status sensor and its settings as entities (themes 3 and 14).
- **"The lights go dark or flash in dark scenes."** Glowcast holds the colours, then keeps a faint warm glow (theme 9).
- **"No cloud, no account, no camera in my living room."** Everything stays on the local network (theme 13).
- **"Colours are too pale."** Intensity goes up to 200 % (theme 7).

### Limits a post should not hide

- **LG webOS TVs only**, and **confirmed on one model** (a 2023 G3). Reports from other models are what we need most.
- **About 3-4 pictures per second.** The lights follow scenes, not every cut. It is ambient light, not a gaming accessory, and slower than the Sync Box.
- **No picture from DRM apps in the TV's own apps** (tested with Prime Video). The same app on a streaming stick over HDMI works.
- **Hue limits stay:** at most 10 lights in an entertainment area, one stream per bridge at a time, and the lights only listen to Glowcast while it syncs.
- **What the lights do after a stop** depends on the area's Hue app setting; choose "Last on-state".
- **It needs Home Assistant, HACS, a Hue bridge (v2 or Pro) and an entertainment area.**
- **The TV's capture call is undocumented by LG** and could stop working after a firmware update.
- **HDR and Dolby Vision colours are not tested yet.**

## New issues and comments

New Backlog issues (no milestone; the product owner plans them):

- #160 - Spike: HDR and Dolby Vision - does the TV's capture show the colours as on screen? (feature #73; theme 8)
- #161 - Start with the TV only when it fits: after dark, and for chosen inputs or apps (feature #75; theme 14; blocked by #92)

Evidence added to existing issues instead of new ones:

- #71 - measure the picture-to-light delay on the TV (theme 5); the test bench #134 does the same for the screen source
- #115 - reports from 2020-2023 LG models matter most (theme 2)
- #40 and #16 - pale colours, and calm versus lively (theme 7)
- #45 - a camera as source, mainly for DRM apps (themes 6 and 10 of the lessons)
- #60 - the promotion points below

No issue for the "explain" items; they are listed in "Points for the promotion" for the README and #60.

## Open questions for the product owner

1. **Restoring the lights after a stop.**
   - Now: what the lights do follows the area's Hue app setting (#142).
   - Option: Glowcast could restore the lights itself.
   - Trade-off: this works with any Hue app setting, but Glowcast would have to take ownership of the light state.
2. **DRM apps.** Should the next TV session (#129) also try one or two other apps (Netflix, Disney+, YouTube), so the README and posts can name more than Prime Video? The decision on #8 leaves this to user reports.
3. **Model years.** Should promotion speak to 2022-2023 LG owners directly before any model other than the G3 is confirmed? Alternatively, wait for the first reports from #115.
4. **Gaming.** Should the README say plainly that Glowcast is not meant for fast games, given the capture rate?

## Sources by product

Sources before 2023 are marked with their year. Reddit could not be opened directly (see "Method and limits").

### Philips Hue Play HDMI Sync Box (4K and 8K)

- [hueblog: Sync Box 8K unveiled (2024)](https://hueblog.com/2024/09/05/for-349-99-hue-sync-box-8k-officially-unveiled/)
- [hueblog: Sync Box 8K firmware fixes (2025)](https://hueblog.com/2025/04/08/numerous-improvements-for-the-philips-hue-play-hdmi-sync-box-8k/)
- [TechRadar: Sync Box 8K review (2024)](https://www.techradar.com/home/small-appliances/philips-hue-play-hdmi-sync-box-8k-review)
- [Best Buy: Sync Box 8K reviews](https://www.bestbuy.com/site/reviews/philips-hue-play-hdmi-sync-box-8k-black/6589719)
- [Best Buy: Sync Box reviews](https://www.bestbuy.com/site/reviews/philips-hue-play-hdmi-sync-box-black/6371722)
- [Coolblue: Sync Box 8K reviews (2025)](https://www.coolblue.nl/en/productreviews/954033)
- [MacRumors: Sync Box review (2019)](https://macrumors.com/review/philips-hue-play-hdmi-sync-box)
- [Apple Community: Dolby Vision and Hue Sync (2023-2024)](https://discussions.apple.com/thread/254520111)
- [Apple Community: Apple TV and the Sync Box (2025)](https://discussions.apple.com/thread/256059799)
- [Sonos Community: Beam stops Hue sync (2022)](https://en.community.sonos.com/home-theater-229129/beam-gen-2-stops-my-philips-hue-synch-working-6865050)
- [Hue Essentials Community: Sync Box turning off (2024)](https://community.hueessentials.com/t/hue-sync-box-turning-off/10602)
- [Hue Essentials Community: two areas at once (2022)](https://community.hueessentials.com/t/using-hue-sync-box-with-2-entertainment-areas-at-the-same-time/4059)

### Hue Sync TV app (Samsung, LG) and Hue Play Screen Sync

- [hueblog: Sync TV app for LG is official (2025)](https://hueblog.com/2025/01/07/hue-sync-tv-app-for-lg-is-now-official/)
- [hueblog: not for older LG TVs after all (2025)](https://hueblog.com/2025/03/05/hue-sync-tv-app-not-for-older-lg-televisions/)
- [hueblog: Sync TV app now on LG TVs (2025)](https://hueblog.com/2025/02/25/philips-hue-sync-tv-app-now-available-on-lg-tvs/)
- [hueblog: answers about the Sync TV app (2023)](https://hueblog.com/2023/01/05/the-important-answers-about-the-philips-hue-sync-tv-app/)
- [Philips Hue: Hue and LG](https://www.philips-hue.com/nl-nl/explore-hue/works-with/lg-smart-tvs)
- [MacRumors: subscription for the Samsung app (2024)](https://www.macrumors.com/2024/03/26/samsung-philips-hue-subscription-sync-app/)
- [MacRumors forum on the LG app (2025)](https://forums.macrumors.com/threads/philips-hue-sync-app-expands-to-lg-tvs.2450185/page-2)
- [TechHive: Sync TV app on Samsung (2023)](https://www.techhive.com/article/1445653/philips-hue-sync-tv-app-samsung.html)
- [Samsung Community: Sync TV app not working (2024-2025)](https://eu.community.samsung.com/t5/tv/the-philips-hue-sync-tv-app-is-not-working/td-p/9635079)
- [SmartThings Community: lightstrip turns on without reason (2025)](https://community.smartthings.com/t/hue-sync-tv-app-phillips-hue-white-color-ambiance-play-gradient-lightstrip-turn-on-without-reason/294241)
- [hueblog: Play Screen Sync review (2026)](https://hueblog.com/2026/10/01/philips-hue-play-screen-sync-review-of-the-tv-camera/)
- [Notebookcheck: Play Screen Sync and Sync Box 4K launch (2026)](https://www.notebookcheck.net/Philips-Hue-launches-new-Play-Screen-Sync-Play-HDMI-sync-box-4K-and-Play-gradient-TV-strip-lights.1386896.0.html)

### Hue Sync for PC and Mac, and Hue in general

- [Philips Hue: Hue Sync release notes](https://www.philips-hue.com/de-de/support/release-notes/hue-sync)
- [Hue Essentials Community: saturation (2021)](https://community.hueessentials.com/t/hue-sync-saturation/2002)
- [hueblog: bypassing the 10-lamp limit (2022, comments 2024)](https://hueblog.com/2022/04/05/hue-entertainment-how-the-10-lamp-limit-could-be-bypassed/)
- [Philips Hue: Sync with TV, Hue Sync app](https://www.philips-hue.com/en-gb/explore-hue/propositions/entertainment/sync-with-tv/sync-app)
- [Home Assistant Community: Hue cloud account (2023)](https://community.home-assistant.io/t/philips-hue-will-force-users-to-upload-their-data-to-hue-cloud/617391?page=2)

### Philips Ambilight + Hue and AmbiScape

- [hueblog: Ambilight + Hue does not work with the Bridge Pro (2025)](https://hueblog.com/2025/09/15/ambilighthue-does-not-work-with-the-hue-bridge-pro/)
- [hueblog: 2023 Ambilight TVs without Hue (2023)](https://hueblog.com/2023/07/04/2023-ambilight-tvs-no-longer-have-hue-integration/)
- [hueblog: AmbiScape details (2026)](https://hueblog.com/2026/03/18/ambiscape-all-the-details-on-the-successor-of-ambilighthue/)
- [iCulture: Ambilight TVs 2023 without Hue (2023)](https://www.iculture.nl/nieuws/ambilight-tvs-2023-geen-philips-hue/)
- [hueblog: Sync Box against real Ambilight (2020)](https://hueblog.com/2020/08/11/a-comparison-philips-hue-hdmi-sync-box-against-real-ambilight/)
- [hueblog: more Ambilight + Hue complaints (2021)](https://hueblog.com/2021/07/27/more-and-more-users-complain-ambilight-and-philips-hue-with-problems/)

### Govee, Nanoleaf and LIFX

- [9to5Google: Govee TV Backlight 3 Pro review (2025)](https://9to5google.com/2025/11/13/govee-tv-backlight-3-pro-review/)
- [Matter Alpha: Govee TV Backlight 3 Pro review (2025)](https://www.matteralpha.com/review/govee-tv-backlight-3-pro-review)
- [Reviewed: Govee TV Backlight 3 review (2026)](https://www.reviewed.com/televisions/content/govee-tv-backlight-3-review)
- [TechRadar: Govee AI Gaming Sync Box review (2023)](https://www.techradar.com/reviews/govee-ai-gaming-sync-box-kit-review-taking-pc-gaming-to-the-ultimate-level)
- [igor'sLAB: Govee AI Sync Box 2 test](https://www.igorslab.de/en/govee-ai-sync-box-2-test-ambilight-to-go-or-why-i-dont-regret-buying-it-despite-minor-hurdles/)
- [TechRadar: Nanoleaf 4D review (2024)](https://www.techradar.com/home/smart-lights/nanoleaf-4d-screen-mirror-and-lightstrip-kit-review)
- [PC Gamer: Nanoleaf 4D review (2024)](https://www.pcgamer.com/hardware/lighting/nanoleaf-4d-screen-mirror-kit-review)
- [MacRumors: Nanoleaf 4D review (2023)](https://www.macrumors.com/review/nanoleaf-4d-camera-and-light-strip)
- [Gizmodo: Nanoleaf 4D review (2023)](https://gizmodo.com/nanoleaf-4d-review-immersive-lighting-that-watches-you-1850783283)
- [Notebookcheck: Nanoleaf PC Screen Mirror review (2025)](https://www.notebookcheck.net/Nanoleaf-PC-Screen-Mirror-Lightstrip-review-Monitor-backlighting-that-doesn-t-hesitate.997947.0.html)
- [Google Home developers: Nanoleaf case study (2025)](https://developers.home.google.com/case-studies/nanoleaf)
- [Home Assistant: Govee lights local integration](https://www.home-assistant.io/integrations/govee_light_local)

### Hyperion, HyperHDR and PicCap on LG

- [hyperion-webos issue #118 (2024)](https://github.com/webosbrew/hyperion-webos/issues/118)
- [hyperion-webos issue #28 (2021-2022)](https://github.com/webosbrew/hyperion-webos/issues/28)
- [PicCap](https://github.com/TBSniller/piccap)
- [HyperHDR discussion #1016 (2024-2025)](https://github.com/awawa-dev/HyperHDR/discussions/1016)
- [HyperHDR issue #898 (2024)](https://github.com/awawa-dev/HyperHDR/issues/898)
- [HyperHDR discussion #1587: AmbiSun (2026)](https://github.com/awawa-dev/HyperHDR/discussions/1587)
- [hyperion.ng issue #1811 (2024)](https://github.com/hyperion-project/hyperion.ng/issues/1811)
- [webOS Homebrew: rooting](https://www.webosbrew.org/rooting)
- [faultmanager-autoroot status (2025)](https://github.com/throwaway96/faultmanager-autoroot)
- [Hyperion docs: Philips Hue device](https://deploy-preview-28--hyperiondocs.netlify.app/user/leddevices/network/philipshue)
- [Home Assistant Community: HyperHDR add-on (2022-2023)](https://community.home-assistant.io/t/home-assistant-add-on-hyperhdr/403934)

### Home Assistant integrations

- [Home Assistant Community: Sync Box integration (2024-2026)](https://community.home-assistant.io/t/philips-hue-play-hdmi-sync-box-custom-integration/204772?page=6)
- [huesyncbox integration](https://github.com/mvdwetering/huesyncbox)
- [Home Assistant Community: overriding Hue light sync (2025-2026)](https://community.home-assistant.io/t/overriding-philips-hue-light-sync-with-home-assistant/892474)
- [Home Assistant Community: sync status from the Hue bridge (2021)](https://community.home-assistant.io/t/get-husync-status-from-huebridge/305358)
- [Home Assistant: Hyperion integration](https://www.home-assistant.io/integrations/hyperion/)
- [Home Assistant Community: Ambilight sync for any light (2026)](https://community.home-assistant.io/t/philips-ambilight-sync-for-any-home-assistant-rgb-light/1025870)
- [Music Assistant: Hue entertainment plugin](https://www.music-assistant.io/plugins/hue-entertainment/)
