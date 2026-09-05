"""ydotool wrapper. Every call goes through the `ydotool` binary via subprocess —
this project never talks to /dev/uinput directly.

Coordinate space confirmed empirically on this machine (2026-09-05): `ydotool
mousemove --absolute -- X Y` followed by `hyprctl cursorpos` reports back the exact
same X,Y. So hyprctl's global compositor space and ydotool's --absolute space are
treated as identical here. This was the biggest open risk in the project plan and
is now resolved for this machine; re-verify if run on a different scale/monitor setup.

Button codes for `click` are the base button id OR'd with 0xC0 (down+up), confirmed
against `ydotool click --help` on the installed build (1.0.4): 0x00 left / 0x01
right / 0x02 middle, 0x40 down flag, 0x80 up flag.

There is no dedicated scroll subcommand in this ydotool build (confirmed via
`ydotool --help`: click, mousemove, type, key, debug, bakers) — scroll is done via
`mousemove --wheel`.
"""

from __future__ import annotations

import subprocess
import time

BUTTON_CODES = {"left": 0x00, "right": 0x01, "middle": 0x02}

# Raw Linux keycodes (see /usr/include/linux/input-event-codes.h) — only what
# send_keys actually needs, not the full KEY_* table.
KEYCODES: dict[str, int] = {
    "a": 30, "b": 48, "c": 46, "d": 32, "e": 18, "f": 33, "g": 34, "h": 35,
    "i": 23, "j": 36, "k": 37, "l": 38, "m": 50, "n": 49, "o": 24, "p": 25,
    "q": 16, "r": 19, "s": 31, "t": 20, "u": 22, "v": 47, "w": 17, "x": 45,
    "y": 21, "z": 44,
    "0": 11, "1": 2, "2": 3, "3": 4, "4": 5, "5": 6, "6": 7, "7": 8, "8": 9, "9": 10,
    "f1": 59, "f2": 60, "f3": 61, "f4": 62, "f5": 63, "f6": 64, "f7": 65,
    "f8": 66, "f9": 67, "f10": 68, "f11": 87, "f12": 88,
    "ctrl": 29, "leftctrl": 29, "rightctrl": 97,
    "shift": 42, "leftshift": 42, "rightshift": 54,
    "alt": 56, "leftalt": 56, "rightalt": 100,
    "super": 125, "leftmeta": 125, "rightmeta": 126, "meta": 125,
    "up": 103, "down": 108, "left": 105, "right": 106,
    "home": 102, "end": 107, "pageup": 104, "pagedown": 109,
    "insert": 110, "delete": 111,
    "enter": 28, "tab": 15, "esc": 1, "escape": 1,
    "backspace": 14, "space": 57,
}


class InputError(RuntimeError):
    pass


def _run(*args: str) -> None:
    try:
        subprocess.run(["ydotool", *args], capture_output=True, timeout=5, check=True)
    except FileNotFoundError as e:
        raise InputError("ydotool not found on PATH") from e
    except subprocess.CalledProcessError as e:
        stderr = e.stderr.decode(errors="replace").strip()
        raise InputError(f"ydotool {' '.join(args)} failed: {stderr}") from e
    except subprocess.TimeoutExpired as e:
        raise InputError(f"ydotool {' '.join(args)} timed out (is ydotoold running?)") from e


def move_mouse_absolute(x: int, y: int) -> None:
    _run("mousemove", "--absolute", "--", str(x), str(y))


def click(button: str = "left", double: bool = False) -> None:
    if button not in BUTTON_CODES:
        raise ValueError(f"unknown button {button!r}, expected one of {tuple(BUTTON_CODES)}")
    code = BUTTON_CODES[button] | 0xC0
    _run("click", f"0x{code:02x}")
    if double:
        time.sleep(0.08)
        _run("click", f"0x{code:02x}")


def scroll(dx: int = 0, dy: int = 0) -> None:
    _run("mousemove", "--wheel", "-x", str(dx), "-y", str(dy))


def type_text(text: str) -> None:
    _run("type", "--", text)


def send_keys(combo: str) -> None:
    """e.g. "ctrl+shift+t" -> press each key in order, release in reverse."""
    tokens = [tok.strip().lower() for tok in combo.split("+") if tok.strip()]
    if not tokens:
        raise ValueError("empty key combo")
    codes = []
    for tok in tokens:
        if tok not in KEYCODES:
            raise ValueError(f"unknown key {tok!r}")
        codes.append(KEYCODES[tok])
    down = [f"{c}:1" for c in codes]
    up = [f"{c}:0" for c in reversed(codes)]
    _run("key", *down, *up)
