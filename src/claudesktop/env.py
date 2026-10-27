"""Auto-discovers Wayland/Hyprland session environment variables.

MCP host apps commonly spawn a stdio server subprocess with only a minimal safe
env allowlist (HOME/PATH/USER/...), not the caller's full session environment —
confirmed in the official Python SDK's own client (`DEFAULT_INHERITED_ENV_VARS`
in mcp.client.stdio). Without HYPRLAND_INSTANCE_SIGNATURE/WAYLAND_DISPLAY,
hyprctl and grim can't find the running compositor.

Rather than baking a session id into the MCP registration (which goes stale on
every reboot/relogin), this reads the well-known files any Hyprland/Wayland
session drops under XDG_RUNTIME_DIR — the same signal is valid on any machine
and any session, not just this one.

Discovery is re-run before every tool call (see server.py), not just once at
startup: hosts that run as a systemd user service (Hermes gateway) spawn this
server at login, ~2s BEFORE Hyprland creates its socket dir and wayland-N, so a
one-shot check found nothing and every grim call failed with "failed to create
display" for the rest of the session. A value whose socket vanished (compositor
restart) is also dropped and rediscovered.
"""

from __future__ import annotations

import os
from pathlib import Path


def _runtime_dir() -> Path:
    runtime_dir = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"))
    os.environ.setdefault("XDG_RUNTIME_DIR", str(runtime_dir))
    return runtime_dir


def _refresh_hyprland(runtime_dir: Path) -> None:
    hypr_dir = runtime_dir / "hypr"
    current = os.environ.get("HYPRLAND_INSTANCE_SIGNATURE")
    if current and (hypr_dir / current).is_dir():
        return
    os.environ.pop("HYPRLAND_INSTANCE_SIGNATURE", None)
    if not hypr_dir.is_dir():
        return
    # Ambiguous (0 or 2+ instances): leave unset so hyprctl fails with its own clear error.
    signatures = [p.name for p in hypr_dir.iterdir() if p.is_dir()]
    if len(signatures) == 1:
        os.environ["HYPRLAND_INSTANCE_SIGNATURE"] = signatures[0]


def _refresh_wayland(runtime_dir: Path) -> None:
    current = os.environ.get("WAYLAND_DISPLAY")
    if current and (runtime_dir / current).exists():
        return
    os.environ.pop("WAYLAND_DISPLAY", None)
    candidates = sorted(p.name for p in runtime_dir.glob("wayland-*") if not p.name.endswith(".lock"))
    if len(candidates) == 1:
        os.environ["WAYLAND_DISPLAY"] = candidates[0]


def _refresh_dbus(runtime_dir: Path) -> None:
    # systemd/Hermes spawn without DBUS_SESSION_BUS_ADDRESS; AT-SPI (jeepney) needs it.
    bus = runtime_dir / "bus"
    if "DBUS_SESSION_BUS_ADDRESS" not in os.environ and bus.exists():
        os.environ["DBUS_SESSION_BUS_ADDRESS"] = f"unix:path={bus}"


def ensure_session_env() -> None:
    """Cheap (a few stat calls) and idempotent: safe to call before every tool."""
    runtime_dir = _runtime_dir()
    _refresh_hyprland(runtime_dir)
    _refresh_wayland(runtime_dir)
    _refresh_dbus(runtime_dir)
