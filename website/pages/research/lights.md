# Lights, bridges and other apps

How fast lights outside an entertainment area can follow, what a Hue bridge allows, and how Glowcast behaves next to other apps.
Each section is one research question, measured on the product owner's lights, the test TV or the test bench.

## How fast Home Assistant lights can follow { #home-assistant-lights-rate }

**The question:** how many colour updates per second can a light take through Home Assistant's normal light actions, without an entertainment stream?

**The answer:** about one per second per light, and at most 4 lights per Hue bridge or Thread network; above that the lights fall behind, and Home Assistant's reported state does not show it.

| Light | Measured | Result |
|---|---|---|
| Hue bulbs (4) through the Hue integration | one call per light | call returns in about 130 ms (p95 about 200 ms) up to 20 calls per second; 325 ms at 40 per second |
| Hue bulbs (4) | 5 updates per second per light (20 commands per second) | the lights changed only about twice per second; other lights on the same bridge stopped responding until the run ended |
| Hue room as one target | one command for all members | call about 70 ms; 2 updates per second without a backlog |
| Matter bulb (1) on Thread | 1 / 2 / 5 / 10 updates per second | clean at 1; updates lost at 2, more at 5; chaotic at 10 |
| Matter bulb | `transition` 1 s | a smooth fade |

- A Hue light reports its new state in Home Assistant at most about once per second, and a Matter light reported updates the light itself had lost. So the reported state cannot tell whether a light keeps up; only watching the lights does.
- Without `transition` the changes show as steps; with `transition` equal to the interval, as a fade.
- Not measured: Zigbee (ZHA, Zigbee2MQTT), WLED and other kinds, and several Matter lights at once.

**What Glowcast does since:** v0.3.0. Home Assistant lights get 1 update per second with a 1 s fade, and at most 4 commands per second go to each Hue bridge and each Thread network (a Hue room or zone counts as one light), so with more lights each changes less often.

