"""Pure coordinate math: window/monitor geometry -> click targets.

No I/O here — testable without a live Hyprland session.
"""

from __future__ import annotations

Point = tuple[int, int]

ANCHORS = ("center", "top-left", "titlebar")


def window_rect(window: dict) -> tuple[int, int, int, int]:
    x, y = window["at"]
    w, h = window["size"]
    return x, y, w, h


def anchor_point(window: dict, anchor: str = "center", offset: tuple[int, int] = (0, 0)) -> Point:
    if anchor not in ANCHORS:
        raise ValueError(f"unknown anchor {anchor!r}, expected one of {ANCHORS}")
    x, y, w, h = window_rect(window)
    if anchor == "center":
        px, py = x + w // 2, y + h // 2
    elif anchor == "top-left":
        px, py = x, y
    else:  # titlebar
        px, py = x + w // 2, y + 8
    return px + offset[0], py + offset[1]


def monitor_local_to_global(x: int, y: int, monitor: dict) -> Point:
    """Convert monitor-local coordinates to the compositor's global space.

    hyprctl already reports window/cursor positions in global space, so this only
    applies a monitor's origin offset. Scale is intentionally NOT multiplied in:
    empirically verified on this machine (single scale=1 monitor) that ydotool's
    --absolute space matches hyprctl's global space 1:1. Untested on scale != 1 —
    see README "Riscos abertos" before trusting this on a HiDPI/multi-scale setup.
    """
    return monitor["x"] + x, monitor["y"] + y


def find_window(windows: list[dict], address: str) -> dict | None:
    for w in windows:
        if w.get("address") == address:
            return w
    return None
