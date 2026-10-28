#!/usr/bin/env python3
"""Send paced keystrokes through the GLKVM USB HID keyboard gadget.

Usage: hid-type.py TOKEN [TOKEN...]
  Tokens: key names (enter, esc, tab, up, down, left, right, f1..f12,
  space, home, end, pgup, pgdn, del, ins, bksp), ``type:LITERAL`` to type
  text, and ``sleep:SECONDS`` to pause.
"""

import sys
import time

from hid_input import HidKeyboard, run


DEVICE = "/dev/hidg0"
LOCK_FILE = "/run/kvmd-kvm-switch.lock"
KEY_HOLD = 0.075
INTER_KEY = 0.15

NAMED = {
    "enter": 0x28,
    "esc": 0x29,
    "bksp": 0x2A,
    "tab": 0x2B,
    "space": 0x2C,
    "capslock": 0x39,
    "right": 0x4F,
    "left": 0x50,
    "down": 0x51,
    "up": 0x52,
    "ins": 0x49,
    "home": 0x4A,
    "pgup": 0x4B,
    "del": 0x4C,
    "end": 0x4D,
    "pgdn": 0x4E,
    "pause": 0x48,
}
for i in range(1, 13):
    NAMED[f"f{i}"] = 0x3A + i - 1

PLAIN = {}
for i, char in enumerate("abcdefghijklmnopqrstuvwxyz"):
    PLAIN[char] = (0, 0x04 + i)
for i, char in enumerate("1234567890"):
    PLAIN[char] = (0, 0x1E + i)
PLAIN.update(
    {
        " ": (0, 0x2C),
        "-": (0, 0x2D),
        "=": (0, 0x2E),
        "[": (0, 0x2F),
        "]": (0, 0x30),
        "\\": (0, 0x31),
        ";": (0, 0x33),
        "'": (0, 0x34),
        "`": (0, 0x35),
        ",": (0, 0x36),
        ".": (0, 0x37),
        "/": (0, 0x38),
    }
)
SHIFTED = {}
for lower, upper in zip("abcdefghijklmnopqrstuvwxyz", "ABCDEFGHIJKLMNOPQRSTUVWXYZ"):
    SHIFTED[upper] = PLAIN[lower]
for base, symbol in zip("1234567890", "!@#$%^&*()"):
    SHIFTED[symbol] = PLAIN[base]
for base, symbol in [
    ("-", "_"),
    ("=", "+"),
    ("[", "{"),
    ("]", "}"),
    ("\\", "|"),
    (";", ":"),
    ("'", '"'),
    ("`", "~"),
    (",", "<"),
    (".", ">"),
    ("/", "?"),
]:
    SHIFTED[symbol] = PLAIN[base]


def tap(keyboard: HidKeyboard, modifier: int = 0, key: int = 0) -> None:
    keyboard.write_report(modifier, key)
    keyboard.pause(KEY_HOLD)
    keyboard.write_report()
    keyboard.pause(INTER_KEY)


def main() -> int:
    requested_at = time.monotonic()
    tokens = sys.argv[1:]
    if not tokens:
        raise SystemExit("usage: hid-type.py TOKEN [TOKEN...]")

    with HidKeyboard(requested_at, DEVICE, LOCK_FILE) as keyboard:
        keyboard.write_report()
        for token in tokens:
            if token.startswith("sleep:"):
                keyboard.pause(float(token[6:]))
            elif token.startswith("type:"):
                for char in token[5:]:
                    if char in PLAIN:
                        tap(keyboard, 0, PLAIN[char][1])
                    elif char in SHIFTED:
                        tap(keyboard, 0x02, SHIFTED[char][1])
                    else:
                        raise SystemExit(f"untypable char: {char!r}")
            elif token.lower() in NAMED:
                tap(keyboard, 0, NAMED[token.lower()])
            elif token.lower().startswith("ctrl-alt-"):
                rest = token.lower()[9:]
                if rest in NAMED:
                    key = NAMED[rest]
                elif rest in PLAIN:
                    key = PLAIN[rest][1]
                else:
                    raise SystemExit(f"unknown Ctrl-Alt key: {rest}")
                tap(keyboard, 0x05, key)
            else:
                raise SystemExit(f"unknown token: {token}")

    return 0


if __name__ == "__main__":
    raise SystemExit(run(main))