**More:** [spike #125](https://github.com/nicohabets/glowcast/issues/125) and its pull request [#131](https://github.com/nicohabets/glowcast/pull/131); built in [story #9](https://github.com/nicohabets/glowcast/issues/9), pull request [#173](https://github.com/nicohabets/glowcast/pull/173).

## One stream per bridge { #one-stream-per-bridge }

**The question:** can one Hue bridge stream to more than one entertainment area at a time, for example for two TVs?

**The answer:** no; the Hue Bridge Pro reports `max_streams: 1` itself, and a second Glowcast stops with "busy" without touching the other stream.

| Check | Result |
|---|---|
| Bridge | Hue Bridge Pro (BSB003), software 6.3.2071556020 |
| `max_streams` in its entertainment resource | 1 |
| Areas may share lights | yes (only one can stream at a time) |
| A second Glowcast switched on while another app streams another area | 5 tries |
| Stopped with `output_busy` after | 0.20-0.21 s |
| Its own area active | never |
| The other area and its streamer | unchanged in every read (every 0.25 s) |

Philips Hue's support FAQ also says one active entertainment area per bridge (desk research; the page names no model).

**What Glowcast does since:** no change. Since v0.1.0 Glowcast reads all areas first and does not start while another app streams on the bridge; "one stream per bridge" is a known limit. A clearer message for this case is [#299](https://github.com/nicohabets/glowcast/issues/299) (planned for v0.5.1).

**More:** [spike #37](https://github.com/nicohabets/glowcast/issues/37) (closed 31-10-2026), recorded in pull request [#305](https://github.com/nicohabets/glowcast/pull/305).

## Next to a Hue Sync Box { #next-to-a-sync-box }

**The question:** can Glowcast use a Sync Box's colours, and when it drives other lights next to the box, does its notice about a light in the box's area show up in time?

**The answer:** the box does not share its colours, but Glowcast can drive Home Assistant lights next to it; on an emulated box the notice showed in 0 of 10 tries with the "Light sync" switch as trigger and in 10 of 10 with the Hue area's binary sensor.

The first spike (#202, desk research, no Sync Box) found that the box's local API holds only state (sync on or off, mode, input, brightness, area), no colours, and that Glowcast with Home Assistant lights only never starts or stops a stream, so it cannot stop the box's.

The later check (#272) used an emulated Sync Box on the test bench: a second client streams the bench area, as a box does, and a helper switch stands in for the box's "Light sync" switch. 10 tries per trigger:

| | "Light sync" switch as trigger | Hue binary sensor as trigger |
|---|---|---|
| Bridge reports the area active | 0.21-0.30 s | 0.19-0.26 s |
| Glowcast claims the lights | 0.01 s, before the area is active | after the area is active |
| The notice shown | 0 of 10 | 10 of 10 |
| The notice still there 6 s after both stopped | - (never shown) | 10 of 10 |
| The box's stream kept until its own stop | 10 of 10 | 10 of 10 |

- With the binary sensor, the notice stayed after the box and Glowcast had stopped, every time.
- The binary sensor turned on 0.17-1.14 s after the stream's start was called (median 0.21-0.25 s).
- The `huesyncbox` integration polls the box every 3 s, and its switch follows the box's mode, not its stream (published source); how that times against a real box's stream is not known (an inference, not measured).

**What Glowcast does since:** v0.3.1 published the research; v0.5.0 added the "Follow a Hue Sync Box" blueprint and the notice. After #272 the product owner chose to check the notice again a few seconds after the claim and remove it when Glowcast stops (option (a)); that is not built yet.

**More:** [spike #202](https://github.com/nicohabets/glowcast/issues/202) and its pull request [#211](https://github.com/nicohabets/glowcast/pull/211); [#272](https://github.com/nicohabets/glowcast/issues/272) ([the result](https://github.com/nicohabets/glowcast/issues/272#issuecomment-6541587556)); the blueprint and notice in [#219](https://github.com/nicohabets/glowcast/issues/219), pull request [#252](https://github.com/nicohabets/glowcast/pull/252).

## The light test and a late state report { #light-test-late-report }

**The question:** when Glowcast is switched on right after Test lights, can a late state report make it save a test colour as a Home Assistant light's own state?

**The answer:** not reproducible; in 108 light-tries no test colour was saved, because the light reports the test's last colour well before the release.

| Measurement (test bench, 2 Hue bulbs and 1 Matter bulb) | Result |
|---|---|
| Switched on 1, 2 or 3 s after the release | 12 tries per delay with three lights, 108 light-tries |
| Test colour saved | 0 of 108 |
| Own state saved exactly | 107 of 108 (the one miss was a bench artefact, not a test colour) |
| Last test command before the release | median 2.76 s (at least 1.25 s) |
| Light's last report of a test colour before the release | Hue 2.0 s (median), Matter 1.9 s (median) |
| Restored state reported after the release | Hue 0.27 s (median, up to 1.16 s), Matter 0.21 s (up to 0.27 s) |

- So a report newer than the release is always the restored state, never the test colour.
- Not tested: more than 4 lights on one network.

**What Glowcast does since:** no change needed. Test lights itself came in v0.5.0.

**More:** [spike #271](https://github.com/nicohabets/glowcast/issues/271) ([the result](https://github.com/nicohabets/glowcast/issues/271#issuecomment-6540585244)), recorded in pull request [#305](https://github.com/nicohabets/glowcast/pull/305).

## A whole evening { #a-whole-evening }

**The question:** does Glowcast keep running for an evening, and does it stop cleanly and start again after the usual interruptions?

**The answer:** yes; about 95 minutes of syncing without a crash or an error, no visible memory growth, and a clean stop and restart after TV standby, a bridge power cut and a Home Assistant restart.

| Measurement (v0.1.0 through HACS, update rate Maximum, HDMI sources) | Result |
|---|---|
| Syncing | about 95 minutes |
| Errors / warnings | 0 / 4 (all at the bridge power cut) |
| Home Assistant memory (the whole core) | 1154 MB at the start, then 1188-1238 MB for the last hour, mostly 1190-1210 MB |
| TV to standby: Glowcast off | 1.20 s after the TV reported off |
| TV back on with the remote: the automation started Glowcast | 2.3 ms after the TV reported on |
| Bridge power cut: noticed / stopped | after about 11 s / about 38 s, after one retry |
| Next switch-on after the bridge came back | area active 0.9 s later |
| Home Assistant restart: area inactive | 1.27 s after the restart command |

- No visible memory growth, but 95 minutes is too short to rule out a slow leak.
- After the bridge power cut the lights stayed on the last streamed colour, while the bridge at first reported them as off; the next start and stop of Glowcast restored them.
- Not covered: two hours at update rate High, live TV through the tuner, and apps on the TV itself.

**What Glowcast does since:** no change. The product owner accepted the shortened run, the lights after a bridge power cut ([#91](https://github.com/nicohabets/glowcast/issues/91), closed as not planned) and the 38 s to "off".

**More:** [spike #19](https://github.com/nicohabets/glowcast/issues/19) and its pull request [#93](https://github.com/nicohabets/glowcast/pull/93).
