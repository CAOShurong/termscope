"""Observable overload reporting in the interactive application."""

from __future__ import annotations

import io
import unittest
from contextlib import redirect_stdout

from termscope.app import App, Options
from termscope.sources import DemoSource


class TestInputOverloadReporting(unittest.TestCase):
    def setUp(self):
        self.source = DemoSource(queue_capacity=1)
        self.source._emit("a:1")
        self.source._emit("a:2")
        self.app = App(
            self.source,
            Options(charset="ascii", color_depth="none"),
        )

    def test_ylim_starts_with_autoscale_off(self):
        app = App(DemoSource(), Options(ylim=(-30.0, 30.0)))
        self.assertFalse(app.autoscale)
        self.assertEqual(app.held_range, (-30.0, 30.0))

    def test_header_and_status_show_the_drop_count(self):
        self.assertIn("DROP 1", self.app._header_right())
        self.assertIn("1 input line(s) dropped", self.app._status_text())
        self.assertIn("newest live data kept", self.app._status_text())

    def test_summary_does_not_hide_overload_without_samples(self):
        out = io.StringIO()
        with redirect_stdout(out):
            self.app._print_summary()
        self.assertIn("1 input lines were dropped", out.getvalue())


if __name__ == "__main__":
    unittest.main()
