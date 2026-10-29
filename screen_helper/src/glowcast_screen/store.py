"""The helper's id and its pairings, in the user's config directory.

Only SHA-256 hashes of the tokens are stored, so the file holds no usable secret.
The id stays the same across restarts and `--unpair` (Glowcast uses it as the
unique id of its entry, #109).
"""

from __future__ import annotations

import contextlib
import hashlib
import hmac
import json
import os
import secrets
import uuid
from pathlib import Path

import platformdirs

STATE_FILE = "state.json"


def default_directory() -> Path:
    return Path(platformdirs.user_config_dir("glowcast-screen", appauthor=False))


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class Store:
    def __init__(self, directory: Path) -> None:
        self.path = directory / STATE_FILE
        self.helper_id = ""
        self._hashes: list[str] = []
        self._mtime: int | None = None

    def load(self) -> None:
        """Read the state, creating an id on the first run."""
        if self.path.exists():
            self._read()
        if not self.helper_id:
            self.helper_id = uuid.uuid4().hex
            self._write()

    def _read(self) -> None:
        self._mtime = self.path.stat().st_mtime_ns
        data = json.loads(self.path.read_text(encoding="utf-8"))
        self.helper_id = str(data.get("id", ""))
        self._hashes = [str(h) for h in data.get("tokens", [])]

    def _write(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        data = {"id": self.helper_id, "tokens": self._hashes}
        # Readable by the user only (on Windows the config directory is per user).
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            json.dump(data, file)
        os.replace(tmp, self.path)
        self._mtime = self.path.stat().st_mtime_ns

    def _reload_if_changed(self) -> None:
        # Another process (`--unpair`) may have changed the file while we run.
        try:
            mtime = self.path.stat().st_mtime_ns
        except FileNotFoundError:
            self._hashes = []
            return
        if mtime != self._mtime:
            with contextlib.suppress(OSError, ValueError):
                self._read()

    def add_token(self) -> str:
        """A new token for a client that paired; only its hash is kept."""
        self._reload_if_changed()
        token = secrets.token_urlsafe(32)
        self._hashes.append(_hash(token))
        self._write()
        return token

    def is_valid(self, token: str) -> bool:
        self._reload_if_changed()
        digest = _hash(token)
        # Compare against every stored hash, so the time does not depend on the match.
        return sum(hmac.compare_digest(digest, h) for h in self._hashes) > 0

    @property
    def paired(self) -> int:
        return len(self._hashes)

    def unpair(self) -> int:
        """Remove every pairing (the id stays); return how many there were."""
        self._reload_if_changed()
        count = len(self._hashes)
        self._hashes = []
        self._write()
        return count
