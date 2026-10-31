# Speed and hardware

How fast the lights follow the picture, how often the TV can be captured, and what Glowcast needs from the computer Home Assistant runs on.
Each section is one research question, measured on the test TV (a 2023 LG G3) or the test bench, or estimated where no hardware was available.

## The delay from the picture to the light { #delay }

**The question:** how long does it take from a change in the picture until the lights show it?

**The answer:** on the test bench about half a second when it was first measured; with the cut fade of v0.4.0, 336-350 ms against 417-485 ms without it the same evening (medians). Not measured on the TV yet.

The test bench runs Glowcast in a local Home Assistant with a computer screen as the picture, a real Hue light pair and a webcam that judges the lights. The delay runs from the picture change to the light passing halfway. The runs differ in date, light and scenes, so compare only runs from the same date and time of day.

| Run | Mode | Median | Range / p90 |
|---|---|---|---|
| 30-10-2026 morning, update rate High (#134) | pattern (includes the camera's own latency, up to about 75 ms) | 537 ms | 330-697 ms, p90 651 ms (22 changes) |
| 30-10-2026 morning, update rate High (#134) | screen (the camera's latency cancels out) | 488 ms | 335-688 ms, p90 664 ms (16 changes) |
| 30-10-2026 evening, vivid scenes, before the cut fade (five runs, #258) | screen | 423-464 ms | p90 573-653 ms |
| 30-10-2026 evening, vivid scenes, with the cut fade (#258) | screen | 336 ms | p90 515 ms |
| 30-10-2026 evening, vivid scenes, before the cut fade (five runs, #258) | pattern | 417-485 ms | p90 592-664 ms |
| 30-10-2026 evening, vivid scenes, with the cut fade (#258) | pattern | 350 ms | p90 414 ms |

Other timings from the same bench (#134):

| Moment | Result |
|---|---|
| Bridge reports the area active after the switch-on | 0.4-0.6 s |
| Near-black picture: the glow shows | 4.0-4.2 s after the change |
| All-zero picture: lights released / back in their own state | 8.4-8.7 s / 9.5-9.9 s after the change |
| Switch off: area inactive / lights back | 1.2-1.3 s / 2.3-2.6 s |

The cut fade: a light whose colour jumps by a lot fades in 80 ms instead of over the whole interval between two captures. The details are on [Colours and fades](colours.md). The bench is not the TV: on the TV the capture itself takes longer (see [the capture rate](#capture-rate-on-the-tv)), and the cut fade is not measured on the TV yet.

**What Glowcast does since:** the test bench came in v0.2.0 and checks every pull request that changes what the lights get; the cut fade came in v0.4.0.

**More:** [story #134](https://github.com/nicohabets/glowcast/issues/134) and its pull request [#154](https://github.com/nicohabets/glowcast/pull/154); the cut fade in [spike #35](https://github.com/nicohabets/glowcast/issues/35) and [story #258](https://github.com/nicohabets/glowcast/issues/258), pull request [#259](https://github.com/nicohabets/glowcast/pull/259).

## Overlapping captures { #overlapping-captures }

**The question:** can Glowcast start the next capture before the last one has finished, and does that make the lights follow sooner?

**The answer:** no gain on the bench; whether the TV would gain depends on how it handles two requests at once, which is not measured yet.

A model of the pipeline, calibrated to the TV's measured capture times (delay from a scene cut to the light halfway):

| Variant (model) | Captures per second | Median | p90 |
|---|---|---|---|
| High, one at a time (today) | 2.9 | 458 ms | 649 ms |
| High, two in flight, the TV in parallel | 3.0 | 445 ms | 638 ms |
| Maximum, one at a time (today) | 4.1 | 408 ms | 583 ms |
| Maximum, two in flight, the TV in parallel | 7.0 | 360 ms | 484 ms |
| Maximum, two in flight, the TV merges them | 3.8 | 407 ms | 582 ms |

The test bench, 31-10-2026, screen mode, 51 light changes measured per run:

| Run | Captures per second | Median | p90 |
|---|---|---|---|
| High, one at a time | 3 (by its pace) | 384 ms | 496 ms |
| High, two in flight | 3 (by its pace) | 400 ms | 482 ms |
| Maximum, 0.244 s per capture (the TV's time), one at a time | 3.0 | 480 ms | 608 ms |
| Maximum, 0.244 s per capture, two in flight | 6.0 | 528 ms | 624 ms |

- The best case in the model saves about 50 ms on the median and 100 ms on the p90, only at Maximum; at High there is nothing to overlap.
- On the bench two in flight doubled the frames but did not shorten the delay. The screen helper is not a TV (it shares one grab between requests within 50 ms), so this says nothing about the TV.
- The only report from another TV (a 2020 CX) says parallel captures are merged or queued.

**What Glowcast does since:** no change. A short measurement on the TV is [#300](https://github.com/nicohabets/glowcast/issues/300) (open); it decides whether a story follows.

**More:** [spike #290](https://github.com/nicohabets/glowcast/issues/290) ([the result](https://github.com/nicohabets/glowcast/issues/290#issuecomment-6542010339)), recorded in pull request [#305](https://github.com/nicohabets/glowcast/pull/305).

## A Raspberry Pi 4 { #raspberry-pi-4 }

**The question:** does Glowcast run well on a Raspberry Pi 4 at every update rate?

**The answer:** likely yes, at every update rate, with a margin of about 3 times. This is an estimate from an emulated Pi 4, not measured on a Pi.

| Measurement | Result |
|---|---|
| CPU per capture on the bench laptop (download, decode, colour engine) | about 4 ms |
| A Pi 4 core against one laptop core | about 8-10 times slower (estimated from PyBench and Geekbench 6) |
| Home Assistant's use of one emulated Pi core at Maximum | about 31 % (6-7 % idle), so Glowcast about a quarter |
| Sends and captures per second at Maximum, emulated | 25 and 4 |
| Event loop lag, emulated | under 2 ms (p95) |
| Extra memory with Glowcast streaming | about 5 MB |
| The rate held down to / broke at | a quota of 3 % / 2 % of a laptop core (then 17 sends and 3.2 captures per second) |

The emulated Pi is the bench's own local Home Assistant limited to 10 % of one laptop core: all of Home Assistant on one Pi core, with the TV source's download and decoding added.

- The rate is set by the TV, not by the Pi: one capture takes about 250 ms on the TV.
- The main uncertainty is the rest of a real Home Assistant on the same core (recorder, other integrations, add-ons), then the Pi's Python version, clock and cooling.

**What Glowcast does since:** no change; the requirements stay as they are. The estimate is recorded in the architecture, not as a verified fact.

**More:** [spike #47](https://github.com/nicohabets/glowcast/issues/47) ([the result](https://github.com/nicohabets/glowcast/issues/47#issuecomment-6541308693)), recorded in pull request [#305](https://github.com/nicohabets/glowcast/pull/305).

## The capture rate on the TV { #capture-rate-on-the-tv }

**The question:** how often can Glowcast capture the TV's picture?

**The answer:** about 4 captures per second as fast as it goes, including the download and decoding; about 3 per second at update rate High.

| Measurement | Result |
|---|---|
| One after another, with download and decode to 64x36 | about 4.1 per second |
| An earlier test without decoding | about 3.4 per second |
| The pipeline at update rate High, over 27 s | 2.87-2.90 per second |
| A game console over HDMI | about 3.5 per second, 10 of 10 captures |
| A protected app (all-zero picture, a tiny JPEG) | about 6.9 per second |
| The frame | 960x540 JPEG, about 80 KB; the requested size is ignored |
| Source start / start plus first frame | 0.20-0.50 s (median 0.22 s) / 0.56 s |
| A dropped connection reconnected in | 0.34 s |

**What Glowcast does since:** v0.1.0. The update rate is Low, Medium, High or Maximum: 1, 2 or 3 captures per second, or as fast as the TV allows. Since v0.2.0 a Capture rate sensor (diagnostic, off by default) shows how many pictures per second Glowcast gets.

**More:** [story #2](https://github.com/nicohabets/glowcast/issues/2) and its pull request [#26](https://github.com/nicohabets/glowcast/pull/26); the pipeline on real hardware in [story #5](https://github.com/nicohabets/glowcast/issues/5), pull request [#36](https://github.com/nicohabets/glowcast/pull/36).
