"""Where lines come from: a serial port, a pipe, a log file, or a simulator.

Every source runs its blocking read on a background thread and hands complete
lines to the UI through a queue. Doing it this way keeps one hard problem out
of the render loop: non-blocking reads are genuinely different on Windows and
POSIX, for serial ports and for stdin alike, and a scope that stutters while
waiting on a quiet UART is not a scope.

The UI never blocks. It drains whatever has arrived and draws a frame.
"""

from __future__ import annotations

import contextlib
import math
import os
import queue
import random
import sys
import threading
import time
from abc import ABC, abstractmethod

__all__ = [
    "DemoSource",
    "FileSource",
    "SerialSource",
    "Source",
    "SourceError",
    "StdinSource",
    "list_ports",
]


class SourceError(RuntimeError):
    """A source could not be opened or died mid-stream."""


class _SerialConfigurationError(SourceError):
    """A permanent serial configuration error that retrying cannot repair."""


class Source(ABC):
    """Base class: a background thread producing text lines."""

    #: Shown in the status bar.
    description = "source"

    def __init__(self) -> None:
        self._queue: queue.SimpleQueue[str] = queue.SimpleQueue()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._error: BaseException | None = None
        #: Set once the producer has no more data (EOF on a file or pipe).
        self.finished = False

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run_guarded, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            # Daemon threads are usually parked in a blocking read; a short
            # join is enough to catch the common case without hanging exit.
            thread.join(timeout=0.5)
        self.close()

    def close(self) -> None:  # noqa: B027 -- optional hook, not every source holds a handle
        """Release any OS handle. Overridden where there is one."""

    def _run_guarded(self) -> None:
        try:
            self._run()
        except BaseException as exc:  # surfaced to the UI, not swallowed
            self._error = exc
        finally:
            self.finished = True

    @abstractmethod
    def _run(self) -> None:
        """Produce lines via :meth:`_emit` until :attr:`_stop` is set."""

    # -- plumbing ----------------------------------------------------------

    def _emit(self, line: str) -> None:
        self._queue.put(line)

    def drain(self, limit: int = 4096) -> list[str]:
        """Take up to ``limit`` buffered lines without blocking.

        The cap matters: a firmware dumping at 500 kbaud can produce lines
        faster than any terminal can draw them, and an uncapped drain would
        let the UI fall permanently behind its own input.
        """
        out = []
        for _ in range(limit):
            try:
                out.append(self._queue.get_nowait())
            except queue.Empty:
                break
        return out

    def check_error(self) -> BaseException | None:
        return self._error

    def write(self, data: str) -> bool:
        """Send data back to the device. False if the source is read-only."""
        return False

    @property
    def writable(self) -> bool:
        return False


class SerialSource(Source):
    """Read newline-delimited text from a serial port.

    ``pyserial`` is imported lazily so that demo mode, pipes and log replay
    all work in an environment where it was never installed.
    """

    def __init__(
        self,
        port: str,
        baudrate: int = 115200,
        *,
        timeout: float = 0.2,
        reconnect_interval: float | None = None,
    ) -> None:
        super().__init__()
        if reconnect_interval is not None and (
            not math.isfinite(reconnect_interval) or reconnect_interval <= 0
        ):
            raise ValueError("reconnect_interval must be finite and greater than zero")
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self.reconnect_interval = reconnect_interval
        self.reconnect_count = 0
        self.last_disconnect: str | None = None
        self._serial = None
        self._write_lock = threading.Lock()
        self._connected_description = f"{port} @ {baudrate}"
        self.description = self._connected_description

    def _serial_module(self):
        try:
            import serial
        except ImportError as exc:
            raise SourceError(
                "Reading a serial port needs pyserial.\n"
                "  pip install 'termscope[serial]'   (or: pip install pyserial)\n"
                "No hardware to hand? Try:  termscope --demo"
            ) from exc
        return serial

    def _open(self, serial):
        try:
            return serial.Serial(self.port, self.baudrate, timeout=self.timeout)
        except (TypeError, ValueError) as exc:
            raise _SerialConfigurationError(
                f"invalid serial settings for {self.port}: {exc}"
            ) from exc
        except Exception as exc:  # pyserial raises several unrelated types
            raise SourceError(f"could not open {self.port}: {exc}") from exc

    def _run(self) -> None:
        # Import once, outside the retry loop. Missing pyserial is a setup
        # error; waiting forever cannot make the package appear.
        serial = self._serial_module()
        while not self._stop.is_set():
            try:
                self._serial = self._open(serial)
                self.description = self._connected_description
                self._read_connection()
                return
            except _SerialConfigurationError:
                raise
            except SourceError as exc:
                self.close()
                if self.reconnect_interval is None or self._stop.is_set():
                    raise
                self.reconnect_count += 1
                self.last_disconnect = str(exc)
                self.description = f"{self._connected_description} (reconnecting)"
                if self._stop.wait(self.reconnect_interval):
                    return

    def _read_connection(self) -> None:
        pending = bytearray()
        while not self._stop.is_set():
            try:
                waiting = self._serial.in_waiting or 1
                chunk = self._serial.read(waiting)
            except Exception as exc:
                raise SourceError(f"{self.port}: {exc}") from exc
            if not chunk:
                continue
            pending.extend(chunk)
            # Split on \n and tolerate \r\n; a partial trailing line stays in
            # the buffer until its terminator arrives. A disconnect discards
            # an unterminated fragment so two device sessions cannot be joined.
            while True:
                nl = pending.find(b"\n")
                if nl < 0:
                    break
                raw = bytes(pending[:nl])
                del pending[: nl + 1]
                self._emit(raw.decode("utf-8", errors="replace").rstrip("\r"))
            if len(pending) > 1 << 16:
                # No newline in 64 KiB means this is not line-oriented text.
                # Drop it rather than grow forever.
                pending.clear()

    def close(self) -> None:
        ser, self._serial = self._serial, None
        if ser is not None:
            with contextlib.suppress(Exception):
                ser.close()

    @property
    def writable(self) -> bool:
        return True

    def write(self, data: str) -> bool:
        ser = self._serial
        if ser is None:
            return False
        with self._write_lock:
            try:
                ser.write(data.encode("utf-8", errors="replace"))
                return True
            except Exception:
                return False


