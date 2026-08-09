"""Time-base tests: a clock carried in the stream beats the clock on the wall."""

from __future__ import annotations

import unittest

from termscope.parser import TELEPLOT_TIME_KEY
from termscope.timebase import TimeBase


class TestDetection(unittest.TestCase):
    def test_detects_time_s(self):
        tb = TimeBase()
        values, stamp = tb.apply({"time_s": 5.0, "a": 1.0}, fallback=999.0)
        self.assertTrue(tb.active)
        self.assertEqual(tb.column, "time_s")
        self.assertEqual(values, {"a": 1.0})
        self.assertEqual(stamp, 0.0)

    def test_detects_millis_and_converts_to_seconds(self):
        tb = TimeBase()
        tb.apply({"millis": 1000.0, "a": 1.0}, fallback=0.0)
        _, stamp = tb.apply({"millis": 2500.0, "a": 1.0}, fallback=0.0)
        self.assertAlmostEqual(stamp, 1.5)

    def test_detects_micros(self):
        tb = TimeBase()
        tb.apply({"micros": 0.0, "a": 1.0}, fallback=0.0)
        _, stamp = tb.apply({"micros": 2_000_000.0, "a": 1.0}, fallback=0.0)
        self.assertAlmostEqual(stamp, 2.0)

    def test_detection_is_case_insensitive(self):
        tb = TimeBase()
        tb.apply({"Millis": 0.0, "a": 1.0}, fallback=0.0)
        self.assertEqual(tb.column, "Millis")

    def test_more_specific_name_wins(self):
        tb = TimeBase()
        tb.apply({"t": 9.0, "time_s": 1.0, "a": 1.0}, fallback=0.0)
        self.assertEqual(tb.column, "time_s")

    def test_no_candidate_means_arrival_time(self):
        tb = TimeBase()
        values, stamp = tb.apply({"pitch": 1.0, "roll": 2.0}, fallback=42.0)
        self.assertFalse(tb.active)
        self.assertEqual(stamp, 42.0)
        self.assertEqual(values, {"pitch": 1.0, "roll": 2.0})

    def test_detection_happens_once_and_sticks(self):
        # Switching axis midway would put the plot in two coordinate systems.
        tb = TimeBase()
        tb.apply({"pitch": 1.0}, fallback=0.0)
        tb.apply({"millis": 100.0, "pitch": 1.0}, fallback=0.0)
        self.assertFalse(tb.active)


class TestExplicitColumn(unittest.TestCase):
    def test_named_column_is_used(self):
        tb = TimeBase("tick")
        values, stamp = tb.apply({"tick": 10.0, "a": 1.0}, fallback=0.0)
        self.assertEqual(values, {"a": 1.0})
        self.assertEqual(stamp, 0.0)

    def test_named_column_takes_units_from_a_known_name(self):
        tb = TimeBase("millis")
        tb.apply({"millis": 0.0, "a": 1.0}, fallback=0.0)
        _, stamp = tb.apply({"millis": 500.0, "a": 1.0}, fallback=0.0)
        self.assertAlmostEqual(stamp, 0.5)

    def test_unknown_named_column_is_treated_as_seconds(self):
        tb = TimeBase("tick")
        self.assertEqual(tb.scale, 1.0)

    def test_auto_can_be_disabled(self):
        tb = TimeBase(None, auto=False)
        values, stamp = tb.apply({"time_s": 5.0, "a": 1.0}, fallback=7.0)
        self.assertFalse(tb.active)
        self.assertEqual(stamp, 7.0)
        self.assertIn("time_s", values)


class TestBehaviour(unittest.TestCase):
    def test_timestamps_are_relative_to_the_first_sample(self):
        tb = TimeBase()
        _, first = tb.apply({"time_s": 1000.0, "a": 1.0}, fallback=0.0)
        _, second = tb.apply({"time_s": 1002.5, "a": 1.0}, fallback=0.0)
        self.assertEqual(first, 0.0)
        self.assertAlmostEqual(second, 2.5)

    def test_a_reboot_reanchors_instead_of_going_backwards(self):
        # A counter that restarts must not emit a timestamp before data that
        # is already plotted.
        tb = TimeBase()
        tb.apply({"millis": 50_000.0, "a": 1.0}, fallback=0.0)
        _, after = tb.apply({"millis": 10.0, "a": 1.0}, fallback=0.0)
        self.assertGreaterEqual(after, 0.0)

    def test_a_line_with_only_a_timestamp_is_not_a_sample(self):
        tb = TimeBase()
        tb.apply({"time_s": 1.0, "a": 1.0}, fallback=0.0)
        values, stamp = tb.apply({"time_s": 2.0}, fallback=99.0)
        self.assertEqual(values, {})
        self.assertEqual(stamp, 99.0)

    def test_missing_time_column_on_one_line_falls_back(self):
        tb = TimeBase()
        tb.apply({"time_s": 1.0, "a": 1.0}, fallback=0.0)
        _, stamp = tb.apply({"a": 2.0}, fallback=55.0)
        self.assertEqual(stamp, 55.0)

    def test_the_time_column_is_never_plotted(self):
        tb = TimeBase()
        for i in range(5):
            values, _ = tb.apply({"time_s": float(i), "a": 1.0}, fallback=0.0)
            self.assertNotIn("time_s", values)

    def test_teleplot_milliseconds_are_normalised_and_removed_from_the_plot(self):
        tb = TimeBase()
        values, first = tb.apply({TELEPLOT_TIME_KEY: 1000.0, "temp": 20.0}, 999.0)
        values2, second = tb.apply({TELEPLOT_TIME_KEY: 1250.0, "temp": 21.0}, 999.0)
        self.assertEqual(values, {"temp": 20.0})
        self.assertEqual(values2, {"temp": 21.0})
        self.assertEqual(first, 0.0)
        self.assertEqual(second, 0.25)
        self.assertTrue(tb.active)

    def test_no_time_column_drops_teleplot_metadata_and_uses_arrival_time(self):
        tb = TimeBase(auto=False)
        values, stamp = tb.apply({TELEPLOT_TIME_KEY: 1000.0, "temp": 20.0}, 999.0)
        self.assertEqual(values, {"temp": 20.0})
        self.assertEqual(stamp, 999.0)


if __name__ == "__main__":
    unittest.main()
