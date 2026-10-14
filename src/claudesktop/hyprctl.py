"""Thin wrapper around `hyprctl -j`. Always calls fresh — no caching."""

from __future__ import annotations

import json
import subprocess


class HyprctlError(RuntimeError):
    pass


def _run(*args: str) -> str:
    try:
        result = subprocess.run(
            ["hyprctl", *args], capture_output=True, text=True, timeout=5, check=True
        )
    except FileNotFoundError as e:
        raise HyprctlError("hyprctl not found on PATH") from e
    except subprocess.CalledProcessError as e:
        raise HyprctlError(f"hyprctl {' '.join(args)} failed: {e.stderr.strip()}") from e
    except subprocess.TimeoutExpired as e:
        raise HyprctlError(f"hyprctl {' '.join(args)} timed out") from e
    return result.stdout


def clients() -> list[dict]:
    return json.loads(_run("clients", "-j"))


def monitors() -> list[dict]:
    return json.loads(_run("monitors", "-j"))


def active_window() -> dict | None:
    data = json.loads(_run("activewindow", "-j"))
    return data or None


def keyboards() -> list[dict]:
    return json.loads(_run("devices", "-j"))["keyboards"]


def find_client(match: str) -> dict:
    """The one window whose class or title contains `match` (case-insensitive)."""
    needle = match.lower()
    hits = [
        c for c in clients()
        if needle in (c.get("class") or "").lower() or needle in (c.get("title") or "").lower()
    ]
    if len(hits) != 1:
        found = [f"{c.get('class')} | {c.get('title')} | {c.get('address')}" for c in hits]
        raise HyprctlError(f"{len(hits)} windows match {match!r} (need exactly 1): {found}")
    return hits[0]


def focus_window(address: str) -> dict:
    """Focus a window by address — switches to its workspace if needed. Hyprland 0.56
    with a Lua config takes Lua dispatch expressions (`hl.dsp.focus{window=...}`); the
    old `dispatch focuswindow address:...` form is a Lua syntax error there. A missing
    window only prints a warning with exit 0, so success is checked by re-reading the
    active window rather than trusting the exit code."""
    if not address.startswith("0x") or not all(ch in "0123456789abcdef" for ch in address[2:].lower()):
        raise ValueError(f"not a window address: {address!r}")
    out = _run("dispatch", f'hl.dsp.focus({{ window = "address:{address}" }})').strip()
    active = active_window()
    if not active or active.get("address") != address:
        raise HyprctlError(f"focus {address} failed: {out or 'window did not become active'}")
    return active


def cursor_pos() -> tuple[int, int]:
    raw = _run("cursorpos").strip()
    x_str, y_str = raw.split(",")
    return int(x_str.strip()), int(y_str.strip())


def list_windows() -> dict:
    """Combined view for the `list_windows` MCP tool: clients + monitors, active window flagged."""
    all_clients = clients()
    active = active_window()
    active_address = active.get("address") if active else None
    for w in all_clients:
        w["focused"] = w.get("address") == active_address
    return {"windows": all_clients, "monitors": monitors()}
