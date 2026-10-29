from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def config_dir(tmp_path: Path) -> Path:
    """The helper's config directory for a test; never the user's real one."""
    return tmp_path / "config"
