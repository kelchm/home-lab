#!/usr/bin/env python3
"""Reassert one HID key through POST for a bounded number of seconds.

Usage: hid-hold-key.py KEYHEX [SECONDS]
Examples: Esc=0x29, F9=0x42, F10=0x43, F12=0x45. SECONDS defaults to 25.
"""

import math
import sys
import time

from hid_input import HidKeyboard, run


DEVICE = "/dev/hidg0"
LOCK_FILE = "/run/kvmd-kvm-switch.lock"


def main() -> int:
    requested_at = time.monotonic()
    if len(sys.argv) not in (2, 3):
        raise SystemExit(f"usage: {sys.argv[0]} KEYHEX [SECONDS]")

    key = int(sys.argv[1], 16)
    hold = float(sys.argv[2]) if len(sys.argv) == 3 else 25
    if not 0 <= key <= 0xFF:
        raise SystemExit("KEYHEX must be between 0x00 and 0xff")
    if not math.isfinite(hold) or hold <= 0:
        raise SystemExit("SECONDS must be finite and greater than zero")

    with HidKeyboard(requested_at, DEVICE, LOCK_FILE) as keyboard:
        end = time.monotonic() + hold
        while time.monotonic() < end:
            keyboard.write_report(key=key)
            keyboard.pause(0.4)
            keyboard.write_report()
            keyboard.pause(0.1)

    return 0


if __name__ == "__main__":
    raise SystemExit(run(main))
