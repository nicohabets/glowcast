---
name: Story
about: A piece of user value, written by or for the product owner
labels: story
---

## User story
**As** a ...,
**I want** ...,
**so that** ...

## Context
Why this matters, what is already known or verified, links to `docs/ARCHITECTURE.md`.

## Acceptance criteria
**Scenario: ...**
- **Given** ...
- **When** ...
- **Then** ...

## Out of scope
- ...

## Notes for the developer
- ...

## Definition of Done
- [ ] All acceptance criteria met
- [ ] Pull request references this issue (`Closes #<n>`), reviewed and merged by the product owner
- [ ] CI green (`pytest`, `ruff check`, hassfest, HACS validation)
- [ ] No `homeassistant` imports in `core/`; no secrets, image URLs or frame contents in logs
- [ ] Verified on real hardware where hardware is involved
- [ ] README and `docs/ARCHITECTURE.md` updated if behaviour visible to users changed

## Depends on
-
