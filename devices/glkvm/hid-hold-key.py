#!/usr/bin/env python3
"""Reassert one HID key through POST for a bounded number of seconds.

Usage: hid-hold-key.py KEYHEX [SECONDS]
Examples: Esc=0x29, F9=0x42, F10=0x43, F12=0x45. SECONDS defaults to 25.
"""

import fcntl
import os
import sys
import time


DEVICE = "/dev/hidg0"
LOCK_FILE = "/run/kvmd-kvm-switch.lock"


def main() -> int:
    if len(sys.argv) not in (2, 3):
        raise SystemExit(f"usage: {sys.argv[0]} KEYHEX [SECONDS]")

    key = int(sys.argv[1], 16)
    hold = float(sys.argv[2]) if len(sys.argv) == 3 else 25
    if not 0 <= key <= 0xFF:
        raise SystemExit("KEYHEX must be between 0x00 and 0xff")
    if hold <= 0:
        raise SystemExit("SECONDS must be greater than zero")

    with open(LOCK_FILE, "w", encoding="ascii") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        fd = os.open(DEVICE, os.O_WRONLY)
        try:
            end = time.time() + hold
            while time.time() < end:
                os.write(fd, bytes([0, 0, key, 0, 0, 0, 0, 0]))
                time.sleep(0.4)
                os.write(fd, bytes([0] * 8))
                time.sleep(0.1)
        finally:
            os.write(fd, bytes([0] * 8))
            os.close(fd)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
