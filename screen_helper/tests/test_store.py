"""The id and the pairings in the config directory."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from glowcast_screen.store import Store


def test_the_id_stays_across_restarts(config_dir: Path) -> None:
    first = Store(config_dir)
    first.load()
    again = Store(config_dir)
    again.load()
    assert len(first.helper_id) == 32
    assert again.helper_id == first.helper_id


def test_a_token_survives_a_restart_and_is_not_stored(config_dir: Path) -> None:
    store = Store(config_dir)
    store.load()
    token = store.add_token()

    again = Store(config_dir)
    again.load()
    assert again.is_valid(token)
    assert not again.is_valid(token + "x")
    assert not again.is_valid("")
    assert token not in store.path.read_text()


def test_unpair_removes_tokens_and_keeps_the_id(config_dir: Path) -> None:
    store = Store(config_dir)
    store.load()
    helper_id = store.helper_id
    tokens = [store.add_token(), store.add_token()]
    assert store.unpair() == 2

    again = Store(config_dir)
    again.load()
    assert again.helper_id == helper_id
    assert again.paired == 0
    assert not any(again.is_valid(t) for t in tokens)


def test_a_running_helper_sees_an_unpair_from_another_process(config_dir: Path) -> None:
    running = Store(config_dir)
    running.load()
    token = running.add_token()
    assert running.is_valid(token)

    other = Store(config_dir)
    other.load()
    other.unpair()
    # Seen at once, also when the clock has not ticked since the last write.
    assert not running.is_valid(token)


def test_an_unpair_within_the_same_mtime_tick_is_seen(config_dir: Path) -> None:
    running = Store(config_dir)
    running.load()
    token = running.add_token()
    before = running.path.stat().st_mtime_ns
    other = Store(config_dir)
    other.load()
    other.unpair()
    # A coarse file-system clock: the rewrite keeps the old mtime.
    os.utime(other.path, ns=(before, before))
    assert not running.is_valid(token)


@pytest.mark.skipif(os.name == "nt", reason="POSIX file modes")
def test_the_state_file_is_private(config_dir: Path) -> None:
    store = Store(config_dir)
    store.load()
    assert store.path.stat().st_mode & 0o077 == 0
    assert set(json.loads(store.path.read_text())) == {"id", "tokens"}
