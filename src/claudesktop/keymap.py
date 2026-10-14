"""Reverse keymap: character -> (evdev keycode, modifier keycodes), for the layout
the compositor applies to ydotool's virtual keyboard.

Why: `ydotool type` assumes a US layout when turning characters into keycodes, but
Hyprland interprets those keycodes with whatever layout the device has (here
`br`/ABNT2), so `/ : ;` and friends came out wrong. Forcing `kb_layout=us` on the
virtual device was tried and reverted (2026-09-17) — the fix must not touch the
user's layout. Instead we read the layout Hyprland already applies to
`ydotoold-virtual-device` and compile that same keymap with libxkbcommon, so the
lookup is right for any layout on any machine, not a hardcoded ABNT2 table.

libxkbcommon is called via ctypes against the system .so (always present under
Hyprland) — same reasoning as AT-SPI via jeepney: no compiled extension tied to
the venv's Python ABI.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import functools
import unicodedata

from . import hyprctl

VIRTUAL_DEVICE = "ydotoold-virtual-device"

# xkb keycodes are evdev keycodes + 8 (XKB_EVDEV_OFFSET in the X11/evdev rules).
_EVDEV_OFFSET = 8

# Modifier name (as libxkbcommon reports it) -> evdev keycode we press to get it.
# Mod5 is where ISO_Level3_Shift (AltGr) lands in the stock xkeyboard-config rules.
_MOD_KEYS = {"Shift": 42, "Mod5": 100, "LevelThree": 100}
# When a char is reachable several ways, Shift is preferred over AltGr (works
# the same on every layout that has the char at level 2).
_MOD_COST = {42: 1, 100: 2}
# Keypad keys (KEY_KP*) are a last resort: games and terminals often bind them
# separately from the main block, so `*` should come from Shift+8, not KP_Multiply.
_KEYPAD = {55, *range(71, 84), 96, 98, 117, 118, 121, 179, 180}
_KEYPAD_COST = 10

# Dead keysym name -> (combining mark it applies, spacing char it yields + space).
_DEAD_KEYS = {
    "dead_grave": ("̀", "`"),
    "dead_acute": ("́", "´"),
    "dead_circumflex": ("̂", "^"),
    "dead_tilde": ("̃", "~"),
    "dead_diaeresis": ("̈", "¨"),
    "dead_cedilla": ("̧", "¸"),
}

# Control characters that map to keysyms with no printable UTF-32 value.
_CONTROL_KEYSYMS = {"\n": "Return", "\t": "Tab"}

Stroke = tuple[int, tuple[int, ...]]  # (evdev keycode, modifier evdev keycodes)


class KeymapError(RuntimeError):
    pass


class _RuleNames(ctypes.Structure):
    _fields_ = [(name, ctypes.c_char_p) for name in ("rules", "model", "layout", "variant", "options")]


@functools.cache
def _lib() -> ctypes.CDLL:
    path = ctypes.util.find_library("xkbcommon")
    if not path:
        raise KeymapError("libxkbcommon not found")
    lib = ctypes.CDLL(path)
    lib.xkb_context_new.restype = ctypes.c_void_p
    lib.xkb_context_new.argtypes = [ctypes.c_int]
    lib.xkb_context_unref.argtypes = [ctypes.c_void_p]
    lib.xkb_keymap_new_from_names.restype = ctypes.c_void_p
    lib.xkb_keymap_new_from_names.argtypes = [ctypes.c_void_p, ctypes.POINTER(_RuleNames), ctypes.c_int]
    lib.xkb_keymap_unref.argtypes = [ctypes.c_void_p]
    lib.xkb_keymap_min_keycode.restype = ctypes.c_uint32
    lib.xkb_keymap_min_keycode.argtypes = [ctypes.c_void_p]
    lib.xkb_keymap_max_keycode.restype = ctypes.c_uint32
    lib.xkb_keymap_max_keycode.argtypes = [ctypes.c_void_p]
    lib.xkb_keymap_num_levels_for_key.restype = ctypes.c_uint32
    lib.xkb_keymap_num_levels_for_key.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32]
    lib.xkb_keymap_key_get_syms_by_level.restype = ctypes.c_int
    lib.xkb_keymap_key_get_syms_by_level.argtypes = [
        ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32,
        ctypes.POINTER(ctypes.POINTER(ctypes.c_uint32)),
    ]
    lib.xkb_keymap_key_get_mods_for_level.restype = ctypes.c_size_t
    lib.xkb_keymap_key_get_mods_for_level.argtypes = [
        ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_uint32), ctypes.c_size_t,
    ]
    lib.xkb_keymap_num_mods.restype = ctypes.c_uint32
    lib.xkb_keymap_num_mods.argtypes = [ctypes.c_void_p]
    lib.xkb_keymap_mod_get_name.restype = ctypes.c_char_p
    lib.xkb_keymap_mod_get_name.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    lib.xkb_keysym_to_utf32.restype = ctypes.c_uint32
    lib.xkb_keysym_to_utf32.argtypes = [ctypes.c_uint32]
    lib.xkb_keysym_get_name.restype = ctypes.c_int
    lib.xkb_keysym_get_name.argtypes = [ctypes.c_uint32, ctypes.c_char_p, ctypes.c_size_t]
    return lib


def active_layout() -> tuple[str, str]:
    """(layout, variant) Hyprland applies to ydotool's virtual keyboard."""
    keyboards = hyprctl.keyboards()
    device = next((k for k in keyboards if k.get("name") == VIRTUAL_DEVICE), None)
    if device is None:
        raise KeymapError(f"{VIRTUAL_DEVICE} not found in hyprctl devices (is ydotoold running?)")
    return device.get("layout") or "us", device.get("variant") or ""


