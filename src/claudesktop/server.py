"""claudesktop MCP server: screen-aware mouse/keyboard control tools for Hyprland.

Every tool that mutates state (move/click/scroll/type/keys) returns a fresh
screenshot alongside its result — built into v1 rather than bolted on later,
because the agent has no continuous perception of the screen and needs to
confirm each action's effect before deciding the next one.
"""

from __future__ import annotations

from mcp.server.mcpserver import Image, MCPServer

import time

from . import atspi, audio, cdp, geometry, hyprctl, ocr, screenshot
from . import input as ydinput
from .doctor import check_environment as _check_environment

mcp = MCPServer("claudesktop")


def _after_action_screenshot(shot: str = "full") -> Image:
    """shot="full" (default, required by project rules) | "window" (active window only)
    | "small" (fullscreen at half scale) — the cheaper ones are opt-in per call."""
    if shot == "full":
        data = screenshot.capture_fullscreen()
    elif shot == "small":
        data = screenshot.capture_scaled(0.5)
    elif shot == "window":
        win = hyprctl.active_window()
        if win is None:
            data = screenshot.capture_fullscreen()
        else:
            (x, y), (w, h) = win["at"], win["size"]
            data = screenshot.capture_region(x, y, w, h)
    else:
        raise ValueError(f"unknown shot {shot!r}, expected 'full', 'window' or 'small'")
    return Image(data=data, format="png")


def _ensure_keyboard_allowed(on_lock_screen: bool) -> None:
    if ydinput.screen_locked() and not on_lock_screen:
        raise RuntimeError(
            "screen is locked (hyprlock): keystrokes would go to its password field. "
            "Only if the user told you to type the password, call again with on_lock_screen=true."
        )


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
def move_mouse(x: int, y: int, monitor: str | None = None, shot: str = "full") -> list:
    """Move the cursor to absolute coordinates. If `monitor` is given, x/y are local to that monitor."""
    if monitor is not None:
        mon = next((m for m in hyprctl.monitors() if m["name"] == monitor), None)
        if mon is None:
            raise ValueError(f"unknown monitor {monitor!r}")
        x, y = geometry.monitor_local_to_global(x, y, mon)
    ydinput.move_mouse_absolute(x, y)
    return [{"moved_to": [x, y]}, _after_action_screenshot(shot)]


@mcp.tool()
def move_to_window(address: str, anchor: str = "center", offset: list[int] | None = None, shot: str = "full") -> list:
    """Move the cursor to a point on a specific window (by hyprctl address); anchor: center/top-left/titlebar."""
    window = geometry.find_window(hyprctl.clients(), address)
    if window is None:
        raise ValueError(f"no window with address {address!r}")
    x, y = geometry.anchor_point(window, anchor, tuple(offset) if offset else (0, 0))
    ydinput.move_mouse_absolute(x, y)
    return [{"moved_to": [x, y], "window": window.get("class")}, _after_action_screenshot(shot)]


@mcp.tool()
def focus_window(address: str | None = None, match: str | None = None, shot: str = "full") -> list:
    """Give keyboard focus to a window, switching to its workspace if needed — works even when
    it is hidden behind other windows. Pass its hyprctl `address` (from list_windows) or
    `match`, a substring of its class or title that must hit exactly one window."""
    if (address is None) == (match is None):
        raise ValueError("give exactly one of address, match")
    if match is not None:
        address = hyprctl.find_client(match)["address"]
    active = hyprctl.focus_window(address)
    return [{"focused": address, "class": active.get("class"), "title": active.get("title")}, _after_action_screenshot(shot)]


@mcp.tool()
def click(button: str = "left", double: bool = False, x: int | None = None, y: int | None = None, shot: str = "full") -> list:
    """Click a mouse button at the current cursor position, or move there first if x/y are given."""
    if x is not None and y is not None:
        ydinput.move_mouse_absolute(x, y)
    ydinput.click(button, double)
    return [{"clicked": button, "double": double}, _after_action_screenshot(shot)]


