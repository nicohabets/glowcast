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
- Store config as in architecture section 5: what Glowcast is connected to and its secrets in `data`, everything the user can change (including the output type) in `options`, with typed blocks (`{"type": ...}`).
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

## Test environment

- Hardware: an LG OLED65G36LA (2023, G3) on webOS, a Hue Pro bridge, and a Hue entertainment area with a gradient lightstrip. Hardware details that are specific to the owner's home (names, addresses, credentials) are given in the session handover, never in this repository.
- Measured: about 4.1 captures per second with `WebOsFrameSource` (download and decode to 64x36 included), from a 960x540 JPEG (see architecture section 2).
- Minimum Home Assistant version: 2025.7.2 (architecture section 2).

## How we work

The product owner (the repository owner) decides what gets built and when. Claude Code builds; it does not plan.

### The board
Work is tracked as GitHub issues on the project board **Glowcast** (https://github.com/users/nicohabets/projects/12): columns **Backlog → Todo → In Progress → In Review → Done**, swimlanes per milestone (v0.1, v0.2, v0.3, v99 Later).

| Column | Meaning | Who moves items here |
|---|---|---|
| Backlog | Not ready to be picked up. New issues land here automatically. | automatic |
| Todo | Refined and ready to build, in priority order. | **only the product owner** |
| In Progress | Being built. | Claude Code, when it starts |
| In Review | Pull request open, waiting for the product owner to test and accept. | Claude Code, when the PR is ready |
| Done | Accepted and merged; the issue is closed by the pull request. | automatic, when the product owner merges |

### Rules
1. **Only pick up issues from Todo**, top to bottom. Never move anything into Todo, and never start on an issue that is still in Backlog, even if it looks easy or related.
2. Move the issue to **In Progress** when you start (if your tools allow it; otherwise say so in the issue).
3. One issue per branch (`<number>-<short-name>`, for example `3-colour-engine`) and one pull request per issue, with `Closes #<number>` in the description. **Create the branch from the issue**, so GitHub links it under the issue's "Development": `gh issue develop <number> --repo nicohabets/glowcast --base main --name <number>-<short-name> --checkout`. Do not create it with `git checkout -b`; that branch is not linked. (A branch that already exists cannot be linked afterwards; the pull request with `Closes #<number>` still links the work.)
4. Work through the issue's **acceptance criteria** and **Definition of Done**; tick the checkboxes in the PR description. Then move the issue to **In Review** and describe in the PR how to test it on real hardware.
5. **Never merge.** The product owner tests the PR on real hardware, accepts it and merges it. Review comments are handled in the same PR.
6. **Respect dependencies.** If an issue depends on one that is not Done, stop and say so.
7. **Do not decide product questions.** If an acceptance criterion is unclear, conflicts with the architecture, or turns out to be impossible, comment on the issue with the question and options, and stop on that issue.
8. **Found extra work?** Create a new issue (it lands in Backlog) in the format below and link it. Do not widen the current issue.
9. Small commits with clear messages. Run `pytest` and `ruff check` before every commit.
10. Update `README.md` and `docs/ARCHITECTURE.md` when behaviour visible to users changes; record new verified hardware facts in the architecture's "Verified facts" table.
11. **Screenshots for anything visible** (a screen in Home Assistant, the README on GitHub or in HACS, a log or a chart): attach them to the pull request, and to the issue when they answer its question, so the product owner can review visually. Commit them to the `screenshots` branch as `<issue or PR number>/<short-name>.png` (see that branch's README) and show them with `![...](https://raw.githubusercontent.com/nicohabets/glowcast/screenshots/<number>/<name>.png)`. Make them yourself where you can (for example with Playwright on a local render); where you cannot, say which screenshots the product owner should take. No personal details in a screenshot: crop or blur names, entity ids and addresses.

### Working in parallel (Dispatch, desktop sessions, worktrees)
Several sessions may work at the same time, each on its own issue.
1. **Work in your own worktree**, never in a checkout another session uses. Inside it, create the issue's branch with `gh issue develop` (rule 3) before your first commit, so the branch has the right name and is linked to the issue; do not keep a generated `worktree-...` branch.
2. **Stay inside your issue's files.** If you need to change a file that another open pull request also changes, say so in your pull request.
3. **No changes on the product owner's Home Assistant and no light or TV runs** unless your task explicitly allows it: no installs, no restarts, no service calls, no streaming to the lights. Another session may be testing on the same hardware.
4. **The hardware check is the product owner's.** When your issue needs real hardware, write the steps under "How to test on real hardware" in the pull request and leave that Definition of Done box unticked "for the product owner".
5. **Unattended runs:** if you cannot ask the product owner, a product question goes in a comment on the issue with the options; continue with the rest of the issue and say in the pull request what is open. Rule 7 still applies to anything that cannot be undone or that changes the design.

### Writing new issues
New issues follow the same format as the existing ones:
- **Story:** "As a … I want … so that …", context, acceptance criteria as Given/When/Then scenarios, out of scope, notes for the developer, Definition of Done, dependencies. Label `story`.
- **Spike:** context, question, approach, timebox, possible outcomes, Definition of Done. Label `spike`.
- Add a component label (`source`, `color-engine`, `pipeline`, `output`, `ha`, `quality`, `research`); small non-story work gets `task`. Add a milestone if clear.
- **Features** group the work: each story, spike and task is a sub-issue of one feature issue (label `feature`). Add a new issue to the feature it belongs to (`gh api repos/nicohabets/glowcast/issues/<feature>/sub_issues -F sub_issue_id=<id of the new issue>`); if none fits, say so to the product owner instead of creating a feature. Features are created by the product owner; they have no milestone, are not built themselves, and stay out of the board's columns.

### Public repository
This repository is public. Never put personal details in code, docs, issues, commits or PRs: no room or device names from the owner's home, no IP addresses, entity ids, credentials, names or locations. Use generic examples such as `media_player.living_room_tv`.
