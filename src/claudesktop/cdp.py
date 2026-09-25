"""Chrome DevTools Protocol (CDP) automation: DOM-aware clicking/typing/reading for any
Chromium-based browser (Vivaldi, Chrome, Brave, ...), built directly into claudesktop —
independent of the separate `claude-in-chrome` extension/skill.

Requires the browser to already be running with a debugging port open, e.g.:

    vivaldi --remote-debugging-port=9222

The user has authorized restarting the browser to enable this when needed (2026-09-06):
session restore brings tabs back, so a relaunch doesn't lose anything. This module
itself still never does that automatically — the caller (server.py / the agent) decides
when a relaunch is warranted. Every function here raises `CdpError` with the relaunch
instruction if the port isn't reachable.

Design: all interaction goes through `Runtime.evaluate`, injecting small JS snippets to
find/mark/click/read elements by CSS selector or visible text, rather than CDP's native
`Input.dispatchMouseEvent` + `DOM.getBoxModel` coordinate math. This is simpler, doesn't
need the browser window focused or even on-screen, and is exactly how a bookmarklet or
browser extension already interacts with a page — no keyboard-layout or pixel-coordinate
issues (this project hit real ydotool/keyboard-layout bugs typing symbols via the pixel
path; JS `el.value = ...` sidesteps that class of bug entirely for browser content).

Matching is two-step and stateful within one page load: `find_elements` marks each match
with a `data-cdp-mark` attribute and returns that mark alongside its text/role/rect, then
`click_marked`/`type_marked` re-locate the element by that exact mark. This avoids the
classic "CSS selector matched a different element the second time" bug.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from websockets.sync.client import connect as ws_connect

DEFAULT_PORT = 9222

_DEFAULT_INTERACTIVE_SELECTOR = (
    "a, button, input, select, textarea, [role], [onclick], [tabindex]"
)


class CdpError(RuntimeError):
    pass


def _setup_hint(port: int) -> str:
    return (
        f"no browser debugging port open on localhost:{port}. Relaunch the browser with "
        f"--remote-debugging-port={port} (one-time — this project never restarts your "
        f"browser for you, since that would close your open tabs)."
    )


def list_targets(port: int = DEFAULT_PORT) -> list[dict]:
    """List open browser tabs/targets (read-only)."""
    try:
        with urllib.request.urlopen(f"http://localhost:{port}/json/list", timeout=3) as resp:
            return json.load(resp)
    except (urllib.error.URLError, OSError) as e:
        raise CdpError(_setup_hint(port)) from e


def _target_ws_url(port: int, target_index: int) -> str:
    targets = [t for t in list_targets(port) if t.get("type") == "page"]
    if not targets:
        raise CdpError(f"no open page targets on localhost:{port}")
    if target_index >= len(targets):
        raise CdpError(f"only {len(targets)} page target(s) open, index {target_index} out of range")
    return targets[target_index]["webSocketDebuggerUrl"]


def _rpc(ws_url: str, method: str, params: dict | None = None, timeout: float = 10) -> dict:
    with ws_connect(ws_url, open_timeout=timeout, close_timeout=2) as ws:
        ws.send(json.dumps({"id": 1, "method": method, "params": params or {}}))
        while True:
            msg = json.loads(ws.recv(timeout=timeout))
            if msg.get("id") == 1:
                if "error" in msg:
                    raise CdpError(f"{method}: {msg['error']}")
                return msg.get("result", {})


def evaluate(expression: str, target_index: int = 0, port: int = DEFAULT_PORT):
    """Run arbitrary JS in a tab and return the resulting value (must be JSON-serializable)."""
    ws_url = _target_ws_url(port, target_index)
    result = _rpc(
        ws_url,
        "Runtime.evaluate",
        {"expression": expression, "returnByValue": True, "awaitPromise": True},
        timeout=15,
    )
    if result.get("exceptionDetails"):
        raise CdpError(f"JS error: {result['exceptionDetails']}")
    return result.get("result", {}).get("value")


def targets(port: int = DEFAULT_PORT) -> list[dict]:
    """Read-only: title/url of every open page tab, in the index order other functions use."""
    return [
        {"title": t.get("title"), "url": t.get("url")}
        for t in list_targets(port)
        if t.get("type") == "page"
    ]


def find_elements(
    selector: str | None = None,
    text_contains: str | None = None,
    target_index: int = 0,
    port: int = DEFAULT_PORT,
    limit: int = 50,
) -> list[dict]:
    """Find visible elements matching a CSS selector and/or visible-text substring.

    Each match is tagged with a `mark` id (see module docstring) — pass it to
    `click_marked`/`type_marked` to act on that exact element.
    """
    sel_js = json.dumps(selector) if selector else json.dumps(_DEFAULT_INTERACTIVE_SELECTOR)
    text_js = json.dumps(text_contains.lower()) if text_contains else "null"
    limit_js = json.dumps(limit)
    js = f"""
