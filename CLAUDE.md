# CLAUDE.md - instructions for building Glowcast

Glowcast is a Home Assistant custom integration (HACS) that makes Hue lights follow the picture on an LG webOS TV.

**Read `docs/ARCHITECTURE.md` before writing any code.** It is the design and the scope. If something in it turns out to be wrong or impossible, stop and say so; do not quietly redesign.

## Scope rules

- v0.1 (webOS source, Hue entertainment-area output, one switch, the options screen) is built. The milestone in progress is **v0.2**.
- Only work on issues in the **Todo** column of the project board (see "How we work"). An issue in any milestone, v0.2 included, is out of scope until the product owner moves it to Todo; never build ahead for a later issue.
- Keep the ports (`FrameSource`, `LightOutput`) so later features can be added without rewrites, but do not build placeholders or stub adapters for them.
- Prefer the simplest thing that works. No speculative options, no config knobs that are not in the architecture.

## Code rules

- `custom_components/glowcast/core/` (models, ports, colour engine and pipeline) must not import `homeassistant`. Inject the blocking-call runner and the clock so it can be tested with plain fakes.
- Never block the event loop: JPEG decoding and resizing go through `hass.async_add_executor_job`.
- All I/O is async. Use HA's session (`async_get_clientsession`) for your own HTTP calls and pass it to `aiowebostv`. `hue-entertainment` opens its own session; close it in `LightOutput.close()`.
- `LightOutput.stop()` and `close()` must run on every exit path (switch off, TV off, error, unload, HA stop) and be safe to call twice. Send colours to Hue as 0-255 values, never 16-bit (architecture 3.4).
- No code outside `outputs/` may know about Hue, and no code outside `sources/` may know about webOS (architecture section 8). That includes the UI: each source and output type provides its own setup and options steps, entities and user-facing texts. The setup flow, the options flow and the rest of the generic code only chain and show what the types provide.
- Store config as in architecture section 5: what Glowcast is connected to and its secrets in `data`, everything the user can change (including the output type) in `options`, with typed blocks (`{"type": ...}`). An area and loose lights together are one `combined` block with a typed block per part (`{"type": "combined", "parts": [...]}`); a type's `create` and `is_missing` get the options with their own block as `options["output"]`, and its flows and entities find and replace their block with `outputs/blocks.py`, so a type never reads another type's block.
- Reference the `webostv` and `hue` entries by unique id and read the TV's `host` and `client_secret` at runtime. Do not copy them into Glowcast's config entry.
- Store the Hue `app_key` and `client_key` from Glowcast's own pairing in Glowcast's config entry data.
- Do not log secrets, image URLs with tokens, or frame contents.
- Type hints everywhere; follow Home Assistant's integration conventions (config flow, reauth, entity naming with `has_entity_name = True`, unique ids).
- User-facing text in `translations/en.json`, with a Dutch translation in `translations/nl.json`.
- Use a plain hyphen (-) in docs and strings, not an em dash.

## Things to verify early, before building on them

