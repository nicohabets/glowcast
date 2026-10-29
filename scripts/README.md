# Developer scripts

These scripts are tools for developing Glowcast; they are not part of the integration and HACS does not install them. They need the test requirements from `.github/workflows/ci.yml` (`pytest-homeassistant-custom-component` and `aiowebostv`), and `try_hue_output.py` and `try_pipeline.py` also need `hue-entertainment==0.1.2`.

- `try_webos_source.py`: runs the webOS frame source against a real LG TV and prints timings and average colours.
- `try_hue_output.py`: runs the Hue entertainment output against a real bridge: a left/centre/right pattern, a very dark colour, then stop.
- `try_pipeline.py`: runs the pipeline with the real TV source and Hue output for a while, then stops, and prints the start time, rates and stop reason.
- `render_brand.py`: builds the icon and logo PNGs in `custom_components/glowcast/brand/` from the sources in `docs/brand/`. It does not need the test requirements: run it with `uv run scripts/render_brand.py`, which installs its own pinned dependencies. See [docs/brand/README.md](../docs/brand/README.md).
- `render_hacs_readme.cjs`: renders `README.md` the way HACS shows it (the same `marked` and `xss` versions and settings), to check that no HTML shows as text. It needs Node and `npm install marked@15.0.4 xss@1.0.15`, not the test requirements; it is not part of CI.
- `demo.py`: a local Home Assistant with Glowcast from the working copy, for screenshots and UI checks; see below.

See the docstring at the top of each script for usage. Credentials come from environment variables; never put them in files in the repository.

## The local demo

`demo.py` runs Home Assistant 2026.10.4 (the "latest" version in `.github/workflows/ci.yml`; bump both together) on `127.0.0.1:8199` with Glowcast from the working copy, a TV called "Living room TV" and a Hue bridge with the entertainment areas "TV area" and "Whole room". It does not need the test requirements; it installs its own.

- Nothing reaches a real TV or bridge: stub `hue` and `webostv` integrations (`demo/stubs/`) only report their entries as loaded, the bridge is a fake one on `127.0.0.1:8443` (`demo/fake_bridge.py`, including Hue's pairing reply), and Home Assistant loads no `default_config` and no discovery integration. `start` checks this and stops if it is not so. The stubs replace Home Assistant's own integrations: never copy them to a real Home Assistant.
- Switching Glowcast on works, but no TV answers on `127.0.0.1`: after the start-up grace (about 10 s) it stops with "the TV is off or not reachable" in the log.
- Everything it makes (virtual environment, config directory, logs, screenshots) lives in `~/.cache/glowcast-demo`, outside the repository. Playwright keeps its Chromium in `~/.cache/ms-playwright`.
- It runs with Python alone on Linux and macOS (on Windows in WSL). Home Assistant needs Python 3.14.2 or newer; if the Python you run `demo.py` with is older, it uses [uv](https://docs.astral.sh/uv/) to fetch one.

```
python3 scripts/demo.py start                         # first run installs (a few minutes)
python3 scripts/demo.py shoot --lang nl --theme dark  # en|nl, light|dark; --out DIR
python3 scripts/demo.py stop
python3 scripts/demo.py clean                         # stop and remove ~/.cache/glowcast-demo
```

`start` always begins with a fresh config directory without a Glowcast entry. Log in at http://127.0.0.1:8199 as `demo` / `demo-password` to look around. `shoot` removes a Glowcast entry from an earlier run, runs the setup flow (TV, bridge, link button, pairing, area) and saves the setup steps, the integration page, the settings screen with the update-rate choices, and the device page before and after switching on, as `<lang>-<theme>-<nn>-<name>.png` in `~/.cache/glowcast-demo/screenshots`. If a step fails, it saves `error.png` with the page at that moment. Screenshots for a pull request go up as GitHub attachments (rule 11 in `CLAUDE.md`); do not commit them.

To add a screen (for example new entities or a status), write a function in `demo/shoot.py` that takes a `Shoot`, opens the page (`shoot.goto(...)`) and calls `shoot.save("name")`, and add it to `STEPS`. Find buttons by slot or by the frontend's own texts (`shoot.localize(...)`), not by English labels, so the step works in both languages.
