# Developer scripts

These scripts are tools for developing Glowcast; they are not part of the integration and HACS does not install them. They need the test requirements from `.github/workflows/ci.yml` (`pytest-homeassistant-custom-component` and `aiowebostv`), and `try_hue_output.py` and `try_pipeline.py` also need `hue-entertainment==0.1.2`.

- `try_webos_source.py`: runs the webOS frame source against a real LG TV and prints timings and average colours.
- `try_hue_output.py`: runs the Hue entertainment output against a real bridge: a left/centre/right pattern, a very dark colour, then stop.
- `try_pipeline.py`: runs the pipeline with the real TV source and Hue output for a while, then stops, and prints the start time, rates and stop reason.

See the docstring at the top of each script for usage. Credentials come from environment variables; never put them in files in the repository.
