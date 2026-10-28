# CLAUDE.md - instructions for building Glowcast

Glowcast is a Home Assistant custom integration (HACS) that makes Hue lights follow the picture on an LG webOS TV.

**Read `docs/ARCHITECTURE.md` before writing any code.** It is the design and the scope. If something in it turns out to be wrong or impossible, stop and say so; do not quietly redesign.

## Scope rules

- Build **v0.1 only**: webOS source, Hue entertainment-area output, one switch, and the options screen (area, brightness, intensity, update rate).
- Only work on issues in the **Todo** column of the project board (see "How we work"). Keep the ports (`FrameSource`, `LightOutput`) so it can be added without rewrites, but do not build placeholders or stub adapters for later features.
- Prefer the simplest thing that works. No speculative options, no config knobs that are not in the architecture.

## Code rules

- `custom_components/glowcast/core/` (models, ports, colour engine and pipeline) must not import `homeassistant`. Inject the blocking-call runner and the clock so it can be tested with plain fakes.
- Never block the event loop: JPEG decoding and resizing go through `hass.async_add_executor_job`.
- All I/O is async. Use HA's session (`async_get_clientsession`) for your own HTTP calls and pass it to `aiowebostv`. `hue-entertainment` opens its own session; close it in `LightOutput.close()`.
- `LightOutput.stop()` and `close()` must run on every exit path (switch off, TV off, error, unload, HA stop) and be safe to call twice. Send colours to Hue as 0-255 values, never 16-bit (architecture 3.4).
- No code outside `outputs/` may know about Hue, and no code outside `sources/` may know about webOS (architecture section 8).
- Store source and output config as typed blocks (`{"type": ...}`), as in architecture section 5.
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

- Hardware: a 2023 LG OLED (G3) on webOS, a Hue Pro bridge, and a Hue entertainment area with a gradient lightstrip. Hardware details that are specific to the owner's home (names, addresses, credentials) are given in the session handover, never in this repository.
- Measured: about 3.4 captures per second, 960x540 JPEG (see architecture section 2).

## How we work

The product owner (the repository owner) decides what gets built and when. Claude Code builds; it does not plan.

### The board
Work is tracked as GitHub issues on the project board **Glowcast** (https://github.com/users/nicohabets/projects/12): columns **Backlog → Todo → In Progress → In Review → Done**, swimlanes per milestone (v0.1, v0.2, Later).

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
3. One issue per branch (`<number>-<short-name>`, for example `3-colour-engine`) and one pull request per issue, with `Closes #<number>` in the description.
4. Work through the issue's **acceptance criteria** and **Definition of Done**; tick the checkboxes in the PR description. Then move the issue to **In Review** and describe in the PR how to test it on real hardware.
5. **Never merge.** The product owner tests the PR on real hardware, accepts it and merges it. Review comments are handled in the same PR.
6. **Respect dependencies.** If an issue depends on one that is not Done, stop and say so.
7. **Do not decide product questions.** If an acceptance criterion is unclear, conflicts with the architecture, or turns out to be impossible, comment on the issue with the question and options, and stop on that issue.
8. **Found extra work?** Create a new issue (it lands in Backlog) in the format below and link it. Do not widen the current issue.
9. Small commits with clear messages. Run `pytest` and `ruff check` before every commit.
10. Update `README.md` and `docs/ARCHITECTURE.md` when behaviour visible to users changes; record new verified hardware facts in the architecture's "Verified facts" table.

### Writing new issues
New issues follow the same format as the existing ones:
- **Story:** "As a … I want … so that …", context, acceptance criteria as Given/When/Then scenarios, out of scope, notes for the developer, Definition of Done, dependencies. Label `story`.
- **Spike:** context, question, approach, timebox, possible outcomes, Definition of Done. Label `spike`.
- Add a component label (`source`, `color-engine`, `output`, `ha`, `quality`, `research`) and, if clear, a milestone.

### Public repository
This repository is public. Never put personal details in code, docs, issues, commits or PRs: no room or device names from the owner's home, no IP addresses, entity ids, credentials, names or locations. Use generic examples such as `media_player.living_room_tv`.
