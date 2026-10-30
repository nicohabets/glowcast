# Which TVs and devices work with Glowcast's TV source?

Research for spike #185, done on 2026-10-30. Desk research and source code only; no TV or other device was used. Written so the website can reuse the table and the notes (#167).

Glowcast's TV source asks the TV for a screenshot with the call `tv/executeOneShot`, over the same local connection (and the same pairing) as Home Assistant's **LG webOS TV** integration (`webostv`). So a device works when Home Assistant's `webostv` integration can talk to it **and** it answers that call. We found no public LG documentation for the call. **inference**

**Tested on one TV only:** an LG OLED G3 from 2023 (it shipped with webOS 23; its current webOS version was not recorded), on 2026-10-28 (architecture section 2). Everything else below comes from code, documentation and other people's reports. Run the hardware check (#115) and send a hardware report (#60) to confirm your model.

## Evidence levels

As in #12:

- **verified: hardware** - tested by Glowcast on a real device (architecture section 2).
- **verified: source** - code we read, linked to a tag or commit.
- **verified: docs** - official documentation.
- **reported** - forum post, issue, gist, vendor page or blog, with its date.
- **inference** - our own conclusion, not tested and not stated by a source.

## The table

| Device | Likely | Why | Evidence |
|---|---|---|---|
| **LG TVs with webOS 22-25** (2022-2025 models) | **probably works** | Tested on a 2023 G3. The other versions of this generation probably use the same connection and the same call. | verified: hardware (G3); inference (the others) |
| **LG TVs with webOS 4.x-6** (2018-2021 models) | **probably works** | Another library for the same connection has offered this call since January 2023, without limiting it to certain models; community notes show it on webOS 3.8 (an early 2018 TV). No result per model found. | verified: source ([bscpylgtv](https://github.com/chros73/bscpylgtv/commit/14cfd81d2b07d980a1e3840639f0e026173572a5)); reported ([webOS notes gist](https://gist.github.com/Informatic/1983f2e501444cf1cbd182e50820d6c1), last active 2026); inference (works on 4.x-6) |
| **LG TVs with webOS 3.x** (2016-2018 models) | **unknown** | The call is present on webOS 3.8 (an early 2018 TV), according to the same notes. Nothing found for webOS 3.0 or 3.5. | reported (same gist) |
| **LG TVs with webOS 2** (2015 models) | **unknown** | Home Assistant's integration supports webOS 2.0 and later; no report of the call. | verified: docs ([HA webostv](https://www.home-assistant.io/integrations/webostv/)) |
| **LG TVs with webOS 1** (2014 models) | **probably not** | Home Assistant's integration does not support webOS 1. | verified: docs (same page) |
| **Any LG TV after its update to webOS 26** (2026 models; 2022-2025 models are getting it) | **unknown - needs Home Assistant 2026.8.3 or newer** | webOS 26 refuses the pairing that older Home Assistant versions send (details below). The screenshot permission is still in the pairing that 2026.8.3 and later send, but nobody has reported the call on webOS 26 yet. | verified: source; reported; inference (see "webOS 26") |
| **LG StanbyME, StanbyME 2** | **unknown** | A Home Assistant installer says StanbyME 2 works with the `webostv` integration; nothing on the screenshot call. | reported ([Selora Homes](https://selorahomes.com/devices/lg/stanbyme-2/), 2026, vendor page) |
| **LG CineBeam projectors with webOS** | **unknown** | They run LG's webOS (for example webOS 5 on the HU810P); no report with Home Assistant or the screenshot call. | verified: docs ([LG, HU810P](https://lg.com/us/PDF/CES-2021-New-HU810-Projector-Release-FINAL-US-Version-1-13-2021.pdf)) |
| **LG MyView smart monitors** | **unknown** | They run webOS 23; no report with Home Assistant or the screenshot call. | verified: docs ([LG, 32SR73U](https://www.lg.com/nz/monitors/ultrafine-uhd-4k-5k/32sr73u-w)) |
| **Other brands' TVs with LG's webOS** (webOS Hub: for example RCA, Konka, Aiwa, Hyundai, Seiki) | **unknown** | LG says licensees get the same features as its own TVs, but we found nothing on whether these TVs open the same local connection, or on Home Assistant with them. | verified: docs ([LG, 2021](https://lg.com/us/press-release/LG-webOS-TV-Licensing-Release.pdf)); reported ([NextTV, 2022](https://www.nexttv.com/news/lg-expands-webos-tvos-to-more-than-200-third-party-gadget-makers)) |
| **LG TVs before webOS** (NetCast, 2012-2013) | not this source | A different protocol with its own screenshot call; see research #12. | reported (#12, which cites LG's NetCast specification) |

"Probably works" means: the same software and the same call as the tested TV, with no report against it. It is not a promise.

## webOS 26: the pairing change

The biggest risk is not the age of a TV but the **webOS 26 update**. LG started with the 2025 G5, other 2025 models follow, and 2022-2024 models get it "later this year or in early 2027" ([ChannelNews](https://www.channelnews.com.au/lg-begins-webos-26-rollout-for-older-tvs/), 2026, reported).

- **What changed.** For years every program that talks to an LG TV this way (Home Assistant included) sent the same pairing request, signed with an old LG test certificate. webOS 26 blocks that certificate and refuses the pairing before the TV shows any prompt. **reported** ([lgpowercontrol 4.4 release notes](https://github.com/bassidus/lgpowercontrol/releases/tag/v4.4), 12 September; the page shows no year)
- **The fix in Home Assistant.** `aiowebostv` 0.9.2 (21 Aug 2026) moves all permissions into one unsigned list and drops the signature ([commit `2eff739`](https://github.com/home-assistant-libs/aiowebostv/commit/2eff73981b12b22e147ae224b34172a9eb05312c), [PR #719](https://github.com/home-assistant-libs/aiowebostv/pull/719)). Home Assistant ships 0.9.2 from **2026.8.3** and 0.10.0 from 2026.9.0 (2026.8.2 still has 0.9.1). **verified: source** (`webostv/manifest.json` per release tag)
- **What it means for Glowcast.** Glowcast uses Home Assistant's own `aiowebostv` and the TV's pairing from the `webostv` integration. On a TV with webOS 26, Home Assistant older than 2026.8.3 cannot pair, so Glowcast cannot either. Glowcast's documented minimum is 2025.7.2 (D17), which is fine for older webOS versions only. **inference** from the source above
- **The screenshot permission.** `CONTROL_TV_SCREEN` was already in the unsigned list before the change ([v0.9.1](https://github.com/home-assistant-libs/aiowebostv/blob/v0.9.1/aiowebostv/handshake.py), [v0.9.2](https://github.com/home-assistant-libs/aiowebostv/blob/v0.9.2/aiowebostv/handshake.py)), so it is not among the ten permissions that PR #719 lists as lost (those were only in the signed part). **verified: source**. In the PR, testers on a 2026 C6 and a 2023 C3 report that writing settings no longer works; screenshots are not mentioned. **reported** (PR #719, August 2026). Whether webOS 26 grants the screenshot permission: **inference** (probably, since it is in the part the user accepts on the TV).
- **Glowcast's own test already used the new request.** The G3 test of 2026-10-28 ran on Home Assistant 2026.10.4 with `aiowebostv` 0.10.0 and captured fine. **verified: hardware** (architecture section 2). That TV's webOS version was not recorded.
- **Not known:** whether the screenshot call answers on a TV that runs webOS 26. Nobody has reported it either way. The first webOS 26 report is the most valuable one.

## Known limits on any webOS TV

- **Apps with copy protection** (for example Prime Video in the TV's own app) give black screenshots: tiny, all-black frames at about 6.9 per second. The same film from an HDMI device works. **verified: hardware** (architecture section 2, #5). Other projects that capture on rooted LG TVs report the same: no capture of protected apps, HDMI inputs fine. **reported** ([PicCap README](https://github.com/TBSniller/piccap/blob/3501b86bea4613f67af801d1b79d869c118ac92f/README.md), July 2025)
- **Games:** a game console on HDMI captures fine, about 3.5 per second, and 55 minutes of play gave no black frames except at transitions. **verified: hardware** (architecture section 2, #68). Whether the TV's game mode or Game Optimizer was on was not recorded, so their effect is **unknown**.
- **LG's AI picture features** (AI Picture Pro, AI Brightness, AI Game Optimizer and similar) use the same capture hardware on newer models; on rooted capture they cause dropouts, and switching them off helps. **reported** (PicCap README). Whether they disturb the screenshot call too is **unknown** (inference: possible).
- **HDR and Dolby Vision:** the open-source capture on rooted LG TVs reads the TV's HDR type and passes it on, so its LED software can switch its tone mapping per dynamic range. **verified: source** ([hyperion-webos `service.c`](https://github.com/webosbrew/hyperion-webos/blob/00c932f092084b234b3f619b060a98fa134c12bd/src/service.c)). Users of that route report pale HDR colours. **reported** (#159, lesson 6). So HDR pictures probably look paler in the screenshot call too. **inference**. HDR10 and Dolby Vision are not measured on the G3 yet (#160).
- **Picture size:** the G3 returns a 960x540 JPEG of about 80 KB and ignores the size parameters. **verified: hardware**. One other library removed its size and format parameters in October 2025 and documents the result as 960x540 JPG. **verified: source** ([bscpylgtv](https://github.com/chros73/bscpylgtv/commit/fbdbdd9fb5d6104bb2e360cb019195ad46a20d19)). Other models may differ (unknown).
- **Speed** on the G3: about 4.1 screenshots per second on their own, about 3.5 on an HDMI game console, and 2.87-2.90 per second while Glowcast drives the lights (architecture section 2, #2 and #5). **verified: hardware**. No numbers found for other models.
- **An undocumented call** can disappear in a firmware update. **inference** (architecture section 2, "Not yet tested")

## What Glowcast does today when a TV refuses the call

A refused screenshot counts as a failed capture; after five in a row (and a short wait) Glowcast stops with the reason "TV off" ([`sources/webos.py`](https://github.com/nicohabets/glowcast/blob/3425b149323a43c32cd6f8cb8c76134a1c081925/custom_components/glowcast/sources/webos.py), [`core/pipeline.py`](https://github.com/nicohabets/glowcast/blob/3425b149323a43c32cd6f8cb8c76134a1c081925/custom_components/glowcast/core/pipeline.py), at commit `3425b14`). **verified: source**. On a model that does not support the call, the user would see "TV off" instead of "this TV does not allow screenshots". Follow-up: #214.

## Not verified

- Any device other than the 2023 G3 on real hardware, and the G3's current webOS version.
- The screenshot call on webOS 26.
- Whether StanbyME, CineBeam, MyView and webOS Hub TVs from other brands answer the call, or work with Home Assistant at all (one vendor claim for StanbyME 2).
- LG's developer documentation: we could not check LG's developer site.
- Whether a TV refuses the call with "no such method" or with "insufficient permissions" when it does not support it.
