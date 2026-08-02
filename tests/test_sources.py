"""Source tests.

Sources are threaded, so these tests poll with a deadline rather than sleeping
a fixed amount -- a fixed sleep is what makes a suite pass on a laptop and
fail on a loaded CI runner.
"""

from __future__ import annotations

import os
import tempfile
import time
import unittest

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


if __name__ == "__main__":
    unittest.main()
