"""Find a light (or the monitor) in the camera picture by what changes when it
switches on: the bench's camera-position calibration (#134).

numpy only, so the tests run it in CI; the camera worker (camera.py) calls it
on frames that never leave its process.
"""

from __future__ import annotations

from typing import Any

import numpy as np


def change(on: np.ndarray, off: np.ndarray) -> np.ndarray:
    """Per pixel, how much brighter `on` is than `off` (summed over the channels)."""
    return np.clip(on.astype(np.float32) - off.astype(np.float32), 0, None).sum(axis=2)


def best_window(score: np.ndarray, width: int, height: int) -> tuple[int, int, float]:
    """The top-left corner of the width x height window with the largest sum of
    `score`, and that sum (an integral image: every position in one pass)."""
    rows, cols = score.shape
    width, height = min(width, cols), min(height, rows)
    integral = np.zeros((rows + 1, cols + 1), dtype=np.float64)
    integral[1:, 1:] = score.cumsum(axis=0).cumsum(axis=1)
    sums = (
        integral[height:, width:]
        - integral[:-height, width:]
        - integral[height:, :-width]
        + integral[:-height, :-width]
    )
    y, x = np.unravel_index(int(np.argmax(sums)), sums.shape)
    return int(x), int(y), float(sums[y, x])


def locate(
    on: np.ndarray,
    off: np.ndarray,
    size: tuple[int, int],
    others: list[np.ndarray] | None = None,
    after: np.ndarray | None = None,
) -> dict[str, Any]:
    """Where this light is: the window of `size` that brightens most when it
    switches on, minus what the other lights brighten there (so a region lit
    by two lights is not chosen).

    With `after` (the picture once it is off again), a pixel counts only as
    much as it changed against both pictures without the light, so something
    else that changed meanwhile (a screen, a passing shadow) does not.
    `found` is the mean change per pixel in the window (0-765); `contrast`
    is that over the whole picture's median change (plus 1), so a window
    that merely stands on a picture that changed everywhere is not taken.
    """
    score = change(on, off)
    if after is not None:
        score = np.minimum(score, change(on, after))
    if others:
        score = np.clip(score - np.maximum.reduce(others), 0, None)
    width, height = size
    x, y, total = best_window(score, width, height)
    found = total / (width * height)
    return {
        "box": [x, y, width, height],
        "found": round(found, 1),
        "contrast": round(found / (float(np.median(score)) + 1.0), 1),
    }
