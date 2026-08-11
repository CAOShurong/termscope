"""Source tests.

Sources are threaded, so these tests poll with a deadline rather than sleeping
a fixed amount -- a fixed sleep is what makes a suite pass on a laptop and
fail on a loaded CI runner.
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
import types
import unittest
from unittest.mock import patch

from termscope.parser import StreamParser
from termscope.sources import DemoSource, FileSource, Source, SourceError


def collect(source: Source, minimum: int = 1, timeout: float = 5.0) -> list[str]:
    """Drain until ``minimum`` lines have arrived or the deadline passes."""
    source.start()
    lines: list[str] = []
    deadline = time.monotonic() + timeout
    try:
        while len(lines) < minimum and time.monotonic() < deadline:
            lines.extend(source.drain())
            if source.finished:
                lines.extend(source.drain())
                break
            time.sleep(0.01)
    finally:
        source.stop()
    return lines


class TestDemoSource(unittest.TestCase):
    def test_produces_parseable_lines(self):
        lines = collect(DemoSource(rate=200), minimum=5)
        self.assertGreaterEqual(len(lines), 5)
        parser = StreamParser()
        parsed = [parser.feed(line) for line in lines]
        self.assertTrue(all(p.values for p in parsed))

    def test_emits_the_documented_channels(self):
        lines = collect(DemoSource(rate=200), minimum=3)
        parser = StreamParser()
        for line in lines:
            parser.feed(line)
        self.assertEqual(sorted(parser.channels), ["motor", "pitch", "vbat"])

    def test_is_deterministic_for_a_fixed_seed(self):
        # Reproducibility is what lets the demo be used as a test fixture.
        a = DemoSource(rate=500, seed=42)
        b = DemoSource(rate=500, seed=42)
        self.assertEqual(
            [a._step(i / 10) for i in range(20)],
            [b._step(i / 10) for i in range(20)],
        )

    def test_signals_stay_in_a_plausible_range(self):
        src = DemoSource(rate=100, seed=1)
        for i in range(2000):
            vals = src._step(i / 100)
            self.assertLessEqual(abs(vals["motor"]), 100.0)
            self.assertGreater(vals["vbat"], 8.0)
            self.assertLess(abs(vals["pitch"]), 1e4)

    def test_stop_is_idempotent(self):
        src = DemoSource(rate=100)
        src.start()
        src.stop()
        src.stop()

    def test_is_not_writable(self):
        src = DemoSource()
        self.assertFalse(src.writable)
        self.assertFalse(src.write("x"))


class TestFileSource(unittest.TestCase):
    def setUp(self):
        handle = tempfile.NamedTemporaryFile(  # noqa: SIM115 -- delete=False needs a manual close
            "w", suffix=".log", delete=False, encoding="utf-8"
        )
        handle.write("a:1\na:2\na:3\n")
        handle.close()
        self.path = handle.name
        self.addCleanup(lambda: os.path.exists(self.path) and os.unlink(self.path))

    def test_replays_every_line(self):
        lines = collect(FileSource(self.path), minimum=3)
        self.assertEqual(lines, ["a:1", "a:2", "a:3"])

    def test_marks_itself_finished_at_eof(self):
        src = FileSource(self.path)
        collect(src, minimum=3)
        self.assertTrue(src.finished)

    def test_missing_file_reports_a_source_error(self):
        src = FileSource(os.path.join(tempfile.gettempdir(), "nope-12345.log"))
        collect(src, minimum=1, timeout=2.0)
        self.assertIsInstance(src.check_error(), SourceError)

    def test_rate_limited_replay_still_delivers_everything(self):
        lines = collect(FileSource(self.path, rate=200), minimum=3)
        self.assertEqual(len(lines), 3)


class TestDrain(unittest.TestCase):
    def test_drain_is_bounded(self):
        # An uncapped drain lets a fast device starve the render loop.
        src = DemoSource(rate=100)
        for i in range(50):
            src._emit(f"a:{i}")
        self.assertEqual(len(src.drain(limit=10)), 10)
        self.assertEqual(len(src.drain(limit=100)), 40)

    def test_drain_on_an_idle_source_is_empty(self):
        self.assertEqual(DemoSource().drain(), [])


class TestSerialSourceWithoutPyserial(unittest.TestCase):
    def test_missing_pyserial_explains_itself(self):
        from termscope.sources import SerialSource

        try:
            import serial  # noqa: F401
        except ImportError:
            pass
        else:
            self.skipTest("pyserial is installed")
        src = SerialSource("COM_NOT_REAL")
        collect(src, minimum=1, timeout=2.0)
        err = src.check_error()
        self.assertIsInstance(err, SourceError)
        self.assertIn("pyserial", str(err))
        self.assertIn("--demo", str(err))


class _FakeSerial:
    """Small pyserial-shaped fixture for deterministic disconnect tests."""

    def __init__(self, events: list[bytes | BaseException]) -> None:
        self._events = iter(events)
        self.closed = False

    @property
    def in_waiting(self) -> int:
        return 1

    def read(self, _size: int) -> bytes:
        event = next(self._events, b"")
        if isinstance(event, BaseException):
            raise event
        return event

    def close(self) -> None:
        self.closed = True

    def write(self, _data: bytes) -> int:
        if self.closed:
            raise OSError("closed")
        return 1


class TestSerialReconnect(unittest.TestCase):
    def test_programmatic_interval_must_be_positive_and_finite(self):
        from termscope.sources import SerialSource

        for value in (0.0, -1.0, float("nan"), float("inf")):
            with self.subTest(value=value), self.assertRaises(ValueError):
                SerialSource("COM7", reconnect_interval=value)

    def test_missing_pyserial_is_not_retried(self):
        from termscope.sources import SerialSource

        with patch.dict(sys.modules, {"serial": None}):
            src = SerialSource("COM7", reconnect_interval=0.01)
            collect(src, minimum=1, timeout=1.0)

        self.assertEqual(src.reconnect_count, 0)
        self.assertIsInstance(src.check_error(), SourceError)
        self.assertIn("pyserial", str(src.check_error()))

    def test_opt_in_reconnect_keeps_lines_across_a_disconnect(self):
        from termscope.sources import SerialSource

        first = _FakeSerial([b"pitch:1\n", OSError("device removed")])
        second = _FakeSerial([b"pitch:2\n"])
        connections = iter([first, second])
        serial = types.SimpleNamespace(Serial=lambda *_args, **_kwargs: next(connections))

        with patch.dict(sys.modules, {"serial": serial}):
            src = SerialSource("COM7", reconnect_interval=0.01)
            lines = collect(src, minimum=2)

        self.assertEqual(lines, ["pitch:1", "pitch:2"])
        self.assertTrue(first.closed)
        self.assertEqual(src.reconnect_count, 1)
        self.assertEqual(src.description, "COM7 @ 115200")
        self.assertIsNone(src.check_error())

    def test_partial_line_is_not_spliced_to_the_next_device_session(self):
        from termscope.sources import SerialSource

        first = _FakeSerial([b"pitch:1", OSError("device removed")])
        second = _FakeSerial([b"pitch:2\n"])
        connections = iter([first, second])
        serial = types.SimpleNamespace(Serial=lambda *_args, **_kwargs: next(connections))

        with patch.dict(sys.modules, {"serial": serial}):
            lines = collect(
                SerialSource("COM7", reconnect_interval=0.01), minimum=1, timeout=1.0
            )

        self.assertEqual(lines, ["pitch:2"])

    def test_default_still_surfaces_a_disconnect_instead_of_retrying(self):
        from termscope.sources import SerialSource

        calls = 0

        def open_once(*_args, **_kwargs):
            nonlocal calls
            calls += 1
            return _FakeSerial([OSError("device removed")])

        with patch.dict(sys.modules, {"serial": types.SimpleNamespace(Serial=open_once)}):
            src = SerialSource("COM7")
            collect(src, minimum=1, timeout=1.0)

        self.assertEqual(calls, 1)
        self.assertIsInstance(src.check_error(), SourceError)
        self.assertIn("device removed", str(src.check_error()))

    def test_stop_interrupts_waiting_between_reconnect_attempts(self):
        from termscope.sources import SerialSource

        calls = 0

        def unavailable(*_args, **_kwargs):
            nonlocal calls
            calls += 1
            raise OSError("not present")

        with patch.dict(sys.modules, {"serial": types.SimpleNamespace(Serial=unavailable)}):
            src = SerialSource("COM7", reconnect_interval=0.01)
            src.start()
            deadline = time.monotonic() + 1.0
            while calls < 2 and time.monotonic() < deadline:
                time.sleep(0.005)
            src.stop()

        self.assertGreaterEqual(calls, 2)
        self.assertTrue(src.finished)
        self.assertIsNone(src.check_error())

    def test_invalid_serial_settings_are_not_retried(self):
        from termscope.sources import SerialSource

        calls = 0

        def invalid(*_args, **_kwargs):
            nonlocal calls
            calls += 1
            raise ValueError("unsupported baudrate")

        with patch.dict(sys.modules, {"serial": types.SimpleNamespace(Serial=invalid)}):
            src = SerialSource("COM7", reconnect_interval=0.01)
            collect(src, minimum=1, timeout=1.0)

        self.assertEqual(calls, 1)
        self.assertIsInstance(src.check_error(), SourceError)
        self.assertIn("invalid serial settings", str(src.check_error()))


if __name__ == "__main__":
    unittest.main()