class StdinSource(Source):
    """Read lines piped in from another process.

    Lets termscope compose with anything that already prints numbers::

        pio device monitor | termscope -
    """

    description = "stdin"

    def _run(self) -> None:
        stream = sys.stdin
        if stream is None:
            raise SourceError("stdin is not available")
        for line in stream:
            if self._stop.is_set():
                break
            self._emit(line.rstrip("\r\n"))


class FileSource(Source):
    """Replay a captured log.

    With ``rate`` set the file is fed in at that many lines per second so a
    recording plays back like the live capture it came from; with ``rate=0``
    the whole file is loaded at once.
    """

    def __init__(self, path: str, *, rate: float = 0.0) -> None:
        super().__init__()
        self.path = path
        self.rate = rate
        self.description = f"{os.path.basename(path)} (replay)"

    def _run(self) -> None:
        try:
            handle = open(self.path, encoding="utf-8", errors="replace")  # noqa: SIM115
        except OSError as exc:
            raise SourceError(f"could not read {self.path}: {exc}") from exc
        delay = 1.0 / self.rate if self.rate > 0 else 0.0
        with handle:
            for line in handle:
                if self._stop.is_set():
                    break
                self._emit(line.rstrip("\r\n"))
                if delay:
                    time.sleep(delay)


class DemoSource(Source):
    """Synthesise the telemetry of a self-balancing robot.

    This exists so the tool is useful the instant it is installed, with no
    board wired up -- which is also what makes it testable in CI. The signals
    are modelled on a real two-wheel balancer: an inclinometer fighting a
    disturbance, the motor command that results, and a battery that sags
    under load.
    """

    description = "demo (simulated balancing robot)"

    def __init__(self, *, rate: float = 60.0, seed: int | None = 7) -> None:
        super().__init__()
        self.rate = max(1.0, rate)
        self._rng = random.Random(seed)
        self._pitch = 0.0
        self._pitch_rate = 0.0
        self._battery = 12.4

    def _step(self, t: float) -> dict[str, float]:
        rng = self._rng
        dt = 1.0 / self.rate

        # A periodic shove, plus noise, plus a PD controller pushing back.
        disturbance = 2.2 * math.sin(t * 0.9) + 0.9 * math.sin(t * 3.7 + 1.1)
        disturbance += rng.gauss(0.0, 0.35)
        control = -(4.5 * self._pitch + 0.65 * self._pitch_rate)

        self._pitch_rate += (disturbance + control) * dt
        self._pitch_rate *= 0.985  # damping
        self._pitch += self._pitch_rate * dt * 12.0

        motor = max(-100.0, min(100.0, control * 9.0))
        # Battery sags with motor effort and drifts down over the session.
        self._battery -= 0.000025 * abs(motor) * dt + 0.0000015
        self._battery = max(9.6, self._battery)
        sag = self._battery - abs(motor) * 0.0035

        return {
            "pitch": round(self._pitch, 3),
            "motor": round(motor, 2),
            # ~3 mV of sense noise, about what a filtered ADC on a battery
            # divider actually shows -- enough to look real, not enough to
            # bury the sag under the motor load.
            "vbat": round(sag + rng.gauss(0.0, 0.003), 3),
        }

    def _run(self) -> None:
        period = 1.0 / self.rate
        start = time.monotonic()
        next_tick = start
        n = 0
        while not self._stop.is_set():
            now = time.monotonic()
            if now < next_tick:
                # Sleep the remainder, but wake often enough to notice a stop.
                time.sleep(min(next_tick - now, 0.05))
                continue
            vals = self._step(now - start)
            self._emit("pitch:{pitch} motor:{motor} vbat:{vbat}".format(**vals))
            n += 1
            next_tick = start + n * period
            if next_tick < now:
                # Fell behind (slow machine, or the process was suspended);
                # resync rather than sprinting to catch up.
                next_tick = now + period
                n = 0
                start = now


def list_ports() -> list[tuple[str, str]]:
    """Enumerate serial ports as ``(device, human description)``.

    Returns an empty list when pyserial is missing, so callers can print a
    helpful message instead of a traceback.
    """
    try:
        from serial.tools import list_ports as _lp
    except ImportError:
        return []
    out = []
    for info in _lp.comports():
        label = info.description or ""
        if info.manufacturer and info.manufacturer not in label:
            label = f"{label} ({info.manufacturer})".strip()
        out.append((info.device, label or "unknown device"))
    return sorted(out)


def autodetect_port() -> str | None:
    """Best guess at the board the user means.

    Ports whose description mentions a known USB-serial bridge win, because on
    a typical laptop the alternatives are Bluetooth modems and motherboard
    headers that no one wants to open.
    """
    ports = list_ports()
    if not ports:
        return None
    preferred = (
        "usb",
        "acm",
        "ch340",
        "ch910",
        "cp210",
        "ftdi",
        "ft232",
        "silicon labs",
        "wch",
        "arduino",
        "st-link",
        "stlink",
        "jlink",
    )
    for device, label in ports:
        haystack = f"{device} {label}".lower()
        if any(key in haystack for key in preferred):
            return device
    return ports[0][0]
