"""Virtual Xbox 360 controller via /dev/uinput (python-evdev), for games that don't
play well with keyboard/mouse.

It presents the same identity and button/axis codes as the kernel's xpad driver for
a real 360 pad (045e:028e), so SDL/Steam pick their stock mapping for it. Note xpad's
naming: the X button is BTN_X (== BTN_NORTH) and Y is BTN_Y (== BTN_WEST) — we emit
exactly those, and SDL's 360 mapping interprets them correctly.

The device lives as long as the MCP server process (some games only detect
controllers present at launch), until gamepad_disconnect. Uses the same /dev/uinput
access ydotool already needs (input group + udev rule from the README).
"""

from __future__ import annotations

import time

from evdev import AbsInfo, UInput, ecodes as e

BUTTONS = {
    "a": e.BTN_A, "b": e.BTN_B, "x": e.BTN_X, "y": e.BTN_Y,
    "lb": e.BTN_TL, "rb": e.BTN_TR,
    "back": e.BTN_SELECT, "start": e.BTN_START, "guide": e.BTN_MODE,
    "ls": e.BTN_THUMBL, "rs": e.BTN_THUMBR,
}
DPAD = {"up": (e.ABS_HAT0Y, -1), "down": (e.ABS_HAT0Y, 1), "left": (e.ABS_HAT0X, -1), "right": (e.ABS_HAT0X, 1)}
STICKS = {"left": (e.ABS_X, e.ABS_Y), "right": (e.ABS_RX, e.ABS_RY)}
TRIGGERS = {"left": e.ABS_Z, "right": e.ABS_RZ}

_STICK = AbsInfo(value=0, min=-32768, max=32767, fuzz=16, flat=128, resolution=0)
_TRIGGER = AbsInfo(value=0, min=0, max=255, fuzz=0, flat=0, resolution=0)
_HAT = AbsInfo(value=0, min=-1, max=1, fuzz=0, flat=0, resolution=0)

_device: UInput | None = None


class GamepadError(RuntimeError):
    pass


def connect() -> dict:
    global _device
    if _device is None:
        caps = {
            e.EV_KEY: list(BUTTONS.values()),
            e.EV_ABS: [(a, _STICK) for a in (e.ABS_X, e.ABS_Y, e.ABS_RX, e.ABS_RY)]
            + [(a, _TRIGGER) for a in (e.ABS_Z, e.ABS_RZ)]
            + [(a, _HAT) for a in (e.ABS_HAT0X, e.ABS_HAT0Y)],
        }
        try:
            _device = UInput(
                caps, name="Microsoft X-Box 360 pad", vendor=0x045E, product=0x028E, version=0x110, bustype=e.BUS_USB
            )
        except OSError as err:
            raise GamepadError(f"could not create virtual gamepad on /dev/uinput: {err}") from err
        time.sleep(0.3)  # let udev/SDL see the new device before the first event
    return {"connected": True, "device": _device.device.path if _device.device else None}


def disconnect() -> dict:
    global _device
    if _device is not None:
        _device.close()
        _device = None
    return {"connected": False}


def _dev() -> UInput:
    if _device is None:
        raise GamepadError("no virtual gamepad — call gamepad_connect first")
    return _device


def _emit(events: list[tuple[int, int, int]]) -> None:
    dev = _dev()
    for etype, code, value in events:
        dev.write(etype, code, value)
    dev.syn()


def press(button: str, hold_ms: int = 100) -> None:
    button = button.lower()
    if button in DPAD:
        axis, value = DPAD[button]
        _emit([(e.EV_ABS, axis, value)])
        time.sleep(hold_ms / 1000)
        _emit([(e.EV_ABS, axis, 0)])
        return
    if button not in BUTTONS:
        raise ValueError(f"unknown button {button!r}, expected one of {(*BUTTONS, *DPAD)}")
    _emit([(e.EV_KEY, BUTTONS[button], 1)])
    time.sleep(hold_ms / 1000)
    _emit([(e.EV_KEY, BUTTONS[button], 0)])


def stick(which: str, x: float, y: float, hold_ms: int = 300) -> None:
    """x/y in [-1, 1], y positive = up. Returns to center after hold_ms."""
    if which not in STICKS:
        raise ValueError(f"unknown stick {which!r}, expected 'left' or 'right'")
    if not (-1 <= x <= 1 and -1 <= y <= 1):
        raise ValueError("x and y must be within [-1, 1]")
    ax, ay = STICKS[which]
    # Linux/xpad convention: Y axis grows downward.
    _emit([(e.EV_ABS, ax, round(x * 32767)), (e.EV_ABS, ay, round(-y * 32767))])
    time.sleep(hold_ms / 1000)
    _emit([(e.EV_ABS, ax, 0), (e.EV_ABS, ay, 0)])


def trigger(side: str, value: float = 1.0, hold_ms: int = 200) -> None:
    """value in [0, 1]. Releases after hold_ms."""
    if side not in TRIGGERS:
        raise ValueError(f"unknown trigger {side!r}, expected 'left' or 'right'")
    if not 0 <= value <= 1:
        raise ValueError("value must be within [0, 1]")
    _emit([(e.EV_ABS, TRIGGERS[side], round(value * 255))])
    time.sleep(hold_ms / 1000)
    _emit([(e.EV_ABS, TRIGGERS[side], 0)])