def _mods_for_mask(lib: ctypes.CDLL, keymap: int, mask: int) -> tuple[int, ...] | None:
    keys = []
    for idx in range(lib.xkb_keymap_num_mods(keymap)):
        if not mask & (1 << idx):
            continue
        name = lib.xkb_keymap_mod_get_name(keymap, idx).decode()
        if name not in _MOD_KEYS:
            return None  # needs a modifier we don't press (e.g. Lock) — skip this mask
        if _MOD_KEYS[name] not in keys:
            keys.append(_MOD_KEYS[name])
    return tuple(keys)


def _cost(stroke: Stroke) -> int:
    code, mods = stroke
    return sum(_MOD_COST[m] for m in mods) + (_KEYPAD_COST if code in _KEYPAD else 0)


@functools.cache
def _build(layout: str, variant: str) -> tuple[dict[str, Stroke], dict[str, Stroke]]:
    """Returns (direct chars, dead keys keyed by dead keysym name)."""
    lib = _lib()
    ctx = lib.xkb_context_new(0)
    if not ctx:
        raise KeymapError("xkb_context_new failed")
    names = _RuleNames(None, None, layout.encode(), variant.encode() or None, None)
    keymap = lib.xkb_keymap_new_from_names(ctx, ctypes.byref(names), 0)
    if not keymap:
        lib.xkb_context_unref(ctx)
        raise KeymapError(f"could not compile keymap for layout={layout!r} variant={variant!r}")

    direct: dict[str, Stroke] = {}
    dead: dict[str, Stroke] = {}
    syms = ctypes.POINTER(ctypes.c_uint32)()
    masks = (ctypes.c_uint32 * 16)()
    name_buf = ctypes.create_string_buffer(64)
    try:
        for code in range(lib.xkb_keymap_min_keycode(keymap), lib.xkb_keymap_max_keycode(keymap) + 1):
            evdev = code - _EVDEV_OFFSET
            # Above 255 are media/special keys (e.g. KEY_NUMERIC_POUND) that some
            # layouts bind to printable symbols; apps don't treat them as typing.
            if not 0 < evdev <= 255:
                continue
            for level in range(lib.xkb_keymap_num_levels_for_key(keymap, code, 0)):
                if lib.xkb_keymap_key_get_syms_by_level(keymap, code, 0, level, ctypes.byref(syms)) != 1:
                    continue
                sym = syms[0]
                n_masks = lib.xkb_keymap_key_get_mods_for_level(keymap, code, 0, level, masks, len(masks))
                options = [m for m in (_mods_for_mask(lib, keymap, masks[i]) for i in range(n_masks)) if m is not None]
                if not options:
                    continue
                stroke = (evdev, min(options, key=lambda m: _cost((evdev, m))))

                lib.xkb_keysym_get_name(sym, name_buf, len(name_buf))
                sym_name = name_buf.value.decode()
                if sym_name in _DEAD_KEYS:
                    dead.setdefault(sym_name, stroke)
                    continue
                for char, control_name in _CONTROL_KEYSYMS.items():
                    if sym_name == control_name:
                        direct.setdefault(char, stroke)
                cp = lib.xkb_keysym_to_utf32(sym)
                if cp >= 0x20 and cp != 0x7F:
                    char = chr(cp)
                    # Keep the cheapest way to reach each char (keypad duplicates etc.).
                    if char not in direct or _cost(stroke) < _cost(direct[char]):
                        direct[char] = stroke
    finally:
        lib.xkb_keymap_unref(keymap)
        lib.xkb_context_unref(ctx)
    return direct, dead


Segment = tuple[str, "list[Stroke] | str"]  # ("keys", strokes) or ("paste", text)


def segments(text: str, layout: str | None = None, variant: str = "") -> list[Segment]:
    """Split `text` into runs: ("keys", strokes) for what the active layout can type, and
    ("paste", text) for runs it can't (emoji, CJK...). Consecutive untypeable code points
    stay in one run, so multi-code-point emoji (ZWJ sequences, skin tones) aren't split."""
    if layout is None:
        layout, variant = active_layout()
    direct, dead = _build(layout, variant)
    by_mark = {mark: dead[name] for name, (mark, _) in _DEAD_KEYS.items() if name in dead}
    by_spacing = {spacing: dead[name] for name, (_, spacing) in _DEAD_KEYS.items() if name in dead}

    out: list[Segment] = []

    def add(kind: str, item) -> None:
        if out and out[-1][0] == kind:
            out[-1] = (kind, out[-1][1] + item)
        else:
            out.append((kind, item))

    for char in text:
        if char in direct:
            add("keys", [direct[char]])
            continue
        if char in by_spacing and " " in direct:
            add("keys", [by_spacing[char], direct[" "]])  # dead key + space -> the bare accent
            continue
        decomposed = unicodedata.normalize("NFD", char)
        base, marks = decomposed[0], decomposed[1:]
        if len(marks) == 1 and base in direct and marks in by_mark:
            add("keys", [by_mark[marks], direct[base]])
            continue
        add("paste", char)
    return out
