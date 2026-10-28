# Developer scripts

These scripts are tools for developing Glowcast; they are not part of the integration and HACS does not install them. They need the test requirements from `.github/workflows/ci.yml` (`pytest-homeassistant-custom-component` and `aiowebostv`).

- `try_webos_source.py`: runs the webOS frame source against a real LG TV and prints timings and average colours. See the docstring at the top of the script for usage.
