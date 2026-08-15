#!/usr/bin/env python3
"""Switch an eight-port hotkey KVM through the GL.iNet KVM USB gadget."""

import fcntl
import os
import sys
import time


DEVICE = "/dev/hidg0"
LOCK_FILE = "/run/kvmd-kvm-switch.lock"
KEY_HOLD_SECONDS = 0.075
INTER_KEY_SECONDS = 0.250


def write_report(fd: int, modifier: int = 0, key: int = 0) -> None:
    report = bytes([modifier, 0, key, 0, 0, 0, 0, 0])
    if os.write(fd, report) != len(report):
        raise OSError("short write to HID gadget")


def tap(fd: int, modifier: int = 0, key: int = 0) -> None:
    write_report(fd, modifier, key)
    time.sleep(KEY_HOLD_SECONDS)
    write_report(fd)


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit(f"usage: {sys.argv[0]} CHANNEL")

    channel = int(sys.argv[1])
    if not 1 <= channel <= 8:
        raise SystemExit("CHANNEL must be between 1 and 8")

    # Prevent rapid UI clicks from interleaving two HID sequences.
    with open(LOCK_FILE, "w", encoding="ascii") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        fd = os.open(DEVICE, os.O_WRONLY)
        try:
            write_report(fd)
            tap(fd, modifier=0x01)  # Left Control
            time.sleep(INTER_KEY_SECONDS)
            tap(fd, modifier=0x01)  # Left Control
            time.sleep(INTER_KEY_SECONDS)
            tap(fd, key=0x1D + channel)  # Digit1 through Digit8
        finally:
            write_report(fd)
            os.close(fd)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
