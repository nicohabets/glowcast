Closes #

## What changed
-

## How to test on real hardware
1.

## Acceptance criteria
- [ ] (copy from the issue)

## Definition of Done
- [ ] CI green (`pytest`, `ruff check`, hassfest, HACS validation)
- [ ] No `homeassistant` imports in `core/`; no secrets, image URLs or frame contents in logs
- [ ] No personal details (room or device names, IPs, entity ids, credentials)
- [ ] README and `docs/ARCHITECTURE.md` updated if behaviour visible to users changed
- [ ] A change to a screen shown in the README (setup flow, settings screen, device page) updates its image in `docs/images/`, made with `scripts/demo.py`
