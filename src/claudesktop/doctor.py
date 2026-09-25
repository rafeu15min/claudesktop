"""Read-only environment diagnostics, shared between the CLI and the `check_environment`
MCP tool. Never mutates anything — safe to call at the start of any session."""

from __future__ import annotations

import os
import shutil
import socket
from pathlib import Path

from . import atspi, hyprctl


def _ydotoold_reachable() -> bool:
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    sock_path = Path(runtime_dir) / ".ydotool_socket"
    if not sock_path.exists():
        return False
    try:
        # ydotoold's socket is SOCK_DGRAM, not SOCK_STREAM — confirmed empirically
        # (a STREAM connect fails with ENOTSOCK/"Protocol wrong type for socket").
        s = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        s.settimeout(1)
        s.connect(str(sock_path))
        s.close()
        return True
    except OSError:
        return False


def check_environment() -> dict:
    uinput = Path("/dev/uinput")
    hyprctl_ok = False
    if shutil.which("hyprctl"):
        try:
            hyprctl.monitors()
            hyprctl_ok = True
        except hyprctl.HyprctlError:
            hyprctl_ok = False
    atspi_bus_ok = False
    try:
        conn = atspi.connect()
        conn.close()
        atspi_bus_ok = True
    except atspi.AtspiError:
        atspi_bus_ok = False
    return {
        "ydotoold_reachable": _ydotoold_reachable(),
        "grim_ok": shutil.which("grim") is not None,
        "hyprctl_ok": hyprctl_ok,
        "uinput_writable": uinput.exists() and os.access(uinput, os.W_OK),
        "atspi_bus_reachable": atspi_bus_ok,
    }
