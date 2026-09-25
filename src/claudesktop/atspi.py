"""AT-SPI accessibility-tree wrapper, talking raw D-Bus via jeepney.

Why not PyGObject (`gi.repository.Atspi`)? It's already installed and working
against the system Python (3.14), but this project's uv-managed venv pins
Python 3.13 — PyGObject's compiled `_gi` extension is built for one exact
Python ABI, so it can't be imported across that version gap, and
`--system-site-packages` can't bridge it. jeepney is pure Python (no compiled
extension), so it works regardless of which Python build uv picks.

AT-SPI runs on its own private D-Bus, not the regular session bus. Its address
is looked up once per call via `org.a11y.Bus.GetAddress` on the session bus
(confirmed empirically 2026-09-06: `unix:path=/run/user/<uid>/at-spi/bus_0`).

Every accessible object is addressed as an (bus_name, object_path) pair — each
one lives inside whichever application process owns it, not inside a
registry-owned namespace. `root()` is the one fixed exception: it's owned by
the `org.a11y.atspi.Registry` service and aggregates every registered app as
its children.

Coverage is real but partial: only apps whose toolkit implements AT-SPI show
up in `list_apps()`. Confirmed present: GTK/Qt apps (waybar, swaync, thunar,
xdg-desktop-portal-gtk). Confirmed absent: Chromium/Vivaldi (use the `cdp`
module's DOM automation for anything inside a Chromium-based browser instead)
and GPU-rendered terminals like Alacritty (no widget tree to expose).
"""

from __future__ import annotations

from jeepney import DBusAddress, MessageType, Properties, new_method_call
from jeepney.io.blocking import DBusConnection, open_dbus_connection

REGISTRY_BUS = "org.a11y.atspi.Registry"
ROOT_PATH = "/org/a11y/atspi/accessible/root"
IFACE_ACCESSIBLE = "org.a11y.atspi.Accessible"
IFACE_COMPONENT = "org.a11y.atspi.Component"
IFACE_ACTION = "org.a11y.atspi.Action"

COORD_SCREEN = 0  # Atspi.CoordType.SCREEN — the other option, WINDOW (1), is relative.

# AT-SPI returns this INT32_MIN sentinel for extents of unmapped/off-screen widgets
# (e.g. buttons sitting in a closed overflow menu) — confirmed empirically 2026-09-06
# against Thunar's hidden toolbar buttons.
_UNMAPPED = -2147483648

Ref = tuple[str, str]  # (bus_name, object_path)


class AtspiError(RuntimeError):
    pass


def _send(conn: DBusConnection, msg, description: str) -> tuple:
    reply = conn.send_and_get_reply(msg, timeout=5)
    if reply.header.message_type == MessageType.error:
        raise AtspiError(f"{description}: {reply.body[0]!r}")
    return reply.body


def _call(conn: DBusConnection, addr: DBusAddress, member: str, signature: str = "", body: tuple = ()):
    msg = new_method_call(addr, member, signature or None, body)
    return _send(conn, msg, f"{addr.bus_name} {addr.object_path} {member}")


def _addr(ref: Ref, interface: str) -> DBusAddress:
    bus_name, path = ref
    return DBusAddress(path, bus_name=bus_name, interface=interface)


def _bus_address() -> str:
    conn = open_dbus_connection(bus="SESSION")
    try:
        addr = DBusAddress("/org/a11y/bus", bus_name="org.a11y.Bus", interface="org.a11y.Bus")
        body = _call(conn, addr, "GetAddress")
        return body[0]
    except Exception as e:
        raise AtspiError(
            "couldn't reach the AT-SPI bus via org.a11y.Bus.GetAddress "
            "(is at-spi2-core running?)"
        ) from e
    finally:
        conn.close()


def connect() -> DBusConnection:
    return open_dbus_connection(bus=_bus_address())


def root() -> Ref:
    return (REGISTRY_BUS, ROOT_PATH)


def get_name(conn: DBusConnection, ref: Ref) -> str:
    accessible = _addr(ref, IFACE_ACCESSIBLE)
    msg = Properties(accessible).get("Name")
    body = _send(conn, msg, f"{accessible.bus_name} {accessible.object_path} Name")
    return body[0][1]


def get_role_name(conn: DBusConnection, ref: Ref) -> str:
    return _call(conn, _addr(ref, IFACE_ACCESSIBLE), "GetRoleName")[0]


def get_interfaces(conn: DBusConnection, ref: Ref) -> list[str]:
    return _call(conn, _addr(ref, IFACE_ACCESSIBLE), "GetInterfaces")[0]


