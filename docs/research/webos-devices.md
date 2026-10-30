# Which TVs and devices work with Glowcast's TV source?

Research for spike #185, done on 2026-10-30. Desk research and source code only; no TV or other device was used. Written so the website can reuse the table and the notes (#167).

Glowcast's TV source asks the TV for a screenshot with the call `tv/executeOneShot`, over the same local connection (and the same pairing) as Home Assistant's **LG webOS TV** integration (`webostv`). So a device works when Home Assistant's `webostv` integration can talk to it **and** it answers that call. The call is undocumented by LG.

**Tested on one TV only:** an LG OLED G3 from 2023 (webOS 23), on 2026-10-28 (architecture section 2). Everything else below comes from code, documentation and other people's reports. Run the hardware check (#115) and send a hardware report (#60) to confirm your model.

## Evidence levels

As in #12:

- **verified: hardware** - tested by Glowcast on a real device (architecture section 2).
- **verified: source** - code we read, linked to a tag or commit.
- **verified: docs** - official documentation.
- **reported** - forum post, issue, gist or blog, with its date.
- **inference** - our own conclusion, not tested and not stated by a source.

## The table

| Device | Likely | Why | Evidence |
|---|---|---|---|
| **LG TVs with webOS 22-25** (2022-2025 models) | **probably works** | Tested on a 2023 G3 with webOS 23. The later and earlier webOS versions of this generation use the same connection and the same call. | verified: hardware (G3); inference (the others) |
| **LG TVs with webOS 4.x-6** (2018-2021 models) | **probably works** | Other libraries for the same connection have offered this screenshot call since early 2023; community notes document it from webOS 3.8 on, with comments from webOS 4.4 and a 2020 CX. No result per model found. | verified: source ([bscpylgtv](https://github.com/chros73/bscpylgtv/commit/14cfd81d2b07d980a1e3840639f0e026173572a5)); reported ([webOS notes gist](https://gist.github.com/Informatic/1983f2e501444cf1cbd182e50820d6c1), last active 2026) |
| **LG TVs with webOS 3.x** (2016-2017 models) | **unknown** | The same community notes (webOS 3.8, an early 2018 TV) show the call. Nothing found for webOS 3.0 or 3.5. | reported (same gist) |
| **LG TVs with webOS 1-2** (2014-2015 models) | **unknown** | Home Assistant's integration supports webOS 2.0 and later; no report of the call on these versions. | verified: docs ([HA webostv](https://www.home-assistant.io/integrations/webostv/)) |
| **Any LG TV after its update to webOS 26** (2026 models; 2022-2025 models are getting it) | **unknown - needs Home Assistant 2026.8.3 or newer** | webOS 26 refuses the pairing that older Home Assistant versions send (details below). The screenshot permission is still in the pairing that 2026.8.3 and later send, so the call probably still answers, but nobody has reported it yet. | verified: source; reported; inference (see "webOS 26") |
| **LG StanbyME, StanbyME 2** | **unknown** | A Home Assistant installer says StanbyME 2 works with the `webostv` integration; nothing on the screenshot call. | reported ([Selora Homes](https://selorahomes.com/devices/lg/stanbyme-2/), 2026, vendor page) |
| **LG CineBeam projectors with webOS** | **unknown** | They run LG's webOS (for example webOS 5 on the HU810P); no report with Home Assistant or the screenshot call. | verified: docs ([LG, HU810P](https://lg.com/us/PDF/CES-2021-New-HU810-Projector-Release-FINAL-US-Version-1-13-2021.pdf)) |
| **LG MyView smart monitors** | **unknown** | They run webOS 23, the same version as the tested G3; no report with Home Assistant or the screenshot call. | verified: docs ([LG, 32SR73U](https://www.lg.com/nz/monitors/ultrafine-uhd-4k-5k/32sr73u-w)) |
| **Other brands' TVs with LG's webOS** (webOS Hub: for example RCA, Konka, Aiwa, Hyundai, Seiki, Blaupunkt, Polaroid) | **unknown** | LG licenses webOS with the same features as its own TVs, but we found nothing on whether these TVs open the same local connection, or on Home Assistant with them. | verified: docs ([LG, 2021](https://lg.com/us/press-release/LG-webOS-TV-Licensing-Release.pdf)); reported ([NextTV, 2022](https://www.nexttv.com/news/lg-expands-webos-tvos-to-more-than-200-third-party-gadget-makers)) |
| **LG TVs before webOS** (NetCast, 2012-2013) | not this source | A different protocol with its own screenshot call; see #12. | verified: docs (#12) |

"Probably works" means: the same software and the same call as the tested TV, with no report against it. It is not a promise.

## webOS 26: the pairing change

The biggest risk is not the age of a TV but the **webOS 26 update**, which LG started rolling out to 2025 models and plans for 2022-2024 models "later this year or in early 2027" ([ChannelNews](https://www.channelnews.com.au/lg-begins-webos-26-rollout-for-older-tvs/), 2026, reported).

- **What changed.** For years every program that talks to an LG TV this way (Home Assistant included) sent the same pairing request, signed with an old LG test certificate. webOS 26 blocks that certificate and refuses the pairing before the TV shows any prompt. **reported** ([lgpowercontrol 4.4 release notes](https://github.com/bassidus/lgpowercontrol/releases/tag/v4.4), September 2026)
- **The fix in Home Assistant.** `aiowebostv` 0.9.2 (21 Aug 2026) sends the pairing request without that signature ([commit `2eff739`](https://github.com/home-assistant-libs/aiowebostv/commit/2eff73981b12b22e147ae224b34172a9eb05312c), [PR #719](https://github.com/home-assistant-libs/aiowebostv/pull/719)). Home Assistant ships 0.9.2 from **2026.8.3** and 0.10.0 from 2026.9.0 (2026.8.2 still has 0.9.1). **verified: source** (`webostv/manifest.json` per release tag)
- **What it means for Glowcast.** Glowcast uses Home Assistant's own `aiowebostv` and the TV's pairing from the `webostv` integration. On a TV with webOS 26, Home Assistant older than 2026.8.3 cannot pair, so Glowcast cannot either. Glowcast's documented minimum is 2025.7.2 (D17), which is fine for older webOS versions only. **inference** from the source above
- **The screenshot permission.** The unsigned request keeps only the permissions the user accepts on the TV, not the extra ones the signature gave. The screenshot permission (`CONTROL_TV_SCREEN`) was always among the accepted ones, so it is still asked for. **verified: source** ([manifest before](https://github.com/home-assistant-libs/aiowebostv/blob/v0.9.1/aiowebostv/handshake.py), [after](https://github.com/home-assistant-libs/aiowebostv/blob/v0.10.0/aiowebostv/handshake.py)). The PR lists which permissions were lost; the screenshot one is not among them, and one tester on a 2026 C6 found that writing settings no longer worked. **reported** (PR #719, August 2026)
- **Glowcast's own test already used the new request.** The G3 test of 2026-10-28 ran on Home Assistant 2026.10.4 with `aiowebostv` 0.10.0 and captured fine; that TV was still on webOS 23. **verified: hardware** (architecture section 2)
- **Not known:** whether the screenshot call answers on a TV that runs webOS 26. Nobody has reported it either way. The first webOS 26 report is the most valuable one.

## Known limits on any webOS TV

- **Apps with copy protection** (for example Prime Video in the TV's own app) give black screenshots; the same film from an HDMI device works. **verified: hardware** (architecture section 2, #5). Other projects that capture on rooted LG TVs report the same: no capture of protected apps, HDMI inputs fine. **reported** ([PicCap README](https://github.com/TBSniller/piccap))
- **LG's AI picture features** (AI Picture Pro, AI Brightness, AI Game Optimizer and similar) use the same capture hardware on newer models; on rooted capture they cause dropouts, and switching them off helps. **reported** (PicCap README). Whether they disturb the screenshot call too is **unknown** (inference: possible).
- **HDR and Dolby Vision:** the open-source capture on rooted LG TVs reads the TV's HDR type and passes it on, so its LED software can switch its tone mapping per dynamic range. **verified: source** ([hyperion-webos `service.c`](https://github.com/webosbrew/hyperion-webos/blob/00c932f092084b234b3f619b060a98fa134c12bd/src/service.c)). Users of that route report pale HDR colours (#159, lesson 6). So HDR pictures probably look paler in the screenshot call too. **inference**; not measured on the G3 yet (#160).
- **Picture size:** the G3 returns 960x540 and ignores the size parameters. **verified: hardware**. Other libraries document 960x540 as the default and list several formats and capture methods. **verified: source** ([bscpylgtv](https://github.com/chros73/bscpylgtv/commit/fbdbdd9fb5d6104bb2e360cb019195ad46a20d19)). Other models may differ (unknown).
- **Speed:** about 4.1 screenshots per second on the G3 (verified: hardware). No numbers found for other models.
- **An undocumented call** can disappear in a firmware update. **inference** (architecture section 2, "Not yet tested")

## What Glowcast does today when a TV refuses the call

A refused screenshot counts as a failed capture; after several in a row Glowcast stops with the reason "TV off" ([`sources/webos.py`](https://github.com/nicohabets/glowcast/blob/main/custom_components/glowcast/sources/webos.py), [`core/pipeline.py`](https://github.com/nicohabets/glowcast/blob/main/custom_components/glowcast/core/pipeline.py)). **verified: source**. On a model that does not support the call, the user would see "TV off" instead of "this TV does not allow screenshots". A follow-up story is linked from #185.

## Not verified

- Any device other than the 2023 G3 on real hardware.
- The screenshot call on webOS 26.
- Whether StanbyME, CineBeam, MyView and webOS Hub TVs from other brands answer the call, or work with Home Assistant at all (one vendor claim for StanbyME 2).
- How LG's own webOS developer documentation describes this call: the call is not in the public documentation, and the developer site was not reachable from this session.
- Whether a TV refuses the call with "no such method" or with "insufficient permissions" when it does not support it.