(() => {{
  const nodes = Array.from(document.querySelectorAll({sel_js}));
  const textFilter = {text_js};
  const limit = {limit_js};
  const results = [];
  let n = 0;
  for (const el of nodes) {{
    if (results.length >= limit) break;
    const rect = el.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) continue;
    const text = (el.innerText || el.value || el.getAttribute('aria-label') || el.placeholder || '').trim();
    if (textFilter && !text.toLowerCase().includes(textFilter)) continue;
    const mark = 'cdp-mark-' + Date.now() + '-' + (n++);
    el.setAttribute('data-cdp-mark', mark);
    results.push({{
      mark,
      tag: el.tagName.toLowerCase(),
      role: el.getAttribute('role') || null,
      text: text.slice(0, 200),
      rect: {{
        x: Math.round(rect.x), y: Math.round(rect.y),
        width: Math.round(rect.width), height: Math.round(rect.height),
      }},
    }});
  }}
  return results;
}})()
"""
    return evaluate(js, target_index=target_index, port=port)


def click_marked(mark: str, target_index: int = 0, port: int = DEFAULT_PORT) -> dict:
    """Click the element previously tagged with `mark` by `find_elements`."""
    js = f"""
(() => {{
  const el = document.querySelector('[data-cdp-mark="{mark}"]');
  if (!el) return {{ok: false, error: 'element not found (page may have re-rendered)'}};
  el.scrollIntoView({{block: 'center', inline: 'center'}});
  el.click();
  return {{ok: true}};
}})()
"""
    return evaluate(js, target_index=target_index, port=port)


def type_marked(mark: str, text: str, target_index: int = 0, port: int = DEFAULT_PORT) -> dict:
    """Set the value of an <input>/<textarea> previously tagged with `mark`, dispatching the
    input/change events the page's own JS listens for (works even when the field can't
    receive real keyboard focus, and sidesteps keyboard-layout bugs entirely)."""
    text_js = json.dumps(text)
    js = f"""
(() => {{
  const el = document.querySelector('[data-cdp-mark="{mark}"]');
  if (!el) return {{ok: false, error: 'element not found (page may have re-rendered)'}};
  el.focus();
  el.value = {text_js};
  el.dispatchEvent(new Event('input', {{bubbles: true}}));
  el.dispatchEvent(new Event('change', {{bubbles: true}}));
  return {{ok: true}};
}})()
"""
    return evaluate(js, target_index=target_index, port=port)


def get_text(selector: str = "body", target_index: int = 0, port: int = DEFAULT_PORT) -> str:
    """Read the visible text of an element (default: the whole page)."""
    js = f"""
(() => {{
  const el = document.querySelector({json.dumps(selector)});
  return el ? el.innerText : null;
}})()
"""
    return evaluate(js, target_index=target_index, port=port)


def navigate(url: str, target_index: int = 0, port: int = DEFAULT_PORT) -> None:
    """Navigate a tab to a URL via CDP's Page domain (no address-bar typing needed)."""
    ws_url = _target_ws_url(port, target_index)
    _rpc(ws_url, "Page.navigate", {"url": url})