@mcp.tool()
def scroll(dx: int = 0, dy: int = 0, shot: str = "full") -> list:
    """Scroll the mouse wheel by dx/dy steps."""
    ydinput.scroll(dx, dy)
    return [{"scrolled": [dx, dy]}, _after_action_screenshot(shot)]


@mcp.tool()
def type_text(
    text: str, shot: str = "full", on_lock_screen: bool = False, key_delay_ms: int = ydinput.DEFAULT_KEY_DELAY_MS
) -> list:
    """Type a string of free text at the current focus. Everything the active keyboard layout
    can produce (accents, / : ; etc.) is typed as real keystrokes; anything else (emoji, CJK...)
    is pasted via the clipboard, which is restored afterwards. key_delay_ms (default 3) is the
    gap between key events — raise it (~20-30) for games that read input once per frame.
    Refuses while the screen is locked unless on_lock_screen=true. shot: "full" | "window" |
    "small" for the after-action screenshot."""
    _ensure_keyboard_allowed(on_lock_screen)
    result = ydinput.type_text(text, key_delay_ms)
    return [{"typed_chars": len(text), **result}, _after_action_screenshot(shot)]


@mcp.tool()
def send_keys(keys: str, shot: str = "full", on_lock_screen: bool = False) -> list:
    """Send a key combination, e.g. "ctrl+shift+t" or "enter". Refuses while the screen is
    locked unless on_lock_screen=true."""
    _ensure_keyboard_allowed(on_lock_screen)
    ydinput.send_keys(keys)
    return [{"sent": keys}, _after_action_screenshot(shot)]


@mcp.tool()
def wait_for(
    window: str | None = None,
    atspi_app: str | None = None,
    atspi_name_contains: str | None = None,
    region: list[int] | None = None,
    timeout_s: float = 10,
    interval_s: float = 0.5,
    min_changed_fraction: float = 0.002,
    shot: str = "full",
) -> list:
    """Poll until a condition holds instead of taking repeated screenshots. Give exactly one:
    window (substring of a window's class or title), atspi_app + atspi_name_contains (an
    element appears in a GTK/Qt app), or region [x, y, w, h] (at least min_changed_fraction of
    its pixels change, so a blinking caret doesn't count).
    Returns {found, elapsed_s} plus one final screenshot."""
    given = [window is not None, atspi_app is not None, region is not None]
    if sum(given) != 1:
        raise ValueError("give exactly one of window, atspi_app, region")

    def window_present() -> bool:
        needle = window.lower()
        return any(
            needle in (c.get("class") or "").lower() or needle in (c.get("title") or "").lower()
            for c in hyprctl.clients()
        )

    def atspi_present() -> bool:
        try:
            return bool(atspi.find(atspi_app, name_contains=atspi_name_contains or ""))
        except atspi.AtspiError:
            return False  # app not registered yet

    if region is not None:
        x, y, w, h = region
        baseline = screenshot.capture_region_pixels(x, y, w, h)
        # A blinking text caret alone changes a few dozen pixels; require a real change.
        threshold = max(200, int(w * h * min_changed_fraction))

        def region_changed() -> bool:
            now = screenshot.capture_region_pixels(x, y, w, h)
            changed = sum(1 for i in range(0, min(len(now), len(baseline)), 3) if now[i : i + 3] != baseline[i : i + 3])
            return changed >= threshold

        check = region_changed
    else:
        check = window_present if window is not None else atspi_present

    start = time.monotonic()
    found = check()
    while not found and time.monotonic() - start < timeout_s:
        time.sleep(interval_s)
        found = check()
    return [{"found": found, "elapsed_s": round(time.monotonic() - start, 2)}, _after_action_screenshot(shot)]


