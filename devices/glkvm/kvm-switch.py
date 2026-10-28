#!/usr/bin/env python3
"""Switch an eight-port hotkey KVM through the GL.iNet KVM USB gadget."""

import sys
import time

from hid_input import HidKeyboard, run


DEVICE = "/dev/hidg0"
LOCK_FILE = "/run/kvmd-kvm-switch.lock"
KEY_HOLD_SECONDS = 0.075
INTER_KEY_SECONDS = 0.250


def tap(keyboard: HidKeyboard, modifier: int = 0, key: int = 0) -> None:
    keyboard.write_report(modifier, key)
    keyboard.pause(KEY_HOLD_SECONDS)
    keyboard.write_report()


def main() -> int:
    requested_at = time.monotonic()
    if len(sys.argv) != 2:
        raise SystemExit(f"usage: {sys.argv[0]} CHANNEL")

    channel = int(sys.argv[1])
    if not 1 <= channel <= 8:
        raise SystemExit("CHANNEL must be between 1 and 8")

    with HidKeyboard(requested_at, DEVICE, LOCK_FILE) as keyboard:
        keyboard.write_report()
        tap(keyboard, modifier=0x01)  # Left Control
        keyboard.pause(INTER_KEY_SECONDS)
        tap(keyboard, modifier=0x01)  # Left Control
        keyboard.pause(INTER_KEY_SECONDS)
        tap(keyboard, key=0x1D + channel)  # Digit1 through Digit8

    return 0


if __name__ == "__main__":
    raise SystemExit(run(main))
