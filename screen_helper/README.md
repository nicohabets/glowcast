# Glowcast screen helper

A small program for a PC or Mac that lets Glowcast follow that computer's screen. It shrinks the screen to 64x36 pixels and serves the latest picture on the local network. Glowcast, in Home Assistant, pulls the pictures the same way it pulls them from the TV. It can also serve synthetic **test patterns** without a screen, for agents and automated tests.

It is a developer tool for now: you start it from a clone of the repository. Home Assistant finds a running helper on the network by itself (Settings → Devices & services → Discovered: Glowcast - *computer name*); click Add and enter the pairing code it shows. Without discovery, add Glowcast by hand and choose Computer screen (see [A computer screen](https://nicohabets.github.io/glowcast/docs/computer-screen/) on Glowcast's website). You can also try the helper with `curl`. Home Assistant (HACS) does not install this folder, and the integration does not import it.

## Start it

You need [uv](https://docs.astral.sh/uv/getting-started/installation/) and nothing else: uv fetches Python and the pinned dependencies itself (about 32 MB) on the first run. From the root of the repository:

```bash
uv run --project screen_helper glowcast-screen
```

That serves the main screen on port 8488 and shows a 6-digit pairing code. For a test pattern instead of the screen:

```bash
uv run --project screen_helper glowcast-screen --pattern red
```

Stop it with Ctrl+C. It then withdraws its network announcement and exits, within about a second also when a screen grab hangs.

## Flags

| Flag | What it does |
|---|---|
| `--pattern NAME` | Serve a test pattern instead of the screen (see below). It needs no screen access and no display, so it also works headless, in a cloud session and in CI. |
| `--pattern-interval SECONDS` | Seconds per pattern for `--pattern cycle` (default 5). |
| `--screen N` | The screen to capture, numbered as `/v1/info` and the start output list them (default: the main screen). |
| `--port PORT` | HTTP port, 0-65535 (default 8488; `0` picks a free one, which the `started` event reports). |
| `--host ADDRESS` | Address to listen on (default `0.0.0.0`, all). On a loopback address such as `127.0.0.1`, the helper is not announced on the network. |
| `--name NAME` | The name Home Assistant will show (default: the computer's name). Agents and tests pass a generic one, so the computer's name never ends up in output they share. |
| `--pairing-code CODE` | A fixed 6-digit pairing code instead of a new random one on every start. |
| `--non-interactive` | Never ask anything. Without `--pairing-code`, no code is made and only existing pairings work. |
| `--json` | Print one JSON line per event on standard output (see below). |
| `--unpair` | Remove all pairings and exit. It asks first, unless `--non-interactive` is set. The helper's id stays. |
| `--config-dir DIR` | Where the id and the pairings live (default: your user config directory, see below). |
| `--version` | Print the version. |

For an agent or a test, for example:

```bash
uv run --project screen_helper glowcast-screen --pattern cycle --pattern-interval 2 --name "Test PC" --host 127.0.0.1 --port 0 --non-interactive --pairing-code 123456 --json
```

## Screens

Screens are numbered 1, 2, ... in the order the operating system lists them. The main screen is not always number 1. The start output and `GET /v1/info` list each screen with its size, its position on the desktop and whether it is the main screen:

```
  Screens:
    1: 3440x1440 at 0,0 (main)
    2: 1920x1080 at 3440,0
```

Pick the second monitor with `--screen 2`. A number that does not exist stops the helper with exit code 4 and the list of screens. While frames are pulled, the helper lists the screens again every 5 seconds, so a monitor that changes resolution or is plugged in again is captured as it is now. When the chosen screen is gone (unplugged, or asleep and removed by the operating system), the answer is "no picture" (`screen_missing`) until it is back. The whole screen is shrunk to 64x36 and stretched, not cropped, so the left and right edges of an ultrawide screen stay at the left and right.

Capture happens only when a picture is asked for: one grab per request, shared by requests that arrive within 50 ms of each other. With nobody asking, the helper is idle. A picture is never encoded as a JPEG.

On Linux, `mss` captures through X11; in a Wayland session capture may fail or stay black (not tested).

## Pairing

On every start (unless `--non-interactive`), the helper shows a 6-digit pairing code. `POST /v1/pair` with that code returns a token. Every other request needs that token, else the answer is 401. Each wrong code costs 1 second, one attempt at a time, so guessing the code takes days. Pairings survive a restart. `--unpair` removes all of them; Glowcast then has to pair again.

The pairings and the helper's id (a random id made on the first start, which Home Assistant will use to recognise the helper) live in `state.json` in your user config directory, never in the repository:

- Linux: `~/.config/glowcast-screen/`
- macOS: `~/Library/Application Support/glowcast-screen/`
- Windows: `%LOCALAPPDATA%\glowcast-screen\`

Only a SHA-256 hash of each token is stored. The helper never logs a token. The pairing code only appears in the interactive output, and in the `--json` output only when you passed it in with `--pairing-code`.

Frames and the token go over plain HTTP on the local network, as the TV's pictures do today (architecture 3.4).

## The protocol

Protocol version 1, as in [architecture 8.3](../docs/ARCHITECTURE.md#83-a-computer-screen-feature-107):

| Request | Answer |
|---|---|
| `POST /v1/pair` with `{"code": "123456"}` | `200` `{"token": "...", "id": "..."}`; `403` `{"error": "wrong_code"}`; `403` `{"error": "pairing_unavailable"}` when the helper has no code (`--non-interactive` without `--pairing-code`); `400` `{"error": "bad_request"}` |
| `GET /v1/frame` | `200` with 6,912 bytes, `application/octet-stream`: 64x36 pixels, RGB, one byte per channel, row by row from the top left. **`204` with no body: no picture** (capture failed, for example a locked screen, or took longer than 1 s). |
| `GET /v1/info` | `200` JSON, see below |
| anything else without a valid `Authorization: Bearer <token>` | `401` `{"error": "unauthorized"}` |

`/v1/info`:

```json
{
  "id": "4f0c9a...",
  "name": "Test PC",
  "protocol": 1,
  "version": "0.1.0",
  "width": 64,
  "height": 36,
  "screens": [{"index": 1, "width": 3440, "height": 1440, "left": 0, "top": 0, "primary": true}],
  "screen": 1,
  "pattern": null,
  "no_picture": null
}
```

`no_picture` is `null` while there is a picture, else the reason of the last "no picture" answer: `capture_failed` (the grab failed, for example a locked screen), `capture_timeout` (it took longer than 1 s) or `screen_missing` (the chosen screen is gone). With `--pattern`, `screens` is empty, `screen` is `null`, and `pattern` is `{"name": "cycle", "showing": "green", "since": "2026-10-29T12:00:05.000Z", "interval": 5.0}` (`interval` only for `cycle`; times are UTC).

The helper announces itself with mDNS as `_glowcast._tcp.local.`, with the instance name `<name> (<first 6 characters of the id>)` and a TXT record `id=<id>`, `name=<name>`, `protocol=1`. It announces one address: the one the computer reaches the network with (its default route), so addresses of virtual adapters (Hyper-V, WSL, Docker, a VPN) that Home Assistant cannot reach are left out. Only without a default route does it announce all its addresses.

Try it with `curl`:

```bash
curl -s -X POST -d '{"code": "123456"}' http://127.0.0.1:8488/v1/pair
curl -s -H "Authorization: Bearer <token>" http://127.0.0.1:8488/v1/info
curl -s -H "Authorization: Bearer <token>" -o frame.rgb -w "%{http_code} %{size_download} bytes\n" http://127.0.0.1:8488/v1/frame
```

## Test patterns

Every pattern is 64x36 RGB. Columns are `x` 0-63 from the left, rows are `y` 0-35 from the top.

| Pattern | Pixels |
|---|---|
| `red` | every pixel `(255, 0, 0)` |
| `green` | every pixel `(0, 255, 0)` |
| `blue` | every pixel `(0, 0, 255)` |
| `white` | every pixel `(255, 255, 255)` |
| `black` | every pixel `(0, 0, 0)` |
| `left-red-right-blue` | columns 0-31 `(255, 0, 0)`, columns 32-63 `(0, 0, 255)` |
| `letterbox` | rows 0-4 and 31-35 `(0, 0, 0)` (bars of 5 rows, about a 2.39:1 film on a 16:9 screen); rows 5-30 `(0, 255, 0)` |
| `cycle` | steps through `red`, `green`, `blue`, `white`, `black`, `left-red-right-blue`, `letterbox`, then starts again, `--pattern-interval` seconds each (default 5). Neighbours, including `letterbox` and `red`, always look different at the lights. |

`cycle` reports which pattern is on show and since when, in `/v1/info` (`pattern.showing`, `pattern.since`) and in a `pattern` event on every change. The `since` times follow from the start: `since = start + n x interval`.

## Events (`--json`)

With `--json`, every event is one JSON line on standard output, flushed at once. Each has `event` and `time` (UTC, ISO 8601 with milliseconds). Human-only text, such as a pairing code the helper made itself, goes to standard error.

| `event` | When | Other fields |
|---|---|---|
| `started` | serving | `host`, `port`, `id`, `name`, `protocol`, `version`, `pattern` (or `null`), `screen`, `screens`, `mdns` (announced or not), `paired` (number of pairings), and `pairing_code` only if passed with `--pairing-code` |
| `pattern` | `cycle` shows the next pattern (also the first) | `showing`, `since` |
| `paired` | a client paired | `client` (its address) |
| `pairing_failed` | a wrong code | `client` |
| `client_connected` | the first request with a valid token, or the first after 30 s of quiet | `client` |
| `frames` | every second in which frames were served | `fps`, `capture_ms` (`{"median": ..., "max": ...}` grab plus shrink, in ms; `null` for patterns) |
| `no_picture` | the first "no picture" answer after a picture | `reason` |
| `picture` | the first picture after "no picture" | |
| `mdns_failed` | the announcement failed; serving goes on | `error` |
| `unpaired` | `--unpair` removed the pairings | `removed` |
| `stopped` | after Ctrl+C, SIGINT or SIGTERM, with the announcement withdrawn | |
| `error` | a start-up error, just before the exit | `reason`, `message`, `exit_code` |

Without `--json`, the same events are printed as plain text.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | stopped normally (Ctrl+C, SIGINT, SIGTERM) or `--unpair` done |
| 1 | the config directory cannot be read or written |
| 2 | a wrong flag |
| 3 | the port is in use or the address cannot be used (`reason: port_in_use`), or `--host` does not resolve (`reason: bad_host`) |
| 4 | no screen access: no display, no Screen Recording permission, a screen number that does not exist, or listing the screens took longer than 5 s (`reason: no_screen_access`). A screen that is locked at start-up is not an error: the helper starts and answers "no picture" until it is unlocked. |

## Prompts on the first start

- **macOS: Screen Recording.** The first time, macOS asks whether the app you started the helper from (for example Terminal or iTerm) may record the screen. Without that permission macOS does not refuse the capture; it returns the desktop without windows. So the helper checks first, and stops with exit code 4 and these steps: open System Settings > Privacy & Security > Screen Recording, switch it on for that app, quit and reopen the app, and start the helper again. With `--pattern`, no permission is needed.
- **macOS: local network and firewall.** macOS may ask whether the app may find and connect to devices on the local network (for the mDNS announcement), and, if the firewall is on, whether it may accept incoming connections. Allow both.
- **Windows: firewall.** The first time the helper listens on the network, Windows Defender Firewall asks whether Python may communicate on networks. Allow it on **private** networks (your home network). If you dismissed the prompt, allow Python in Windows Security > Firewall & network protection > Allow an app through firewall.

## Tests

```bash
uv run --project screen_helper --group dev pytest screen_helper/tests
```

They use a fake grabber instead of `mss`, a fake clock and a fake zeroconf, and a real aiohttp test client against the server. They need no display and run in CI on Linux, macOS, Windows and Windows on ARM.
