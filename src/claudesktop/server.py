"""claudesktop MCP server: screen-aware mouse/keyboard control tools for Hyprland.

Every tool that mutates state (move/click/scroll/type/keys) returns a fresh
screenshot alongside its result — built into v1 rather than bolted on later,
because the agent has no continuous perception of the screen and needs to
confirm each action's effect before deciding the next one.
"""

from __future__ import annotations

from mcp.server.mcpserver import Image, MCPServer

from . import geometry, hyprctl, screenshot
from . import input as ydinput
from .doctor import check_environment as _check_environment

mcp = MCPServer("claudesktop")


def _after_action_screenshot() -> Image:
    return Image(data=screenshot.capture_fullscreen(), format="png")


@mcp.tool()
def list_windows() -> dict:
    """List all Hyprland windows and monitors, with the currently focused window flagged."""
    return hyprctl.list_windows()


@mcp.tool(name="screenshot")
def take_screenshot(monitor: str | None = None, region: list[int] | None = None) -> Image:
    """Capture a screenshot: fullscreen (default), one monitor by name, or a pixel region [x, y, w, h]."""
    if region is not None:
        x, y, w, h = region
        data = screenshot.capture_region(x, y, w, h)
    elif monitor is not None:
        data = screenshot.capture_monitor(monitor)
    else:
        data = screenshot.capture_fullscreen()
    return Image(data=data, format="png")


@mcp.tool()
def move_mouse(x: int, y: int, monitor: str | None = None) -> list:
    """Move the cursor to absolute coordinates. If `monitor` is given, x/y are local to that monitor."""
    if monitor is not None:
        mon = next((m for m in hyprctl.monitors() if m["name"] == monitor), None)
        if mon is None:
            raise ValueError(f"unknown monitor {monitor!r}")
        x, y = geometry.monitor_local_to_global(x, y, mon)
    ydinput.move_mouse_absolute(x, y)
    return [{"moved_to": [x, y]}, _after_action_screenshot()]


@mcp.tool()
def move_to_window(address: str, anchor: str = "center", offset: list[int] | None = None) -> list:
    """Move the cursor to a point on a specific window (by hyprctl address); anchor: center/top-left/titlebar."""
    window = geometry.find_window(hyprctl.clients(), address)
    if window is None:
        raise ValueError(f"no window with address {address!r}")
    x, y = geometry.anchor_point(window, anchor, tuple(offset) if offset else (0, 0))
    ydinput.move_mouse_absolute(x, y)
    return [{"moved_to": [x, y], "window": window.get("class")}, _after_action_screenshot()]


@mcp.tool()
def click(button: str = "left", double: bool = False, x: int | None = None, y: int | None = None) -> list:
    """Click a mouse button at the current cursor position, or move there first if x/y are given."""
    if x is not None and y is not None:
        ydinput.move_mouse_absolute(x, y)
    ydinput.click(button, double)
    return [{"clicked": button, "double": double}, _after_action_screenshot()]


@mcp.tool()
def scroll(dx: int = 0, dy: int = 0) -> list:
    """Scroll the mouse wheel by dx/dy steps."""
    ydinput.scroll(dx, dy)
    return [{"scrolled": [dx, dy]}, _after_action_screenshot()]


@mcp.tool()
def type_text(text: str) -> list:
    """Type a string of free text at the current focus."""
    ydinput.type_text(text)
    return [{"typed_chars": len(text)}, _after_action_screenshot()]


@mcp.tool()
def send_keys(keys: str) -> list:
    """Send a key combination, e.g. "ctrl+shift+t" or "enter"."""
    ydinput.send_keys(keys)
    return [{"sent": keys}, _after_action_screenshot()]


@mcp.tool()
def check_environment() -> dict:
    """Read-only diagnostics: confirms ydotoold/grim/hyprctl/uinput are all working. Never mutates anything."""
    return _check_environment()


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
