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
