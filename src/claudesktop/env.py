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
"""

from __future__ import annotations

import os
from pathlib import Path


def ensure_session_env() -> None:
    runtime_dir = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"))
    os.environ.setdefault("XDG_RUNTIME_DIR", str(runtime_dir))

    if "HYPRLAND_INSTANCE_SIGNATURE" not in os.environ:
        hypr_dir = runtime_dir / "hypr"
        if hypr_dir.is_dir():
            signatures = [p.name for p in hypr_dir.iterdir() if p.is_dir()]
            if len(signatures) == 1:
                os.environ["HYPRLAND_INSTANCE_SIGNATURE"] = signatures[0]
            # Ambiguous (0 or 2+ running instances): leave unset, let hyprctl
            # fail with its own clear error rather than guess wrong.

    if "WAYLAND_DISPLAY" not in os.environ:
        candidates = sorted(
            p.name for p in runtime_dir.glob("wayland-*") if not p.name.endswith(".lock")
        )
        if len(candidates) == 1:
            os.environ["WAYLAND_DISPLAY"] = candidates[0]
