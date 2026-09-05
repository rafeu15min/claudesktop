"""grim wrapper: capture fullscreen, per-monitor, or region PNGs to bytes.

`slurp` is deliberately not used anywhere here — it waits for a human to drag a
selection, which makes no sense for an agent driving the screen programmatically.
Region selection uses coordinates from hyprctl instead.
"""

from __future__ import annotations

import subprocess


class ScreenshotError(RuntimeError):
    pass


def _run(*args: str) -> bytes:
    try:
        result = subprocess.run(
            ["grim", *args, "-"], capture_output=True, timeout=10, check=True
        )
    except FileNotFoundError as e:
        raise ScreenshotError("grim not found on PATH") from e
    except subprocess.CalledProcessError as e:
        raise ScreenshotError(f"grim failed: {e.stderr.decode(errors='replace').strip()}") from e
    except subprocess.TimeoutExpired as e:
        raise ScreenshotError("grim timed out") from e
    return result.stdout


def capture_fullscreen() -> bytes:
    return _run()


def capture_monitor(name: str) -> bytes:
    return _run("-o", name)


def capture_region(x: int, y: int, w: int, h: int) -> bytes:
    return _run("-g", f"{x},{y} {w}x{h}")
