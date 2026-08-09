"""Parser tests.

Weighted towards the messy cases, because the clean ones were never the
problem: real serial streams interleave telemetry with log output, reboot
mid-capture, and emit the occasional half-line of garbage.
"""

from __future__ import annotations

import unittest

from termscope.parser import TELEPLOT_TIME_KEY, Format, StreamParser


class TestLabelled(unittest.TestCase):
    def test_colon_pairs(self):
        p = StreamParser()
        result = p.feed("pitch:1.25 roll:-4.5")
        self.assertEqual(result.values, {"pitch": 1.25, "roll": -4.5})
        self.assertEqual(result.fmt, Format.LABELLED)

    def test_equals_pairs(self):
        p = StreamParser()
        self.assertEqual(p.feed("a=1 b=2").values, {"a": 1.0, "b": 2.0})

    def test_mixed_separators_and_spacing(self):
        p = StreamParser()
        self.assertEqual(p.feed("temp : 24.5, hum=61").values, {"temp": 24.5, "hum": 61.0})

    def test_scientific_notation(self):
        p = StreamParser()
        self.assertEqual(p.feed("x:1.5e-3").values, {"x": 0.0015})

    def test_dotted_and_indexed_keys_survive(self):
        p = StreamParser()
        self.assertEqual(p.feed("imu.gyro[0]:0.5").values, {"imu.gyro[0]": 0.5})

    def test_telemetry_embedded_in_a_log_line(self):
        # Once locked to labelled, readings are still found inside prose --
        # firmware routinely prints "[INFO] battery=11.8V ok".
        p = StreamParser()
        p.feed("v:1")
        self.assertEqual(p.feed("[INFO] battery=11.8 ok").values, {"battery": 11.8})


class TestBare(unittest.TestCase):
    def test_comma_separated(self):
        p = StreamParser()
        result = p.feed("1.0,2.0,3.0")
        self.assertEqual(result.values, {"ch0": 1.0, "ch1": 2.0, "ch2": 3.0})
        self.assertEqual(result.fmt, Format.BARE)

    def test_whitespace_separated(self):
        p = StreamParser()
        self.assertEqual(p.feed("1 2 3").values, {"ch0": 1.0, "ch1": 2.0, "ch2": 3.0})

    def test_single_value(self):
        p = StreamParser()
        self.assertEqual(p.feed("42").values, {"ch0": 42.0})

    def test_line_with_a_stray_word_is_not_data(self):
        p = StreamParser()
        p.feed("1 2 3")
        self.assertEqual(p.feed("1 2 boot").values, {})

    def test_negative_and_leading_dot(self):
        p = StreamParser()
        self.assertEqual(p.feed("-1.5 .25").values, {"ch0": -1.5, "ch1": 0.25})


class TestCsvHeader(unittest.TestCase):
    def test_header_names_the_columns(self):
        p = StreamParser()
        p.feed("time,pitch,roll")
        result = p.feed("0.1,2.0,3.0")
        self.assertEqual(result.values, {"time": 0.1, "pitch": 2.0, "roll": 3.0})

    def test_header_reappearing_midstream_rekeys(self):
        # A board that reboots reprints its header; the new names must win.
        p = StreamParser()
        p.feed("a,b")
        p.feed("1,2")
        p.feed("x,y")
        self.assertEqual(p.feed("3,4").values, {"x": 3.0, "y": 4.0})

    def test_extra_columns_beyond_the_header_fall_back_to_index(self):
        p = StreamParser()
        p.feed("a,b")
        self.assertEqual(p.feed("1,2,3").values, {"a": 1.0, "b": 2.0, "ch2": 3.0})


class TestJson(unittest.TestCase):
    def test_object_per_line(self):
        p = StreamParser()
        result = p.feed('{"pitch": 1.5, "roll": -2}')
        self.assertEqual(result.values, {"pitch": 1.5, "roll": -2.0})
        self.assertEqual(result.fmt, Format.JSON)

    def test_non_numeric_fields_are_dropped(self):
        p = StreamParser()
        self.assertEqual(p.feed('{"a": 1, "state": "idle"}').values, {"a": 1.0})

    def test_booleans_become_zero_and_one(self):
        p = StreamParser()
        self.assertEqual(p.feed('{"armed": true}').values, {"armed": 1.0})

    def test_malformed_json_does_not_raise(self):
        p = StreamParser()
        p.feed('{"a": 1}')
        self.assertEqual(p.feed('{"a": ').values, {})