@mcp.tool()
def read_text(
    address: str | None = None,
    match: str | None = None,
    region: list[int] | None = None,
    lang: str = "por+eng",
    min_conf: float = 40,
) -> dict:
    """OCR the text on screen — for terminals, games and anything without an AT-SPI/DOM tree.
    Target: a window by `address` or `match` (class/title substring), a `region` [x, y, w, h],
    or the active window if none given. Returns `text` (by line) and `words`, each with a
    screen `center` you can pass to `click`. Good for words and labels; loose symbol strings
    are unreliable. Read-only."""
    if sum(v is not None for v in (address, match, region)) > 1:
        raise ValueError("give at most one of address, match, region")
    if region is not None:
        x, y, w, h = region
    else:
        if match is not None:
            win = hyprctl.find_client(match)
        elif address is not None:
            win = geometry.find_window(hyprctl.clients(), address)
            if win is None:
                raise ValueError(f"no window with address {address!r}")
        else:
            win = hyprctl.active_window()
            if win is None:
                raise ValueError("no active window; pass a region")
        (x, y), (w, h) = win["at"], win["size"]
    return ocr.read(x, y, w, h, lang=lang, min_conf=min_conf)


@mcp.tool()
def audio_level(seconds: float = 2.0) -> dict:
    """Record `seconds` of what the default output device is playing and report peak/RMS in
    dBFS, sound_detected (peak above ~-70 dBFS), and the apps with an audio stream (muted /
    paused flags). Read-only — use it to check whether a game or app is really producing sound."""
    return audio.level(seconds)


@mcp.tool()
def check_environment() -> dict:
    """Read-only diagnostics: confirms ydotoold/grim/hyprctl/uinput are all working. Never mutates anything."""
    return _check_environment()


# --- Virtual gamepad: DISABLED (2026-10-14, user decision) ---------------------------------
# gamepad.py (virtual Xbox 360 pad, verified via SDL2) is kept but its tools are not
# registered: the user found no use for it for now and set gamepad issues to lowest
# priority. Re-register gamepad_connect/disconnect/press/stick/trigger only if asked.


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
    shot: str = "full",
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
        return [{"clicked_via": "action", "target": target, "ok": ok}, _after_action_screenshot(shot)]

    if via != "mouse":
        raise ValueError(f"unknown via {via!r}, expected 'mouse' or 'action'")
    if "extents" not in target:
        raise ValueError(f"{target['name']!r} has no on-screen extents (hidden/unmapped) — try via='action'")
    x, y = target["extents"]["center"]
    ydinput.move_mouse_absolute(x, y)
    ydinput.click("left", False)
    return [{"clicked_via": "mouse", "target": target}, _after_action_screenshot(shot)]


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
def browser_click(mark: str, target_index: int = 0, port: int = cdp.DEFAULT_PORT, shot: str = "full") -> list:
    """Click the element tagged `mark` by a prior browser_find call."""
    result = cdp.click_marked(mark, target_index=target_index, port=port)
    return [result, _after_action_screenshot(shot)]


@mcp.tool()
def browser_type(mark: str, text: str, target_index: int = 0, port: int = cdp.DEFAULT_PORT, shot: str = "full") -> list:
    """Set an <input>/<textarea> (tagged `mark` by a prior browser_find) to `text`, dispatching
    the input/change events the page listens for. Use this instead of type_text for browser
    form fields — it goes through JS, not real keystrokes, so it can't hit keyboard-layout bugs."""
    result = cdp.type_marked(mark, text, target_index=target_index, port=port)
    return [result, _after_action_screenshot(shot)]


@mcp.tool()
def browser_get_text(selector: str = "body", target_index: int = 0, port: int = cdp.DEFAULT_PORT) -> str:
    """Read the visible text of an element in a browser tab (default: the whole page). Read-only."""
    return cdp.get_text(selector, target_index=target_index, port=port)


@mcp.tool()
def browser_navigate(url: str, target_index: int = 0, port: int = cdp.DEFAULT_PORT, shot: str = "full") -> list:
    """Navigate a browser tab to `url` via CDP directly — no address-bar click/type needed."""
    cdp.navigate(url, target_index=target_index, port=port)
    return [{"navigated_to": url}, _after_action_screenshot(shot)]


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