def get_children(conn: DBusConnection, ref: Ref) -> list[Ref]:
    return [(bn, p) for bn, p in _call(conn, _addr(ref, IFACE_ACCESSIBLE), "GetChildren")[0]]


def get_extents(conn: DBusConnection, ref: Ref, interfaces: list[str] | None = None) -> tuple[int, int, int, int] | None:
    """Screen-space (x, y, width, height), or None if this object has no Component interface."""
    ifaces = interfaces if interfaces is not None else get_interfaces(conn, ref)
    if IFACE_COMPONENT not in ifaces:
        return None
    return _call(conn, _addr(ref, IFACE_COMPONENT), "GetExtents", "u", (COORD_SCREEN,))[0]


def get_actions(conn: DBusConnection, ref: Ref, interfaces: list[str] | None = None) -> list[tuple[str, str, str]]:
    ifaces = interfaces if interfaces is not None else get_interfaces(conn, ref)
    if IFACE_ACTION not in ifaces:
        return []
    return [tuple(a) for a in _call(conn, _addr(ref, IFACE_ACTION), "GetActions")[0]]


def do_action(conn: DBusConnection, ref: Ref, index: int) -> bool:
    return _call(conn, _addr(ref, IFACE_ACTION), "DoAction", "i", (index,))[0]


def _find_app(conn: DBusConnection, app_name: str) -> Ref:
    apps = get_children(conn, root())
    match = next((a for a in apps if app_name.lower() in get_name(conn, a).lower()), None)
    if match is None:
        names = [get_name(conn, a) for a in apps]
        raise AtspiError(f"no accessible app matching {app_name!r}; currently registered: {names}")
    return match


def _node_summary(conn: DBusConnection, ref: Ref) -> dict:
    ifaces = get_interfaces(conn, ref)
    node: dict = {"name": get_name(conn, ref), "role": get_role_name(conn, ref), "ref": list(ref)}
    extents = get_extents(conn, ref, ifaces)
    if extents is not None:
        x, y, w, h = extents
        if w > 0 and h > 0 and x != _UNMAPPED and y != _UNMAPPED:
            node["extents"] = {"x": x, "y": y, "width": w, "height": h, "center": [x + w // 2, y + h // 2]}
    actions = get_actions(conn, ref, ifaces)
    if actions:
        node["actions"] = [a[0] for a in actions]
    return node


def list_apps() -> list[dict]:
    """Read-only: every application currently registered with AT-SPI, by name."""
    conn = connect()
    try:
        return [{"name": get_name(conn, a), "ref": list(a)} for a in get_children(conn, root())]
    finally:
        conn.close()


def tree(app_name: str, max_depth: int = 6, max_children: int = 40) -> dict:
    """Dump the accessible tree of a running app, matched by substring of its AT-SPI name.

    Bounded by max_depth/max_children — accessible trees can be large; raise these only
    if a needed element isn't showing up.
    """
    conn = connect()
    try:
        app_ref = _find_app(conn, app_name)

        def walk(ref: Ref, depth: int) -> dict:
            node = _node_summary(conn, ref)
            if depth < max_depth:
                children = get_children(conn, ref)[:max_children]
                if children:
                    node["children"] = [walk(c, depth + 1) for c in children]
            return node

        return walk(app_ref, 0)
    finally:
        conn.close()


def find(
    app_name: str,
    *,
    name: str | None = None,
    name_contains: str | None = None,
    role: str | None = None,
    max_depth: int = 20,
    max_children: int = 100,
) -> list[dict]:
    """Search a running app's accessible tree for nodes matching name/role.

    Returns a flat list of matches with screen extents (when available), so a caller can
    click each match's `extents.center` directly via the existing `click` tool.
    """
    if name is None and name_contains is None and role is None:
        raise ValueError("give at least one of name, name_contains, role")

    conn = connect()
    try:
        app_ref = _find_app(conn, app_name)
        matches: list[dict] = []

        def visit(ref: Ref, depth: int) -> None:
            if depth > max_depth:
                return
            node_name = get_name(conn, ref)
            node_role = get_role_name(conn, ref)
            hit = (
                (name is None or node_name == name)
                and (name_contains is None or name_contains.lower() in node_name.lower())
                and (role is None or node_role == role)
            )
            if hit:
                matches.append(_node_summary(conn, ref))
            for child in get_children(conn, ref)[:max_children]:
                visit(child, depth + 1)

        visit(app_ref, 0)
        return matches
    finally:
        conn.close()
