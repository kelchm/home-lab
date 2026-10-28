"""Offline fault injection. No test may open a real HID/USB device.

Run: python3 -m unittest discover -s devices/glkvm -p 'test_hid_input.py' -v
"""

import contextlib
import errno
import fcntl
import importlib.util
import io
import os
from pathlib import Path
import select
import subprocess
import sys
import tempfile
import textwrap
import types
import unittest
from unittest.mock import patch

import hid_input


ROOT = Path(__file__).parent


def load_helper(filename):
    spec = importlib.util.spec_from_file_location(filename.replace("-", "_"), ROOT / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SWITCH = load_helper("kvm-switch.py")
TYPE = load_helper("hid-type.py")
HOLD = load_helper("hid-hold-key.py")


class Clock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []
        self.oversleep = 0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds + self.oversleep


class Driver:
    """One asynchronous request slot, as in f_hidg_write/poll/release.

    A queued report survives close. Readiness after completion says nothing
    about USB status or host action. Availability restoration is test-controlled.
    """

    def __init__(self, clock):
        self.clock = clock
        self.opened = []
        self.closed = []
        self.active = set()
        self.writes = []
        self.attempts = []
        self.delivered = []
        self.pending = None
        self.complete_at = None
        self.delay = 0
        self.available = True
        self.unlock_at = 0
        self.stall_on = None
        self.effects = []
        self.events = None
        self.close_error = False
        self.flock_late = False
        self.open_late = False
        self.open_error = None
        self.completion_error = False

    def open(self, path, flags, mode=None):
        # Fail the test rather than fall through to an actual device.
        assert path in ("/offline/keyboard", "/offline/lock"), path
        self.opened.append((path, flags))
        if path.endswith("lock"):
            assert not flags & os.O_TRUNC
            self.active.add(10)
            return 10
        assert flags & os.O_NONBLOCK
        if self.open_error:
            raise self.open_error
        if self.open_late:
            self.clock.now += hid_input.START_TIMEOUT
        self.active.add(11)
        return 11

    def flock(self, fd, flags):
        assert fd == 10 and flags == fcntl.LOCK_EX | fcntl.LOCK_NB
        if self.clock.now < self.unlock_at:
            raise BlockingIOError(errno.EAGAIN, "lock busy")
        if self.flock_late:
            self.clock.now += hid_input.START_TIMEOUT

    def register(self, fd, flags):
        assert fd == 11 and flags == select.POLLOUT

    def complete(self):
        if self.pending is not None:
            if not self.completion_error:
                self.delivered.append(self.pending)
            self.pending = None
            self.complete_at = None

    def restore(self):
        self.available = True
        self.complete()

    def poll(self, milliseconds):
        if self.events is not None:
            return [(11, self.events)]
        until = self.clock.now + milliseconds / 1000
        if self.available and self.pending is not None and self.complete_at is not None:
            if self.complete_at <= until:
                self.clock.now = max(self.clock.now, self.complete_at)
                self.complete()
        if self.available and self.pending is None:
            return [(11, select.POLLOUT)]
        self.clock.now = until
        return []

    def write(self, fd, report):
        assert fd == 11 and fd in self.active
        assert len(report) == 8
        self.attempts.append(report)
        if self.effects:
            effect = self.effects.pop(0)
            if isinstance(effect, BaseException):
                raise effect
            if effect is not None:
                if effect > 0:
                    self.submit(report[:effect])
                return effect
        self.submit(report)
        return 8

    def submit(self, report):
        assert self.pending is None
        self.writes.append((self.clock.now, report))
        self.pending = report
        self.complete_at = self.clock.now + self.delay
        if len(self.writes) == self.stall_on:
            self.available = False
            self.complete_at = None

    def close(self, fd):
        self.closed.append(fd)
        self.active.remove(fd)
        # Closing deliberately DOES NOT erase pending accepted reports.
        if self.close_error:
            raise OSError(errno.EIO, "close error")


class Fixture(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.driver = Driver(self.clock)
        fake_os = types.SimpleNamespace(
            open=self.driver.open, write=self.driver.write, close=self.driver.close,
            O_RDWR=os.O_RDWR, O_CREAT=os.O_CREAT, O_CLOEXEC=os.O_CLOEXEC,
            O_WRONLY=os.O_WRONLY, O_NONBLOCK=os.O_NONBLOCK, path=os.path,
        )
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(hid_input, "os", fake_os))
        self.stack.enter_context(patch.object(hid_input, "time", self.clock))
        self.stack.enter_context(patch.object(hid_input, "fcntl", types.SimpleNamespace(
            flock=self.driver.flock, LOCK_EX=fcntl.LOCK_EX, LOCK_NB=fcntl.LOCK_NB)))
        self.stack.enter_context(patch.object(hid_input, "select", types.SimpleNamespace(
            poll=lambda: self.driver, POLLOUT=select.POLLOUT, POLLERR=select.POLLERR,
            POLLHUP=select.POLLHUP, POLLNVAL=select.POLLNVAL)))
        self.stderr = io.StringIO()
        self.stack.enter_context(contextlib.redirect_stderr(self.stderr))

    def keyboard(self, requested_at=0):
        return hid_input.HidKeyboard(requested_at, "/offline/keyboard", "/offline/lock")

    def helper(self, module, args):
        with patch.object(module, "DEVICE", "/offline/keyboard"), \
                patch.object(module, "LOCK_FILE", "/offline/lock"), \
                patch.object(module, "time", self.clock), \
                patch.object(sys, "argv", [module.__file__, *args]):
            return hid_input.run(module.main)

    def reports(self):
        return [report for _, report in self.driver.writes]

    def test_expired_lock_never_opens_device_or_replays(self):
        self.driver.unlock_at = 5
        self.assertEqual(self.helper(SWITCH, ["2"]), 1)
        self.assertLessEqual(self.clock.now, hid_input.START_TIMEOUT + .001)
        self.assertEqual([p for p, _ in self.driver.opened], ["/offline/lock"])
        self.clock.now = 10
        self.driver.restore()
        self.assertEqual(self.reports(), [])
        self.assertEqual(self.driver.closed, [10])

    def test_lock_can_be_acquired_before_start_deadline(self):
        self.driver.unlock_at = .2
        self.assertEqual(self.helper(SWITCH, ["2"]), 0)
        self.assertGreaterEqual(self.driver.writes[0][0], .2)

    def test_late_lock_or_open_does_not_submit_input(self):
        for late in ("flock_late", "open_late"):
            with self.subTest(late=late):
                setattr(self.driver, late, True)
                with self.assertRaises(hid_input.HidError):
                    with self.keyboard():
                        self.fail("late start accepted")
                self.assertEqual(self.reports(), [])
                setattr(self.driver, late, False)
                self.clock.now = 0

    def test_start_time_is_request_entry_not_lock_success(self):
        self.clock.now = hid_input.START_TIMEOUT
        with self.assertRaises(hid_input.HidError):
            with self.keyboard():
                self.fail("expired request entered")
        self.assertEqual(self.driver.opened, [])

    def test_unavailable_device_has_bounded_neutral_cleanup(self):
        self.driver.available = False
        self.assertEqual(self.helper(SWITCH, ["2"]), 1)
        self.assertLessEqual(self.clock.now, hid_input.START_TIMEOUT + hid_input.CLEANUP_TIMEOUT + .002)
        self.assertEqual(self.reports(), [])
        self.assertEqual(self.driver.closed, [11, 10])
        self.driver.restore()
        self.assertEqual(self.reports(), [])

    def test_device_open_error_releases_lock_without_input(self):
        self.driver.open_error = FileNotFoundError(errno.ENOENT, "offline device absent")
        self.assertEqual(self.helper(SWITCH, ["2"]), 1)
        self.assertEqual(self.driver.closed, [10])
        self.assertEqual(self.reports(), [])

    def test_long_declared_work_does_not_extend_start_failure_for_any_helper(self):
        self.driver.available = False
        for helper, args in ((SWITCH, ["2"]), (TYPE, ["sleep:60", "type:a"]),
                             (HOLD, ["29", "25"])):
            with self.subTest(helper=helper.__file__):
                start = self.clock.now
                self.assertEqual(self.helper(helper, args), 1)
                self.assertLessEqual(self.clock.now - start, 1.252)
                self.assertEqual(self.reports(), [])

    def test_slot_completion_is_not_a_host_delivery_ack(self):
        self.driver.completion_error = True
        self.assertEqual(self.helper(SWITCH, ["2"]), 0)
        self.assertEqual(len(self.reports()), 8)
        self.assertEqual(self.driver.delivered, [])

    def test_accepted_pending_report_survives_close_but_sequence_stops(self):
        self.driver.stall_on = 2  # First Ctrl accepted after initial neutral.
        self.assertEqual(self.helper(SWITCH, ["2"]), 1)
        self.assertEqual(len(self.reports()), 2)
        pending = bytes([1, 0, 0, 0, 0, 0, 0, 0])
        self.assertEqual(self.driver.pending, pending)
        self.assertEqual(self.driver.closed, [11, 10])
        self.assertLessEqual(self.clock.now, 1.252)
        self.clock.now = 10
        self.driver.restore()
        self.assertEqual(self.driver.delivered[-1], pending)
        self.assertEqual(len(self.reports()), 2)  # No second Ctrl or digit later.
        self.assertIn("neutral release unconfirmed", self.stderr.getvalue())

    def test_write_eagain_and_eintr_retry_only_before_deadline(self):
        self.driver.effects = [BlockingIOError(errno.EAGAIN, "race"), InterruptedError(), None]
        self.assertEqual(self.helper(SWITCH, ["2"]), 0)
        self.assertEqual(len(self.reports()), 8)

    def test_eagain_forever_expires_and_cannot_restart_same_sequence(self):
        self.driver.effects = [BlockingIOError(errno.EAGAIN, "busy")] * 200
        keyboard = self.keyboard()
        with self.assertRaises(hid_input.HidError):
            with keyboard:
                keyboard.write_report(key=4)
        self.driver.effects.clear()
        self.driver.restore()
        with self.assertRaises(hid_input.HidError):
            keyboard.write_report(key=5)
        self.assertFalse(any(any(r) for r in self.reports()))
        self.assertLessEqual(self.clock.now, 1.252)

    def test_short_write_is_not_retried_as_a_tail(self):
        for count in (0, 3, 7):
            with self.subTest(count=count):
                self.driver.effects = [count]
                self.assertEqual(self.helper(SWITCH, ["2"]), 1)
                self.assertTrue(all(not any(r) for r in self.reports()))
                self.assertTrue(all(len(r) == 8 for r in self.driver.attempts))
                self.assertIn("short HID report", self.stderr.getvalue())

    def test_short_non_neutral_submission_is_ambiguous_not_retried(self):
        self.driver.effects = [None, 3]
        self.assertEqual(self.helper(SWITCH, ["2"]), 1)
        self.assertEqual(self.reports(), [bytes(8), bytes([1, 0, 0]), bytes(8)])
        self.assertEqual(len(self.driver.attempts), 3)
        self.assertTrue(all(len(r) == 8 for r in self.driver.attempts))

    def test_device_error_does_not_skip_close_or_hide_original(self):
        self.driver.effects = [OSError(errno.ESHUTDOWN, "original device failure"),
                               OSError(errno.EIO, "cleanup failure")]
        self.assertEqual(self.helper(SWITCH, ["2"]), 1)
        self.assertEqual(self.driver.closed, [11, 10])
        self.assertIn("original device failure", self.stderr.getvalue())
        self.assertIn("cleanup failure", self.stderr.getvalue())

    def test_interrupted_sequence_attempts_neutral_release_and_exits_nonzero(self):
        self.driver.effects = [None, KeyboardInterrupt()]
        self.assertEqual(self.helper(SWITCH, ["2"]), 130)
        self.assertEqual(self.reports(), [bytes(8), bytes(8)])
        self.assertEqual(self.driver.closed, [11, 10])

    def test_interrupt_storm_cannot_extend_poll_deadline(self):
        def interrupted_poll(milliseconds):
            self.clock.now += .1
            raise InterruptedError()

        self.driver.poll = interrupted_poll
        self.assertEqual(self.helper(SWITCH, ["2"]), 1)
        self.assertLess(self.clock.now, 1.5)
        self.assertEqual(self.reports(), [])

    def test_poll_error_hup_and_invalid_are_failures(self):
        for flag in (select.POLLERR, select.POLLHUP, select.POLLNVAL):
            with self.subTest(flag=flag):
                self.driver.events = flag | select.POLLOUT
                self.assertEqual(self.helper(SWITCH, ["2"]), 1)
                self.assertEqual(self.reports(), [])

    def test_poll_eintr_and_late_readiness_recheck_deadline(self):
        normal_poll = self.driver.poll
        calls = 0

        def poll(timeout):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise InterruptedError()
            self.clock.now += timeout / 1000
            return [(11, select.POLLOUT)]

        self.driver.poll = poll
        self.assertEqual(self.helper(SWITCH, ["2"]), 1)
        self.assertEqual(self.reports(), [])
        self.driver.poll = normal_poll

    def test_normal_final_release_failure_is_nonzero(self):
        self.driver.effects = [None] * 7 + [OSError(errno.EIO, "final release")]
        self.assertEqual(self.helper(SWITCH, ["2"]), 1)
        self.assertIn("neutral release unconfirmed", self.stderr.getvalue())

    def test_close_errors_do_not_leave_lock_fd_unhandled(self):
        self.driver.close_error = True
        self.assertEqual(self.helper(SWITCH, ["2"]), 1)
        self.assertEqual(self.driver.closed, [11, 10])

    def test_switch_report_order_and_pacing(self):
        self.assertEqual(self.helper(SWITCH, ["2"]), 0)
        neutral = bytes(8)
        ctrl = bytes([1, 0, 0, 0, 0, 0, 0, 0])
        digit = bytes([0, 0, 0x1f, 0, 0, 0, 0, 0])
        self.assertEqual(self.reports(), [neutral, ctrl, neutral, ctrl, neutral, digit, neutral, neutral])
        self.assertEqual(self.clock.sleeps, [.075, .25, .075, .25, .075])
        self.assertAlmostEqual(self.clock.now, .725)

    def test_typing_modifiers_named_keys_and_deliberate_sleep(self):
        self.assertEqual(self.helper(TYPE, ["type:aA!", "enter", "ctrl-alt-del", "sleep:20"]), 0)
        nonzero = [(r[0], r[2]) for r in self.reports() if any(r)]
        self.assertEqual(nonzero, [(0, 4), (2, 4), (2, 0x1e), (0, 0x28), (5, 0x4c)])
        self.assertEqual(self.clock.sleeps, [.075, .15] * 5 + [20.0])
        self.assertAlmostEqual(self.clock.now, 21.125)
        self.assertEqual(self.reports()[-1], bytes(8))

    def test_long_typing_keeps_normal_bus_time_without_per_retry_budget(self):
        self.driver.delay = .020
        self.assertEqual(self.helper(TYPE, ["type:" + "a" * 1000]), 0)
        self.assertEqual(len(self.reports()), 2002)
        self.assertAlmostEqual(self.clock.now, 265.04)

    def test_cumulative_budget_prevents_one_second_per_report(self):
        self.driver.delay = .45
        self.assertEqual(self.helper(TYPE, ["type:" + "a" * 20]), 1)
        # Deliberate pauses plus successful-record allowance, not 42 fresh seconds.
        self.assertLess(self.clock.now, 4)
        self.assertLess(len(self.reports()), 10)

    def test_monotonic_hold_preserves_cycles_and_release(self):
        self.assertEqual(self.helper(HOLD, ["0x29", "25"]), 0)
        self.assertEqual(self.clock.sleeps, [.4, .1] * 50)
        self.assertEqual(len(self.reports()), 101)
        self.assertEqual(self.reports()[-1], bytes(8))
        self.assertAlmostEqual(self.clock.now, 25)

    def test_fractional_hold_with_bus_delay_finishes_current_release_cycle(self):
        self.driver.delay = .010
        self.assertEqual(self.helper(HOLD, ["29", ".6"]), 0)
        self.assertEqual(self.clock.sleeps, [.4, .1] * 2)
        self.assertEqual(self.reports()[-1], bytes(8))
        self.assertGreaterEqual(self.clock.now, .6)
        self.assertLess(self.clock.now, 1.2)

    def test_finite_hold_and_sleep_validation(self):
        for value in ("nan", "inf", "-1", "0"):
            with self.subTest(hold=value), self.assertRaises(SystemExit):
                self.helper(HOLD, ["29", value])
        self.assertEqual(self.driver.opened, [])
        self.assertEqual(self.helper(TYPE, ["sleep:inf"]), 1)
        self.assertTrue(all(r == bytes(8) for r in self.reports()))

    def test_scheduler_oversleep_expires_before_next_non_neutral_report(self):
        self.driver.delay = 0
        self.clock.oversleep = 3
        self.assertEqual(self.helper(SWITCH, ["2"]), 1)
        self.assertEqual(len([r for r in self.reports() if any(r)]), 1)
        self.assertEqual(self.reports()[-1], bytes(8))

    def test_invalid_token_still_releases_keys(self):
        with self.assertRaises(SystemExit):
            self.helper(TYPE, ["type:a", "invalid-token"])
        self.assertEqual(self.reports()[-1], bytes(8))


class KernelPrimitives(unittest.TestCase):
    """Real disposable primitives, supervised so regressions cannot hang CI."""

    def run_offline(self, program, *args):
        bootstrap = "import sys; sys.path.insert(0, sys.argv[1]); import hid_input\n"
        subprocess.run(
            [sys.executable, "-c", bootstrap + textwrap.dedent(program), str(ROOT), *args],
            check=True, capture_output=True, text=True, timeout=3,
        )

    def test_real_flock_contention_expires(self):
        with tempfile.TemporaryDirectory() as d:
            lock = str(Path(d) / "lock")
            fd = os.open(lock, os.O_RDWR | os.O_CREAT, 0o600)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                self.run_offline("""
                    import time
                    hid_input.START_TIMEOUT = .05
                    start = time.monotonic()
                    try:
                        with hid_input.HidKeyboard(start, sys.argv[3], sys.argv[2]):
                            raise AssertionError("contended lock accepted")
                    except hid_input.HidError:
                        pass
                    assert time.monotonic() - start < 1
                """, lock, str(Path(d) / "never-created-device"))
                self.assertFalse((Path(d) / "never-created-device").exists())
            finally:
                os.close(fd)

    def test_real_full_nonblocking_pipe_times_out_and_closes(self):
        self.run_offline("""
            import os, select, time
            reader, writer = os.pipe()
            os.set_blocking(writer, False)
            try:
                while True:
                    try:
                        os.write(writer, bytes(4096))
                    except BlockingIOError:
                        break
                hid_input.START_TIMEOUT = .05
                hid_input.CLEANUP_TIMEOUT = .03
                start = time.monotonic()
                keyboard = hid_input.HidKeyboard(start, "unused-offline-path", "unused-offline-lock")
                keyboard.fd = writer
                keyboard.poller = select.poll()
                keyboard.poller.register(writer, select.POLLOUT)
                try:
                    try:
                        keyboard.write_report(key=4)
                        raise AssertionError("full pipe accepted input")
                    except hid_input.HidError:
                        pass
                finally:
                    keyboard.__exit__(RuntimeError, RuntimeError("test failure"), None)
                assert time.monotonic() - start < 1
                try:
                    os.fstat(writer)
                    raise AssertionError("writer left open")
                except OSError:
                    pass
            finally:
                os.close(reader)
        """)


if __name__ == "__main__":
    unittest.main()
