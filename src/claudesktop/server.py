"""claudesktop MCP server: screen-aware mouse/keyboard control tools for Hyprland.

Every tool that mutates state (move/click/scroll/type/keys) returns a fresh
screenshot alongside its result — built into v1 rather than bolted on later,
because the agent has no continuous perception of the screen and needs to
confirm each action's effect before deciding the next one.
"""

from __future__ import annotations

from mcp.server.mcpserver import Image, MCPServer

from . import atspi, cdp, geometry, hyprctl, screenshot
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


# --- AT-SPI: structured element lookup for native GTK/Qt apps ------------------------
# Prefer these over blind screenshot+click for anything inside a GTK/Qt app: they locate
# elements by name/role instead of pixel guessing, and don't need a fresh screenshot to
# confirm a click landed on the right thing. Does NOT cover Chromium/Vivaldi (see the
# browser_* tools below) or GPU-rendered content like games/terminals — screenshot+click
# remains the fallback for those.


@mcp.tool()
def atspi_apps() -> list[dict]:
    """List every application currently registered with AT-SPI accessibility (read-only).
    Only GTK/Qt-toolkit apps show up here — not Chromium/Vivaldi (use browser_targets
    instead) and not GPU-rendered apps like terminals or games (no accessible tree to expose;
    fall back to screenshot+click for those)."""
    return atspi.list_apps()


@mcp.tool()
def atspi_tree(app: str, max_depth: int = 6, max_children: int = 40) -> dict:
    """Dump the accessibility tree of a running GTK/Qt app (matched by substring of its
    AT-SPI name — see atspi_apps for exact names), with role/name/screen-extents/actions
    per node. Read-only. Use this instead of a screenshot to find exact click targets."""
    return atspi.tree(app, max_depth=max_depth, max_children=max_children)


@mcp.tool()
def atspi_find(
    app: str,
    name: str | None = None,
    name_contains: str | None = None,
    role: str | None = None,
) -> list[dict]:
    """Search a running GTK/Qt app's accessibility tree for elements matching name/role
    (give at least one). Read-only. Returns matches with screen extents when visible —
    feed a match's `extents.center` into atspi_click or the plain `click` tool."""
    return atspi.find(app, name=name, name_contains=name_contains, role=role)


@mcp.tool()
def atspi_click(
    app: str,
    name: str | None = None,
    name_contains: str | None = None,
    role: str | None = None,
    index: int = 0,
    via: str = "mouse",
) -> list:
    """Find one element (same matching as atspi_find) and click it: via="mouse" (default)
    moves the real cursor to its screen center and clicks, like a human would; via="action"
    invokes its AT-SPI action directly instead — works even if occluded or unfocused, but
    some widgets behave differently for a synthetic action than a real click."""
    matches = atspi.find(app, name=name, name_contains=name_contains, role=role)
    if not matches:
        raise ValueError(
            f"no element in {app!r} matching name={name!r} name_contains={name_contains!r} role={role!r}"
        )
    if index >= len(matches):
        raise ValueError(f"only {len(matches)} match(es), index {index} out of range")
    target = matches[index]

    if via == "action":
        conn = atspi.connect()
        try:
            ok = atspi.do_action(conn, tuple(target["ref"]), 0)
        finally:
            conn.close()
        return [{"clicked_via": "action", "target": target, "ok": ok}, _after_action_screenshot()]

    if via != "mouse":
        raise ValueError(f"unknown via {via!r}, expected 'mouse' or 'action'")
    if "extents" not in target:
        raise ValueError(f"{target['name']!r} has no on-screen extents (hidden/unmapped) — try via='action'")
    x, y = target["extents"]["center"]
    ydinput.move_mouse_absolute(x, y)
    ydinput.click("left", False)
    return [{"clicked_via": "mouse", "target": target}, _after_action_screenshot()]


# --- CDP: DOM-aware automation for Chromium-based browsers ----------------------------
# Requires the browser already running with a debugging port open, e.g.
# `vivaldi --remote-debugging-port=9222` — a one-time relaunch the user does themselves
# (this project never restarts the browser itself, that would drop open tabs).


@mcp.tool()
def browser_targets(port: int = cdp.DEFAULT_PORT) -> list[dict]:
    """List open tabs (title + url) in the browser's debugging port. Read-only. Raises with
    setup instructions if no debugging port is open."""
    return cdp.targets(port=port)


@mcp.tool()
def browser_find(
    selector: str | None = None,
    text_contains: str | None = None,
    target_index: int = 0,
    port: int = cdp.DEFAULT_PORT,
) -> list[dict]:
    """Find visible elements in a browser tab by CSS selector and/or visible-text substring
    (omit selector to scan common interactive elements). Read-only. Each match gets a `mark`
    id — pass it to browser_click/browser_type to act on that exact element."""
    return cdp.find_elements(selector=selector, text_contains=text_contains, target_index=target_index, port=port)


@mcp.tool()
def browser_click(mark: str, target_index: int = 0, port: int = cdp.DEFAULT_PORT) -> list:
    """Click the element tagged `mark` by a prior browser_find call."""
    result = cdp.click_marked(mark, target_index=target_index, port=port)
    return [result, _after_action_screenshot()]


@mcp.tool()
def browser_type(mark: str, text: str, target_index: int = 0, port: int = cdp.DEFAULT_PORT) -> list:
    """Set an <input>/<textarea> (tagged `mark` by a prior browser_find) to `text`, dispatching
    the input/change events the page listens for. Use this instead of type_text for browser
    form fields — it goes through JS, not real keystrokes, so it can't hit keyboard-layout bugs."""
    result = cdp.type_marked(mark, text, target_index=target_index, port=port)
    return [result, _after_action_screenshot()]


@mcp.tool()
def browser_get_text(selector: str = "body", target_index: int = 0, port: int = cdp.DEFAULT_PORT) -> str:
    """Read the visible text of an element in a browser tab (default: the whole page). Read-only."""
    return cdp.get_text(selector, target_index=target_index, port=port)


@mcp.tool()
def browser_navigate(url: str, target_index: int = 0, port: int = cdp.DEFAULT_PORT) -> list:
    """Navigate a browser tab to `url` via CDP directly — no address-bar click/type needed."""
    cdp.navigate(url, target_index=target_index, port=port)
    return [{"navigated_to": url}, _after_action_screenshot()]


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
