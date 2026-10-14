"""Paste text the keyboard layout can't produce (emoji, CJK...) through the Wayland
clipboard, restoring whatever the user had copied afterwards.

Used only as type_text's fallback for untypeable runs — everything typeable still goes
through real keystrokes. Verified 2026-10-14: emoji/CJK pasted correctly in Vivaldi
(ctrl+v) and Alacritty (ctrl+shift+v), and the user's clipboard came back byte-identical.
No clipboard-history manager runs on this machine; if one is added later, pasted runs
would show up in its history.
"""

from __future__ import annotations

import subprocess
import time

from . import hyprctl

# Terminals paste with ctrl+shift+v (plain ctrl+v sends a literal ^V to the shell).
TERMINAL_CLASSES = ("alacritty", "kitty", "foot", "wezterm", "konsole", "terminal", "ghostty", "xterm", "st-256color")


class ClipboardError(RuntimeError):
    pass


def _run(args: list[str], data: bytes | None = None) -> bytes:
    try:
        return subprocess.run(args, input=data, capture_output=True, timeout=5, check=True).stdout
    except FileNotFoundError as e:
        raise ClipboardError(f"{args[0]} not found (install wl-clipboard)") from e
    except subprocess.CalledProcessError as e:
        raise ClipboardError(f"{' '.join(args)} failed: {e.stderr.decode(errors='replace').strip()}") from e
    except subprocess.TimeoutExpired as e:
        raise ClipboardError(f"{' '.join(args)} timed out") from e


def _copy(data: bytes | None, mime: str | None = None) -> None:
    """wl-copy forks a daemon that serves the clipboard and inherits our pipes, so its
    output must not be captured (subprocess would wait for that daemon to exit)."""
    args = ["wl-copy", "--clear"] if data is None else ["wl-copy", "--type", mime or "text/plain;charset=utf-8"]
    try:
        subprocess.run(args, input=data, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5, check=True)
    except FileNotFoundError as e:
        raise ClipboardError("wl-copy not found (install wl-clipboard)") from e
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        raise ClipboardError(f"{' '.join(args)} failed: {e}") from e


def _save() -> tuple[str, bytes] | None:
    types = subprocess.run(["wl-paste", "--list-types"], capture_output=True, text=True, timeout=5).stdout.split()
    if not types:
        return None  # clipboard empty
    mime = types[0]
    return mime, _run(["wl-paste", "--no-newline", "--type", mime])


def paste_combo() -> str:
    win = hyprctl.active_window() or {}
    cls = (win.get("class") or "").lower()
    return "ctrl+shift+v" if any(t in cls for t in TERMINAL_CLASSES) else "ctrl+v"


def paste_text(text: str, send_keys) -> None:
    """Paste `text` at the current focus via `send_keys(combo)`, then restore the clipboard."""
    saved = _save()
    combo = paste_combo()
    try:
        _copy(text.encode())
        time.sleep(0.1)  # let wl-copy take ownership before the app asks for the data
        send_keys(combo)
        time.sleep(0.25)  # the app reads the clipboard asynchronously after the keystroke
    finally:
        _copy(None) if saved is None else _copy(saved[1], saved[0])
