"""Bench camera worker: runs natively on Windows (a USB webcam is not visible in WSL).

Started by scripts/bench/run.py through `uv.exe run --with opencv-python --with
numpy`, never imported by the integration or the tests. It opens the webcam
with OpenCV's DirectShow backend and the settings of spike #133, reads frames
on a thread, stamps each with time.time() (the Windows clock) when read()
returns, and prints one JSON line per frame on standard output with the mean
RGB and the 95th percentile of the brightest channel per region. No frame
leaves this process, except a snapshot the caller asks for.

Usage:
    python camera.py log --regions JSON [--settings JSON]
    python camera.py snapshot OUT.png --regions JSON [--settings JSON]

`log` reads commands on standard input, one per line:
    ping            answers {"event": "pong", "t": <time.time()>}
    snapshot PATH   saves the next frame as a PNG (regions drawn on it)
    quit            stops
It also stops when standard input closes.
"""

import argparse
import json
import os
import queue
import sys
import threading
import time
from typing import Any

import cv2
import numpy as np

# Spike #133: MJPG only sticks when the FOURCC is set after size and rate.
DEFAULTS: dict[str, Any] = {
    "index": 0,
    "width": 1280,
    "height": 720,
    "fps": 60,
    "exposure": -8,
    "wb_temperature": 4000,
}


def _emit(**fields: Any) -> None:
    sys.stdout.write(json.dumps(fields) + "\n")
    sys.stdout.flush()


def _open(settings: dict[str, Any]) -> Any:
    s = {**DEFAULTS, **settings}
    cap = cv2.VideoCapture(int(s["index"]), cv2.CAP_DSHOW)
    if not cap.isOpened():
        _emit(event="error", message="the camera cannot be opened")
        sys.exit(3)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, s["width"])
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, s["height"])
    cap.set(cv2.CAP_PROP_FPS, s["fps"])
    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
    cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, 0.25)
    cap.set(cv2.CAP_PROP_EXPOSURE, s["exposure"])
    cap.set(cv2.CAP_PROP_AUTO_WB, 0)
    cap.set(cv2.CAP_PROP_WB_TEMPERATURE, s["wb_temperature"])
    cap.set(cv2.CAP_PROP_AUTOFOCUS, 0)
    for _ in range(20):  # let exposure and white balance settle
        cap.read()
    fourcc = int(cap.get(cv2.CAP_PROP_FOURCC))
    _emit(
        event="started",
        pid=os.getpid(),
        width=int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
        height=int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
        fourcc="".join(chr((fourcc >> (8 * i)) & 0xFF) for i in range(4)),
        t=time.time(),
    )
    return cap


def _measure(frame: Any, regions: dict[str, list[int]]) -> dict[str, Any]:
    out = {}
    for name, (x, y, w, h) in regions.items():
        crop = frame[y : y + h, x : x + w].reshape(-1, 3).astype(np.float32)
        mean = crop.mean(axis=0)[::-1]  # BGR to RGB
        p95 = float(np.percentile(crop.max(axis=1), 95))
        out[name] = [round(float(v), 1) for v in mean] + [round(p95, 1)]
    return out


def _draw(frame: Any, regions: dict[str, list[int]]) -> Any:
    shown = frame.copy()
    for name, (x, y, w, h) in regions.items():
        cv2.rectangle(shown, (x, y), (x + w, y + h), (0, 255, 255), 1)
        cv2.putText(
            shown,
            name,
            (x, max(10, y - 4)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.4,
            (0, 255, 255),
            1,
        )
    return shown


def _commands(out: "queue.Queue[str]") -> None:
    for line in sys.stdin:
        out.put(line.strip())
    out.put("quit")


def log(regions: dict[str, list[int]], settings: dict[str, Any]) -> None:
    cap = _open(settings)
    commands: queue.Queue[str] = queue.Queue()
    threading.Thread(target=_commands, args=(commands,), daemon=True).start()
    snapshot: str | None = None
    while True:
        ok, frame = cap.read()
        t = time.time()
        if ok:
            _emit(t=round(t, 4), r=_measure(frame, regions))
            if snapshot is not None:
                cv2.imwrite(snapshot, _draw(frame, regions))
                _emit(event="snapshot", path=snapshot)
                snapshot = None
        while not commands.empty():
            command = commands.get()
            if command == "quit":
                cap.release()
                _emit(event="stopped")
                return
            if command == "ping":
                _emit(event="pong", t=time.time())
            elif command.startswith("snapshot "):
                snapshot = command.split(" ", 1)[1]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["log", "snapshot"])
    parser.add_argument("out", nargs="?")
    parser.add_argument("--regions", default="{}")
    parser.add_argument("--settings", default="{}")
    args = parser.parse_args()
    regions = json.loads(args.regions)
    settings = json.loads(args.settings)
    if args.command == "log":
        log(regions, settings)
        return
    cap = _open(settings)
    ok, frame = cap.read()
    cap.release()
    if not ok:
        _emit(event="error", message="no frame")
        sys.exit(3)
    cv2.imwrite(args.out, _draw(frame, regions))
    _emit(event="snapshot", path=args.out, r=_measure(frame, regions))


if __name__ == "__main__":
    main()
