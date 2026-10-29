"""Bench screen worker: a borderless full-screen window that shows test pictures.

Runs natively on Windows with the standard library only (Tk), started by
scripts/bench/run.py through `uv.exe run`. Never imported by the integration or
the tests. The window covers the monitor whose physical top-left corner is
--left/--top (the process is per-monitor DPI aware, so these are physical
pixels, as the screen helper's mss reports them).

Commands on standard input, one per line:
    #rrggbb               the whole window in one colour
    split #rrggbb #rrggbb the left half in the first colour, the right half
                          in the second
    ping                  answers {"event": "pong", "t": <time.time()>}
    quit                  closes the window
Each picture prints {"event": "shown", "picture": ..., "t": <time.time()>}
once Tk has drawn it. It also stops when standard input closes.
"""

import argparse
import ctypes
import json
import os
import queue
import sys
import threading
import time
import tkinter as tk
from typing import Any


def _emit(**fields: Any) -> None:
    sys.stdout.write(json.dumps(fields) + "\n")
    sys.stdout.flush()


def _commands(out: "queue.Queue[str]") -> None:
    for line in sys.stdin:
        out.put(line.strip())
    out.put("quit")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--left", type=int, required=True)
    parser.add_argument("--top", type=int, required=True)
    parser.add_argument("--width", type=int, required=True)
    parser.add_argument("--height", type=int, required=True)
    args = parser.parse_args()
    # Before Tk: physical pixels on every monitor (per-monitor DPI aware).
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
    root = tk.Tk()
    root.overrideredirect(True)
    # "+-1291" is y = -1291 from the top; "-1291" would count from the bottom.
    root.geometry(f"{args.width}x{args.height}+{args.left}+{args.top}")
    root.attributes("-topmost", True)
    root.configure(background="#000000", cursor="none")
    canvas = tk.Canvas(root, highlightthickness=0, background="#000000")
    canvas.pack(fill="both", expand=True)
    left = canvas.create_rectangle(0, 0, 0, 0, width=0, fill="#000000")
    right = canvas.create_rectangle(0, 0, 0, 0, width=0, fill="#000000")
    commands: queue.Queue[str] = queue.Queue()
    threading.Thread(target=_commands, args=(commands,), daemon=True).start()

    def show(first: str, second: str, picture: str) -> None:
        w, h = args.width, args.height
        canvas.coords(left, 0, 0, w // 2, h)
        canvas.coords(right, w // 2, 0, w, h)
        canvas.itemconfigure(left, fill=first)
        canvas.itemconfigure(right, fill=second)
        root.update()
        _emit(event="shown", picture=picture, t=round(time.time(), 4))

    def poll() -> None:
        while not commands.empty():
            command = commands.get()
            if command == "quit":
                root.destroy()
                _emit(event="stopped")
                return
            if command == "ping":
                _emit(event="pong", t=time.time())
            elif command.startswith("split "):
                _, first, second = command.split()
                show(first, second, command)
            elif command.startswith("#"):
                show(command, command, command)
        root.after(2, poll)

    root.update()
    root.lift()
    root.focus_force()
    _emit(
        event="started",
        pid=os.getpid(),
        geometry=[
            root.winfo_x(),
            root.winfo_y(),
            root.winfo_width(),
            root.winfo_height(),
        ],
    )
    show("#000000", "#000000", "#000000")
    root.after(2, poll)
    root.mainloop()


if __name__ == "__main__":
    main()
