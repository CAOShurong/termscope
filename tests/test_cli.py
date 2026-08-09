"""End-to-end tests through the CLI.

These run the same entry point a user runs. The plot is exercised in ``--once``
mode, which renders one frame to stdout with no alternate screen and no raw
mode -- so the full pipeline (source, parser, buffers, renderer) is covered
without needing a tty or a board.
"""

from __future__ import annotations

import csv
import io
import os
import re
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from typing import ClassVar

from termscope.cli import build_parser, main

ANSI = re.compile(r"\x1b\[[0-9;]*m")


def run(argv: list[str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main(argv)
    return code, out.getvalue(), err.getvalue()


class TestArgs(unittest.TestCase):
    def test_help_mentions_the_input_formats(self):
        text = build_parser().format_help()
        for fragment in ("pitch:1.23", ">temp:1000:23.5", "--demo", "--split", "--record"):
            self.assertIn(fragment, text)

    def test_version_exits_zero(self):
        with self.assertRaises(SystemExit) as ctx:
            run(["--version"])
        self.assertEqual(ctx.exception.code, 0)

    def test_defaults(self):
        args = build_parser().parse_args([])
        self.assertEqual(args.baud, 115200)
        self.assertEqual(args.window, 10.0)
        self.assertEqual(args.charset, "auto")


class TestOnce(unittest.TestCase):
    BASE: ClassVar[list[str]] = [
        "--demo",
        "--once",
        "1",
        "--color",
        "none",
        "--charset",
        "ascii",
    ]

    def test_renders_a_frame(self):
        code, out, _ = run(self.BASE)
        self.assertEqual(code, 0)
        self.assertIn("pitch", out)
        self.assertIn("motor", out)
        self.assertIn("now", out)

    def test_output_is_plain_text_when_colour_is_off(self):
        # `termscope --once > plot.txt` has to produce a clean file.
        _, out, _ = run(self.BASE)
        self.assertEqual(out, ANSI.sub("", out))

    def test_split_mode_renders(self):
        code, out, _ = run([*self.BASE, "--split"])
        self.assertEqual(code, 0)
        self.assertIn("vbat", out)

    def test_only_filters_channels(self):
        code, out, _ = run([*self.BASE, "--only", "pitch"])
        self.assertEqual(code, 0)
        self.assertIn("pitch", out)

    def test_sample_x_axis(self):
        code, out, _ = run([*self.BASE, "--samples"])
        self.assertEqual(code, 0)
        self.assertIn("samples", out)

    def test_no_grid(self):
        self.assertEqual(run([*self.BASE, "--no-grid"])[0], 0)

    def test_light_theme(self):
        self.assertEqual(run([*self.BASE, "--light"])[0], 0)

    def test_braille_output_is_encodable_as_utf8(self):
        _, out, _ = run(["--demo", "--once", "1", "--color", "none", "--charset", "braille"])
        out.encode("utf-8")
        self.assertIn("⠀", out)


class TestReplayRoundTrip(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        for name in os.listdir(self.dir):
            os.unlink(os.path.join(self.dir, name))
        os.rmdir(self.dir)

    def test_record_then_replay(self):
        capture = os.path.join(self.dir, "capture.csv")
        code, _, _ = run(
            [
                "--demo",
                "--once",
                "1",
                "--color",
                "none",
                "--charset",
                "ascii",
                "--record",
                capture,
            ]
        )
        self.assertEqual(code, 0)
        self.assertTrue(os.path.exists(capture))

        code, out, _ = run(
            ["--replay", capture, "--once", "1", "--color", "none", "--charset", "ascii"]
        )
        self.assertEqual(code, 0)
        # The CSV header becomes the channel names on the way back in.
        self.assertIn("pitch", out)

    def test_timestamped_teleplot_batch_reaches_the_recording_pipeline(self):
        source = os.path.join(self.dir, "teleplot.log")
        capture = os.path.join(self.dir, "teleplot.csv")
        with open(source, "w", encoding="utf-8") as handle:
            handle.write(">temp:1000:20;1250:21;1500:22§°C\n")

        code, out, err = run(
            [
                "--replay",
                source,
                "--once",
                "0.1",
                "--color",
                "none",
                "--charset",
                "ascii",
                "--record",
                capture,
            ]
        )
        self.assertEqual((code, err), (0, ""))
        self.assertIn("temp", out)
        with open(capture, newline="", encoding="utf-8") as handle:
            rows = list(csv.reader(handle))
        self.assertEqual(rows[0], ["time_s", "temp"])
        self.assertEqual([row[1] for row in rows[1:]], ["20.0", "21.0", "22.0"])

    def test_replaying_a_missing_file_fails_cleanly(self):
        code, _, err = run(["--replay", os.path.join(self.dir, "nope.log"), "--once", "1"])
        self.assertEqual(code, 1)
        self.assertIn("termscope:", err)


class TestFailureModes(unittest.TestCase):
    def test_unparseable_stream_reports_rather_than_drawing_nothing(self):
        dir_ = tempfile.mkdtemp()
        path = os.path.join(dir_, "junk.log")
        try:
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("boot ok\nready\nnothing numeric here\n")
            code, _, err = run(["--replay", path, "--once", "1"])
            self.assertEqual(code, 1)
            self.assertIn("no samples parsed", err)
        finally:
            os.unlink(path)
            os.rmdir(dir_)


if __name__ == "__main__":
    unittest.main()