class TestTeleplot(unittest.TestCase):
    def test_single_point_and_unit(self):
        p = StreamParser()
        result = p.feed(">temperature:23.5§°C|g")
        self.assertEqual(result.values, {"temperature": 23.5})
        self.assertEqual(result.fmt, Format.TELEPLOT)

    def test_explicit_millisecond_timestamp_is_not_plotted_as_the_value(self):
        p = StreamParser()
        result = p.feed(">temperature:1627551892437:23.5")
        self.assertEqual(
            result.values,
            {TELEPLOT_TIME_KEY: 1627551892437.0, "temperature": 23.5},
        )

    def test_batched_points_are_preserved(self):
        p = StreamParser()
        result = p.feed(">temperature:1000:20;1250:21;1500:22§°C")
        self.assertEqual(
            result.samples,
            [
                {TELEPLOT_TIME_KEY: 1000.0, "temperature": 20.0},
                {TELEPLOT_TIME_KEY: 1250.0, "temperature": 21.0},
                {TELEPLOT_TIME_KEY: 1500.0, "temperature": 22.0},
            ],
        )
        self.assertEqual(p.parsed_count, 3)
        self.assertEqual(p.channels, ["temperature"])

    def test_unrepresentable_message_types_are_not_misplotted(self):
        for line in (
            ">trajectory:12.3:45.67|xy",
            ">state:running|t",
            ">temperature:23.5|np",
            ">temperature:23.5|clr",
            ">:device rebooted",
            ">3D|cube:S:cube:P:1:1:1",
        ):
            with self.subTest(line=line):
                result = StreamParser().feed(line)
                self.assertEqual(result.values, {})
                self.assertEqual(result.fmt, Format.TELEPLOT)

    def test_tagged_multi_channel_line_keeps_existing_labelled_semantics(self):
        p = StreamParser(prefix=">")
        result = p.feed(">pitch:1.5 roll:2")
        self.assertEqual(result.values, {"pitch": 1.5, "roll": 2.0})
        self.assertEqual(result.fmt, Format.LABELLED)


class TestRobustness(unittest.TestCase):
    def test_blank_lines(self):
        p = StreamParser()
        self.assertEqual(p.feed("").values, {})
        self.assertEqual(p.feed("   ").values, {})

    def test_ansi_colour_is_stripped(self):
        p = StreamParser()
        self.assertEqual(p.feed("\x1b[32mtemp:24.5\x1b[0m").values, {"temp": 24.5})

    def test_control_bytes_are_stripped(self):
        p = StreamParser()
        self.assertEqual(p.feed("\x00temp:24.5\x07").values, {"temp": 24.5})

    def test_nan_and_inf_are_rejected(self):
        # Both would poison autoscaling and blank the plot.
        p = StreamParser()
        p.feed("a:1")
        self.assertEqual(p.feed("a:nan").values, {})
        self.assertEqual(p.feed('{"a": 1e400}').values, {})

    def test_format_lock_survives_interleaved_junk(self):
        p = StreamParser()
        p.feed("1,2")
        p.feed("*** rebooting ***")
        self.assertEqual(p.feed("3,4").values, {"ch0": 3.0, "ch1": 4.0})
        self.assertEqual(p.fmt, Format.BARE)

    def test_skipped_lines_are_counted(self):
        p = StreamParser()
        p.feed("hello")
        p.feed("world")
        self.assertEqual(p.skipped_count, 2)
        self.assertEqual(p.parsed_count, 0)

    def test_prefix_selects_telemetry_only(self):
        p = StreamParser(prefix=">")
        self.assertEqual(p.feed("[log] pitch:9").values, {})
        self.assertEqual(p.feed(">pitch:1.5").values, {"pitch": 1.5})

    def test_channel_cap_is_enforced(self):
        p = StreamParser(max_channels=2)
        p.feed("a:1 b:2 c:3")
        self.assertEqual(p.channels, ["a", "b"])

    def test_channel_order_is_first_seen(self):
        p = StreamParser()
        p.feed("z:1 a:2")
        p.feed("m:3")
        self.assertEqual(p.channels, ["z", "a", "m"])

    def test_very_long_line_does_not_hang(self):
        p = StreamParser()
        p.feed(" ".join(f"k{i}:{i}" for i in range(1000)))
        self.assertEqual(len(p.channels), 32)


if __name__ == "__main__":
    unittest.main()