1. Whether `hue-entertainment==0.1.2` installs cleanly in a current Home Assistant (its `cryptography`, `aiohttp` and `zeroconf` pins must not clash with HA's).
2. Whether DRM apps (Netflix) return black frames. Report the result; do not build a workaround in v0.1.

## Testing

- Write `tests/test_color_engine.py` first; the ColorEngine is the heart and is fully testable without hardware.
- Pipeline tests use fake source/output classes, not mocks of internals.
- Run `pytest` and `ruff check` before every commit. Keep the build green.
- Before opening a pull request that touches a source, the pipeline, the colour engine or an output, run the end-to-end check `python3 scripts/demo.py e2e` (the screen helper's test patterns through Glowcast to the demo's fake bridge, all on `127.0.0.1`; see `scripts/README.md`) and say in the pull request that it passed. CI runs it too.
- The screen helper (`screen_helper/`, its own package) has its own tests: `uv run --project screen_helper --group dev pytest screen_helper/tests`. Run them too when you change the helper.

### The screen helper for tests without the TV

The screen helper (`screen_helper/README.md`, architecture 8.3) serves 64x36 frames to Glowcast. In tests, use its **pattern mode**: known pixels, no screen access, no display, so it also runs headless, in a cloud session and in CI:

```bash
uv run --project screen_helper glowcast-screen --pattern cycle --name "Test PC" --host 127.0.0.1 --port 0 --non-interactive --pairing-code 123456 --json
```

- Read the `started` event (one JSON line) for the port; `pattern` events say which pattern shows since when. The exact pixels of every pattern are in its README.
- Always pass a generic `--name`: the default is the computer's name, which then shows in the JSON output you might attach. Listen on `127.0.0.1` (not announced on the network), use your own `--config-dir` in the scratchpad so you never touch the product owner's pairings, and stop it with SIGINT or SIGTERM.
- Never capture a real screen (no run without `--pattern`) unless your task allows it and the session runs on the product owner's computer with his go, except the test bench ("Working in parallel", rule 6); then print numbers only, never save or show frames.
- Run it against the product owner's lights (a Glowcast on his Home Assistant) only when your task explicitly allows it ("Working in parallel", rule 3), except the test bench ("Working in parallel", rule 6).

## Test environment

- Hardware: an LG OLED65G36LA (2023, G3) on webOS, a Hue Pro bridge, and a Hue entertainment area with a gradient lightstrip. Hardware details that are specific to the owner's home (names, addresses, credentials) are given in the session handover, never in this repository.
- Measured: about 4.1 captures per second with `WebOsFrameSource` (download and decode to 64x36 included), from a 960x540 JPEG (see architecture section 2).
- Minimum Home Assistant version: 2025.7.2 (architecture section 2).

## How we work

The product owner (the repository owner) decides what gets built and when. Claude Code builds; it does not plan.

### The board
Work is tracked as GitHub issues on the project board **Glowcast** (https://github.com/users/nicohabets/projects/12): columns **Backlog → Todo → In Progress → In Review → Done**, swimlanes per milestone. Release plan: v0.2 (see what Glowcast does and control it from Home Assistant), v0.3 (more lights, and ready to share: loose lights #76, the website, user guide and promotion), v0.4 (colours that match the picture, #73), v0.5 (more TVs and sources, #77). Issues without a milestone are not planned yet. The Kanban view hides closed milestones and features. Developer tooling and tests (demo, room tool, test bench, CI) belong to feature #178 "Build and test without the TV".

| Column | Meaning | Who moves items here |
|---|---|---|
| Backlog | Not ready to be picked up. New issues land here automatically. | automatic |
| Todo | Refined and ready to build, in priority order. | **only the product owner** |
| In Progress | Being built. | Claude Code, when it starts |
| In Review | Pull request open, waiting for the product owner to test and accept. | Claude Code, when the PR is ready |
| Done | Accepted and merged; the issue is closed by the pull request. | automatic, when the product owner merges |

### Rules
1. **Only pick up issues from Todo**, top to bottom. Never move anything into Todo, and never start on an issue that is still in Backlog, even if it looks easy or related. **Only issues with a milestone:** if an issue in Todo has no milestone, it is not planned yet; comment on the issue that it needs a milestone, and do not start on it.
    - CI checks this too: the `milestone` job fails a PR whose closing issue has no milestone.
2. Move the issue to **In Progress** when you start (if your tools allow it; otherwise say so in the issue).
3. One issue per branch (`<number>-<short-name>`, for example `3-colour-engine`) and one pull request per issue, with `Closes #<number>` in the description. **Create the branch from the issue**, so GitHub links it under the issue's "Development": `gh issue develop <number> --repo nicohabets/glowcast --base main --name <number>-<short-name> --checkout`. Do not create it with `git checkout -b`; that branch is not linked. (A branch that already exists cannot be linked afterwards; the pull request with `Closes #<number>` still links the work.)
4. Work through the issue's **acceptance criteria** and **Definition of Done**; tick the checkboxes in the PR description. Then move the issue to **In Review** and describe in the PR how to test it on real hardware.
5. **Never merge.** The product owner tests the PR on real hardware, accepts it and merges it. Review comments are handled in the same PR.
6. **Respect dependencies.** If an issue depends on one that is not Done, stop and say so.
7. **Do not decide product questions.** If an acceptance criterion is unclear, conflicts with the architecture, or turns out to be impossible, comment on the issue with the question and options, and stop on that issue.
8. **Found extra work?** Create a new issue (it lands in Backlog) in the format below and link it. Do not widen the current issue.
9. Small commits with clear messages. Run `pytest` and `ruff check` before every commit.
10. Update the user documentation and `docs/ARCHITECTURE.md` when behaviour visible to users changes; record new verified hardware facts in the architecture's "Verified facts" table. User documentation lives on the website, in `website/pages/` (MkDocs Material, published at https://nicohabets.github.io/glowcast/ from `main`; see `website/mkdocs.yml`). `README.md` stays a complete description in short form, because HACS and the Marketplace show it (from the latest release): what Glowcast is and how it works, requirements, installation, setup and the main settings, starting and stopping, the status in brief, known limits, reporting a problem, privacy, disclaimer and license, each with a "More:" link (absolute URL) to the site's page with the detail. New detail goes on the site; the README gets at most a short line. Developer documentation (the architecture, `scripts/`, this file) stays in the repository and is not published. Build the site with `mkdocs build --strict -f website/mkdocs.yml` and check the links with `python3 scripts/check_links.py` (the `website` workflow does both on pull requests); when the README changes, also check it with `scripts/render_hacs_readme.cjs`.
11. **GIFs of the flow for anything visible** (a screen in Home Assistant, the setup flow, the README on GitHub or in HACS): record an animated GIF that shows the flow step by step - what the user clicks and what changes - and attach it to the pull request, and to the issue when it answers its question, so the product owner can review visually. One GIF per flow, starting from a recognisable screen; keep it short (about 5-20 seconds), readable on a phone and on desktop (Home Assistant's mobile view: a viewport about 400 px wide, recorded at device scale factor 2 so text stays sharp, giving a GIF about 800 px wide; slow enough to follow, a short pause on each step) and under 10 MB. Still screenshots use the same mobile view. Use a still screenshot only when there is no flow to show (a log, a chart, a static page). Make them yourself where you can: for Home Assistant use the local demo (`scripts/demo.py`, see `scripts/README.md`; it records the flows itself and never touches the product owner's Home Assistant, TV or lights); for other pages use Playwright (`viewport` about 400 px wide, `device_scale_factor=2`) on a local render. Build the GIF from `page.screenshot()` frames, not from Playwright's video (`record_video_dir`): the video records at 1x whatever the scale factor, so the page fills only a quarter of an 800 px frame. Convert with `ffmpeg` (palettegen/paletteuse) and look at the frames at full size before attaching. Upload them only as GitHub attachments, with `gh` 2.99 or newer: `gh pr comment <n> --body "..." --attach ./flow.gif` (also `issue comment`, `pr create`, `pr edit`, `issue edit`; the `--attach` flag can be repeated, and a local path written in the body is replaced by the uploaded file). **Show them small and clickable:** in PR and issue comments, every image or GIF is shown at 400 px wide (side-by-side pairs, such as en/nl or before/after, in a two-column table) and links to the full-size file: `<a href="<url>"><img src="<url>" width="400" alt="..."></a>`. `--attach` inserts full-size Markdown images (`![...](<url>)`), so after posting, edit the comment (`gh api -X PATCH repos/nicohabets/glowcast/issues/comments/<id> -f body=...`) and replace each with that HTML, keeping the alt text. The files themselves stay full size. Do not commit review GIFs or screenshots to the repository or to any branch. There is no other route: if the upload does not work in your session (cloud sessions only allow JSON requests to GitHub's API), say so in the pull request and show the GIFs to the product owner in your chat instead. No personal details in any frame: use generic names, or crop or blur names, entity ids and addresses. Release notes (`docs/releases/`) show the visible changes of the release with GIFs of the flow too; images in release notes and the README are part of the product and live on `main` (`docs/images/`) and are shown at about 400 px wide (`<img width="400" ...>`).
    - **The room, before and after.** Any change to the colour engine, the pipeline (rates, smoothing, start, stop, release), a source or an output, the light state, or a setting that changes what the lights get (brightness, intensity, update rate, area) also shows what Glowcast sends to the lights: run `python3 scripts/demo.py room --compare` (see `scripts/README.md`) and attach `room-out/room.gif` (`main` and "this PR" side by side) and the stills `room-out/room-<n>.png` to the pull request like the other GIFs; never commit them. A change to loose lights (`outputs/ha_lights*.py`) or the combined output (`outputs/composite.py`) attaches the loose or combined room too: `room --compare --lights loose` or `--lights combined` (and `--versus` when it changes what a place does; see `scripts/README.md`). Not needed for documentation, Home Assistant screens only (those use the flows above), CI or tooling changes; then say so at the checkbox in the PR template.

### Working in parallel (Dispatch, desktop sessions, worktrees)
Several sessions may work at the same time, each on its own issue.
1. **Work in your own worktree**, never in a checkout another session uses. Inside it, create the issue's branch with `gh issue develop` (rule 3) before your first commit, so the branch has the right name and is linked to the issue; do not keep a generated `worktree-...` branch.
2. **Stay inside your issue's files.** If you need to change a file that another open pull request also changes, say so in your pull request.
3. **No changes on the product owner's Home Assistant and no light or TV runs** unless your task explicitly allows it: no installs, no restarts, no service calls, no streaming to the lights. Another session may be testing on the same hardware. The one exception is the test bench ("Working in parallel", rule 6).
4. **The hardware check on the TV is the product owner's.** When your issue needs the TV, write the steps under "How to test on real hardware" in the pull request and leave that Definition of Done box unticked "for the product owner".
5. **Unattended runs:** if you cannot ask the product owner, a product question goes in a comment on the issue with the options; continue with the rest of the issue and say in the pull request what is open. Rule 7 still applies to anything that cannot be undone or that changes the design.
6. **The test bench** (`scripts/bench/`, see `scripts/README.md`). Before you open (or mark ready) a pull request that touches a source, the pipeline, the colour engine or an output, run `python3 scripts/bench/run.py --suite basic --mode both`. Every run starts with its own calibration (camera position, exposure, ambient light); exit code 7 means the calibration failed (camera moved or blocked, too much daylight), which is a finding about the bench, not your change. Then paste the `report.md` it writes (the run prints the path) into a comment on the pull request with `gh pr comment <n> --body-file <path to report.md>`; it contains numbers only.
    - **No need to ask.** You may run it in a desktop session on the bench computer, within its rules: it only touches the bench (its own Glowcast in a local Home Assistant, the bench lights, the helper, the camera and a full-screen window on the bench monitor). When the TV's area streams, it also switches the TV's Glowcast off and back on through the product owner's Home Assistant. It never touches the TV itself, other lights or areas, never restarts anything and never changes configuration.
    - **Not on the bench computer** (for example in a cloud session): say so in the pull request and leave the bench box unticked.
    - **Skips are not failures.** Exit code 3 (another area streams), 4 (another bench run) or 5 (another app streams the TV's area): try again later and say so in the pull request.
    - **A failed step is a finding** for your pull request, not something to work around in the bench.
    - **The Definition of Done of such stories is split.** "Checked on the test bench (report attached)" is yours; "checked on the TV" stays with the product owner, only for what is specific to webOS.

### Dependency updates
Dependabot (`.github/dependabot.yml`) opens weekly pull requests for the GitHub Actions, the screen helper and the website's build packages, grouped per ecosystem (a screen helper major comes on its own) and labelled `dependencies`; security updates come as separate pull requests. They are the exception to rules 1-5: they need no issue and no milestone, the `milestone` check passes them because they close none, and the PO assistant reviews them (changelog, CI) and merges them on the product owner's word. A major update that breaks something becomes a Backlog task under #178. The Home Assistant-bound pins (`manifest.json` and the test pins in `ci.yml`) stay manual; see the comment at the top of `dependabot.yml`.

### Writing new issues
New issues follow the same format as the existing ones:
- **Story:** "As a … I want … so that …", context, acceptance criteria as Given/When/Then scenarios, out of scope, notes for the developer, Definition of Done, dependencies. Label `story`.
- **Spike:** context, question, approach, timebox, possible outcomes, Definition of Done. Label `spike`.
- Add a component label (`source`, `color-engine`, `pipeline`, `output`, `ha`, `quality`, `research`); small non-story work gets `task`. Add a milestone if clear.
- **Features** group the work: each story, spike and task is a sub-issue of one feature issue (label `feature`). Add a new issue to the feature it belongs to (`gh api repos/nicohabets/glowcast/issues/<feature>/sub_issues -F sub_issue_id=<id of the new issue>`); if none fits, say so to the product owner instead of creating a feature. Features are created by the product owner; they have no milestone, are not built themselves, and stay out of the board's columns.

### Public repository
This repository is public. Never put personal details in code, docs, issues, commits or PRs: no room or device names from the owner's home, no IP addresses, entity ids, credentials, names or locations. Use generic examples such as `media_player.living_room_tv`.
