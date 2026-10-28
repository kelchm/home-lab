"""Bounded userspace access to the shared GLKVM keyboard gadget (stdlib only).

O_NONBLOCK avoids f_hidg_write's wait for a pending request. POLLOUT only
indicates a free request slot, including after a USB completion error; neither
write nor poll acknowledges host delivery. Closing cannot retract queued input.
"""

import fcntl
import math
import os
import select
import sys
import time


START_TIMEOUT = 1.0
REPORT_TIMEOUT = 1.0
IO_BUDGET = 2.0
# Successful records earn modest bus/processing time for long text/POST work.
# Retries and failed records never replenish the budget.
REPORT_ALLOWANCE = 0.050
CLEANUP_TIMEOUT = 0.250
RETRY_INTERVAL = 0.010
NEUTRAL = bytes(8)


class HidError(RuntimeError):
    """Input stopped; any already submitted report has an uncertain outcome."""


class HidKeyboard:
    """One locked sequence with absolute deadlines and no reconnect/replay.

    The start deadline includes lock acquisition, open, and the first report.
    The total deadline starts at request entry and gains only deliberate pause
    durations and REPORT_ALLOWANCE per completed slot, never a fresh timeout for
    each retry. Each report also has its own shorter deadline. Cleanup can only
    submit a neutral report.
    """

    def __init__(self, requested_at, device="/dev/hidg0",
                 lock_file="/run/kvmd-kvm-switch.lock"):
        self.device = device
        self.lock_file = lock_file
        self.start_deadline = requested_at + START_TIMEOUT
        self.deadline = requested_at + IO_BUDGET
        self.fd = None
        self.lock_fd = None
        self.poller = None
        self.started = False
        self.failed = False

    @staticmethod
    def remaining(deadline):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise HidError("input deadline expired; sequence stopped")
        return remaining

    def __enter__(self):
        try:
            self.remaining(self.start_deadline)
            # Never truncate or unlink the lock inode: other helpers share it.
            self.lock_fd = os.open(self.lock_file, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o600)
            while True:
                self.remaining(self.start_deadline)
                try:
                    fcntl.flock(self.lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    time.sleep(min(RETRY_INTERVAL, self.remaining(self.start_deadline)))
            self.remaining(self.start_deadline)
            self.fd = os.open(self.device, os.O_WRONLY | os.O_NONBLOCK | os.O_CLOEXEC)
            self.remaining(self.start_deadline)
            self.poller = select.poll()
            self.poller.register(self.fd, select.POLLOUT)
            return self
        except BaseException:
            self._close()
            raise

    def _ready(self, deadline):
        while True:
            timeout_ms = math.ceil(self.remaining(deadline) * 1000)
            try:
                events = self.poller.poll(timeout_ms)
            except InterruptedError:
                continue
            # A late wakeup never licenses input after expiry.
            self.remaining(deadline)
            for fd, flags in events:
                if fd != self.fd:
                    continue
                if flags & (select.POLLERR | select.POLLHUP | select.POLLNVAL):
                    raise HidError("HID device unavailable; sequence stopped")
                if flags & select.POLLOUT:
                    return

    def _write(self, report, deadline):
        while True:
            self._ready(deadline)
            self.remaining(deadline)
            try:
                count = os.write(self.fd, report)
            except InterruptedError:
                continue
            except BlockingIOError:
                # Readiness can race another writer (kvmd does not take our lock).
                time.sleep(min(RETRY_INTERVAL, self.remaining(deadline)))
                continue
            if count != len(report):
                # HID reports are records, not a byte stream. Never send a tail.
                raise HidError(f"short HID report ({count}/{len(report)}); sequence stopped")
            # Wait for the single pending slot to become free, not a host ACK.
            self._ready(deadline)
            return

    def write_report(self, modifier=0, key=0):
        if self.failed or self.fd is None:
            raise HidError("input sequence is stopped; start a new verified request")
        try:
            deadline = min(self.deadline, time.monotonic() + REPORT_TIMEOUT)
            if not self.started:
                deadline = min(deadline, self.start_deadline)
            self._write(bytes([modifier, 0, key, 0, 0, 0, 0, 0]), deadline)
            self.started = True
            self.deadline += REPORT_ALLOWANCE
        except BaseException:
            self.failed = True
            raise

    def pause(self, seconds):
        if not math.isfinite(seconds) or seconds < 0:
            raise ValueError("pause must be finite and nonnegative")
        if self.failed or self.fd is None:
            raise HidError("input sequence is stopped")
        try:
            self.remaining(self.deadline)
            time.sleep(seconds)
            self.deadline += seconds
            self.remaining(self.deadline)
        except BaseException:
            self.failed = True
            raise

    def _close(self):
        errors = []
        for attr in ("fd", "lock_fd"):
            fd = getattr(self, attr)
            setattr(self, attr, None)
            if fd is not None:
                try:
                    os.close(fd)
                except OSError as error:
                    errors.append(str(error))
        return errors

    def __exit__(self, exc_type, exc_value, traceback):
        errors = []
        try:
            if self.fd is not None:
                self._write(NEUTRAL, time.monotonic() + CLEANUP_TIMEOUT)
        except (OSError, HidError) as error:
            errors.append(f"neutral release unconfirmed: {error}")
        finally:
            self.failed = True
            errors.extend(self._close())
        if errors:
            detail = "; ".join(errors)
            if exc_type is None:
                raise HidError(detail)
            # Preserve the original error, but do not conceal a failed release.
            print(f"HID cleanup: {detail}", file=sys.stderr)
        return False


def run(main):
    """Keep operational failures concise; context cleanup runs before this."""
    try:
        return main()
    except (HidError, OSError, ValueError) as error:
        print(f"{os.path.basename(sys.argv[0])}: {error}; verify host/input before retrying", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Input interrupted; verify host/input before retrying", file=sys.stderr)
        return 130
