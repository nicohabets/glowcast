# Developer scripts

These scripts are tools for developing Glowcast; they are not part of the integration and HACS does not install them. They need the test requirements from `.github/workflows/ci.yml` (`pytest-homeassistant-custom-component` and `aiowebostv`), and `try_hue_output.py` and `try_pipeline.py` also need `hue-entertainment==0.1.2`.

- `try_webos_source.py`: runs the webOS frame source against a real LG TV and prints timings and average colours.
- `try_hue_output.py`: runs the Hue entertainment output against a real bridge: a left/centre/right pattern, a very dark colour, then stop.
- `try_pipeline.py`: runs the pipeline with the real TV source and Hue output for a while, then stops, and prints the start time, rates and stop reason.
- `render_brand.py`: builds the icon and logo PNGs in `custom_components/glowcast/brand/` from the sources in `docs/brand/`. It does not need the test requirements: run it with `uv run scripts/render_brand.py`, which installs its own pinned dependencies. See [docs/brand/README.md](../docs/brand/README.md).
- `render_hacs_readme.cjs`: renders `README.md` the way HACS shows it (the same `marked` and `xss` versions and settings), to check that no HTML shows as text. It needs Node and `npm install marked@15.0.4 xss@1.0.15`, not the test requirements; it is not part of CI.
- `demo.py`: a local Home Assistant with Glowcast from the working copy, to record GIFs of its flows and check the UI; see below.

See the docstring at the top of each script for usage. Credentials come from environment variables; never put them in files in the repository.

## The local demo

`demo.py` runs Home Assistant 2026.10.4 (the "latest" version in `.github/workflows/ci.yml`; bump both together) on `127.0.0.1` with Glowcast from the working copy, a TV called "Living room TV" and a Hue bridge with the entertainment areas "TV area" and "Whole room". It does not need the test requirements; it installs its own.

- Nothing reaches a real TV or bridge: stub `hue` and `webostv` integrations (`demo/stubs/`) only report their entries as loaded, the bridge is a fake one on `127.0.0.1` (`demo/fake_bridge.py`, including Hue's pairing reply), and Home Assistant loads no `default_config` and no discovery integration. `start` checks this for every instance and stops if it is not so. The stubs replace Home Assistant's own integrations: never copy them to a real Home Assistant.
- Switching Glowcast on works, but no TV answers on `127.0.0.1`: after the start-up grace (about 10 s) it stops with "the TV is off or not reachable" in the log.
- Each working copy (each worktree) runs its own instance, so several sessions can use the demo at the same time. The instance is named after the working copy's folder and a short hash of its path (for example `glowcast-1a2b3c`); `--instance NAME` acts on another one. `start`, `record`, `stop` and `clean` act on their own instance only, and print its name. `record` refuses to run from another working copy than the one that started the instance.
- Ports: `start` picks them and stores them in the instance's `state.json`; `record` and `status` read them from there. The first instance gets `8199` (Home Assistant) and `8443` (the fake bridge) when they are free, the others get free ones: look up the URL with `status` or in `start`'s output.
- Everything it makes lives in `~/.cache/glowcast-demo`, outside the repository: per instance the config directory, pid files, logs, GIFs and screenshots in `instances/<instance>/`, and one virtual environment for all of them in `venv/`. Playwright keeps its Chromium in `~/.cache/ms-playwright`. The first install takes a lock, so two `start`s never install at once; the environment is only changed (a pin bumped here) while no instance runs, and Home Assistant runs with `--skip-pip`, so a running instance never installs into it. A requirement added to Glowcast's manifest must be added to `REQUIREMENTS` in `demo.py` too.
- A demo from before instances (state directly in `~/.cache/glowcast-demo/config` and `run/`) shows as the `legacy` instance in `status`; `stop --instance legacy` stops it and `clean --instance legacy` removes its config, logs and recordings. While it exists, new instances leave `8199` and `8443` to it. A session still on the old `demo.py` removes all of `~/.cache/glowcast-demo` with its `clean`, other instances included.
- `ffmpeg` for the GIFs comes with the `imageio-ffmpeg` package in the demo's environment; no system install needed.
- It runs with Python alone on Linux and macOS (on Windows in WSL). Home Assistant needs Python 3.14.2 or newer; if the Python you run `demo.py` with is older, it uses [uv](https://docs.astral.sh/uv/) to fetch one.

```
python3 scripts/demo.py start                          # first run installs (a few minutes)
python3 scripts/demo.py record                         # every flow; or name some: setup settings device entities
python3 scripts/demo.py record --lang nl --theme dark  # en|nl, light|dark; --out DIR
python3 scripts/demo.py record settings --still        # a PNG of each step instead of a GIF
python3 scripts/demo.py stop
python3 scripts/demo.py clean                          # stop and remove this instance
python3 scripts/demo.py status                         # all instances: working copy, ports, running, URL
python3 scripts/demo.py stop --instance legacy         # --instance NAME: another instance
python3 scripts/demo.py clean --all                    # stop all and remove ~/.cache/glowcast-demo (asks first; --yes)
```

`start` always begins with a fresh config directory without a Glowcast entry. Log in at the URL `start` prints (http://127.0.0.1:8199 for the first instance) as `demo` / `demo-password` to look around.

`record` sets Home Assistant's own language to `--lang` too (entity names come from the server in its language, not from the profile), and records in Home Assistant's mobile view (400 px wide at device scale factor 2, so 800 px wide with sharp text), with a ring where each click lands and a short pause on each step, and saves one GIF per flow as `<lang>-<theme>-<flow>.gif` in the instance's `recordings` folder (it warns when one is over GitHub's 10 MB). The flows:
- `setup`: the setup flow from "Do you want to set up Glowcast?": TV, bridge, link button, pairing (the fake bridge answers), area, done. It first removes a Glowcast entry from an earlier run.
- `settings`: from the integration page to the settings screen, choosing another update rate, saving.
- `device`: from the integration page to Glowcast's device page.
- `entities`: the settings on the device page, under Configuration: brightness, intensity, update rate and entertainment area (#13). It starts from the default settings; the switch stays off, because no TV answers in the demo.

The other flows set Glowcast up first (unrecorded) if needed. With `--still`, each step is saved as `<lang>-<theme>-<flow>-<nn>-<name>.png` instead, in the same mobile view. If a flow fails, `error.png` shows the page at that moment. GIFs and screenshots for a pull request go up as GitHub attachments (rule 11 in `CLAUDE.md`); do not commit them.

To add a flow (for example new entities or a status), write a function in `demo/record.py` that takes a `Recorder`: open the first screen (`rec.goto(...)`), call `rec.begin()` when it is ready (the GIF starts there), click with `rec.click(...)` and call `rec.step("name")` on each screen worth a pause; then add it to `FLOWS` there (`demo.py` passes flow names on). Find buttons by slot, by option value (`rec.option(...)`) or by the frontend's own texts (`rec.localize(...)`), not by English labels, so the flow works in both languages.
