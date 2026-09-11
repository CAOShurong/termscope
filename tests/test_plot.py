"""Renderer tests: ticks, layout, and the guarantees the palette relies on."""

from __future__ import annotations

import re
import unittest

from termscope.buffers import ChannelSet
from termscope.canvas import ASCII
from termscope.palette import MAX_SERIES, Palette
from termscope.plot import (
    Renderer,
    format_tick,
    format_value,
    nice_ticks,
    tick_step,
)

ANSI = re.compile(r"\x1b\[[0-9;]*m")


def visible_len(text: str) -> int:
    return len(ANSI.sub("", text))


def make_channels(names, samples=50):
    cs = ChannelSet(capacity=256)
    for i in range(samples):
        cs.add({name: float(i % 10) + j for j, name in enumerate(names)}, float(i))
    return cs


class TestNiceTicks(unittest.TestCase):
    def test_ticks_land_on_round_numbers(self):
        self.assertEqual(nice_ticks(0, 10, 5), [0.0, 2.0, 4.0, 6.0, 8.0, 10.0])

    def test_ticks_are_inside_the_range(self):
        for lo, hi in ((-3.7, 8.2), (0.001, 0.009), (-1e6, 1e6)):
            for tick in nice_ticks(lo, hi):
                self.assertGreaterEqual(tick, lo - 1e-9)
                self.assertLessEqual(tick, hi + 1e-9)

    def test_zero_is_exactly_zero(self):
        # A tick of -4.4e-17 would render as "-0.0000" in the gutter.
        ticks = nice_ticks(-5, 5, 4)
        self.assertIn(0.0, ticks)
        self.assertNotIn("-0", [f"{t:.1f}" for t in ticks])

    def test_degenerate_range(self):
        self.assertEqual(nice_ticks(3.0, 3.0), [3.0])

    def test_reversed_range_is_tolerated(self):
        self.assertTrue(nice_ticks(10, 0))

    def test_non_finite_range_yields_nothing(self):
        self.assertEqual(nice_ticks(float("nan"), 1.0), [])
        self.assertEqual(nice_ticks(0.0, float("inf")), [])

    def test_tick_count_stays_near_the_target(self):
        for lo, hi in ((0, 1), (0, 7), (-100, 340), (0.02, 0.07)):
            count = len(nice_ticks(lo, hi, 5))
            self.assertGreaterEqual(count, 2)
            self.assertLessEqual(count, 12)


class TestFormatting(unittest.TestCase):
    def test_tick_precision_follows_the_step(self):
        ticks = [0.0, 10.0, 20.0]
        step = tick_step(ticks)
        self.assertEqual([format_tick(t, step) for t in ticks], ["0", "10", "20"])

    def test_fractional_step_keeps_decimals(self):
        ticks = [0.0, 0.5, 1.0]
        step = tick_step(ticks)
        self.assertEqual([format_tick(t, step) for t in ticks], ["0.0", "0.5", "1.0"])

    def test_axis_labels_share_one_precision(self):
        # The ragged "0.0000 / 5.000 / 10.00" column is the bug this prevents.
        ticks = nice_ticks(0, 30, 4)
        step = tick_step(ticks)
        widths = {
            len(format_tick(t, step).split(".")[-1])
            for t in ticks
            if "." in format_tick(t, step)
        }
        self.assertLessEqual(len(widths), 1)

    def test_negative_zero_is_normalised(self):
        self.assertEqual(format_tick(-0.0001, 1.0), "0")

    def test_format_value_handles_non_finite(self):
        self.assertEqual(format_value(float("nan")), "nan")
        self.assertEqual(format_value(float("inf")), "inf")
        self.assertEqual(format_value(float("-inf")), "-inf")

    def test_format_value_respects_width(self):
        for value in (0.00001234, 123456789.0, -98765.4321):
            self.assertLessEqual(len(format_value(value, width=8)), 8)


class TestLayout(unittest.TestCase):
    def setUp(self):
        self.renderer = Renderer(Palette(depth="none"), charset=ASCII)

    def test_axis_band_and_legend_always_get_rows(self):
        # A plot that expands to fill the window and pushes its own axis
        # labels off-screen is the failure this guards.
        for height in (8, 12, 24, 60):
            layout = self.renderer.layout(80, height, 3)
            self.assertGreaterEqual(layout.legend_rows, 1)
            self.assertLessEqual(layout.plot_height + 3 + layout.legend_rows, height)

    def test_plot_survives_a_tiny_terminal(self):
        layout = self.renderer.layout(20, 8, 2)
        self.assertGreaterEqual(layout.plot_height, 2)
        self.assertGreaterEqual(layout.plot_width, 1)

    def test_pad_range_gives_a_flat_signal_room(self):
        lo, hi = self.renderer.pad_range(5.0, 5.0)
        self.assertLess(lo, 5.0)
        self.assertGreater(hi, 5.0)

    def test_pad_range_rejects_non_finite(self):
        self.assertEqual(self.renderer.pad_range(float("nan"), 1.0), (-1.0, 1.0))


class TestRendering(unittest.TestCase):
    def setUp(self):
        self.palette = Palette(depth="none")
        self.renderer = Renderer(self.palette, charset=ASCII)

    def test_plot_rows_match_the_layout(self):
        cs = make_channels(["a", "b"])
        layout = self.renderer.layout(80, 24, 2)
        rows = self.renderer.render_plot_rows(
            cs,
            ["a", "b"],
            layout,
            0.0,
            10.0,
            window=10.0,
            now=60.0,
            x_mode="sample",
        )
        self.assertEqual(len(rows), layout.plot_height)
        for row in rows:
            self.assertEqual(visible_len(row), layout.width)

    def test_empty_channel_list_still_renders(self):
        layout = self.renderer.layout(80, 24, 0)
        rows = self.renderer.render_plot_rows(
            ChannelSet(),
            [],
            layout,
            0.0,
            1.0,
            window=10.0,
            now=0.0,
            x_mode="time",
        )
        self.assertEqual(len(rows), layout.plot_height)

    def test_split_mode_gives_each_channel_a_panel(self):
        cs = make_channels(["a", "b", "c"])
        layout = self.renderer.layout(80, 24, 3)
        rows = self.renderer.render_split_rows(
            cs,
            ["a", "b", "c"],
            layout,
            window=10.0,
            now=60.0,
            x_mode="sample",
        )
        self.assertEqual(len(rows), layout.plot_height)
        joined = "\n".join(rows)
        for name in ("a", "b", "c"):
            self.assertIn(name, joined)

    def test_legend_stats_include_min_max_mean(self):
        cs = make_channels(["pitch"])
        layout = self.renderer.layout(120, 24, 1)
        plain = " ".join(self.renderer.render_legend(cs, layout, show_stats=True))
        self.assertIn("min", plain)
        self.assertIn("max", plain)
        self.assertIn("mean", plain)
        compact = " ".join(self.renderer.render_legend(cs, layout))
        self.assertNotIn(" mean ", f" {compact} ")

    def test_legend_names_every_channel_with_its_value(self):
        # This is the accessibility relief the palette depends on: several
        # hues sit below 3:1 on a light surface, which is only permitted
        # because the value is legible as text here.
        cs = make_channels(["pitch", "roll"])
        layout = self.renderer.layout(80, 24, 2)
        text = " ".join(self.renderer.render_legend(cs, layout))
        self.assertIn("pitch", text)
        self.assertIn("roll", text)
        self.assertRegex(text, r"pitch\s+-?\d")

    def test_legend_never_exceeds_its_row_budget(self):
        cs = make_channels([f"channel_{i}" for i in range(8)])
        layout = self.renderer.layout(60, 24, 8)
        rows = self.renderer.render_legend(cs, layout)
        self.assertEqual(len(rows), layout.legend_rows)
        for row in rows:
            self.assertLessEqual(visible_len(row), layout.width + 8)

    def test_dropped_legend_entries_are_reported_not_silently_lost(self):
        cs = make_channels([f"a_very_long_channel_name_{i}" for i in range(8)])
        layout = self.renderer.layout(40, 20, 8)
        rows = self.renderer.render_legend(cs, layout)
        self.assertIn("more", " ".join(rows))

    def test_xaxis_band_fits_the_width(self):
        layout = self.renderer.layout(80, 24, 2)
        band = self.renderer.render_xaxis(layout, window=10.0, x_mode="time", sample_count=100)
        self.assertLessEqual(visible_len(band), layout.width)
        self.assertIn("now", band)

    def test_header_fits_the_width(self):
        header = self.renderer.render_header("a" * 200, "30/s", 80)
        self.assertLessEqual(visible_len(header), 80)


class TestColorOutput(unittest.TestCase):
    def test_colour_codes_appear_when_enabled(self):
        renderer = Renderer(Palette(depth="truecolor"), charset=ASCII)
        cs = make_channels(["a"])
        layout = renderer.layout(80, 24, 1)
        rows = renderer.render_plot_rows(
            cs, ["a"], layout, 0.0, 10.0, window=10.0, now=60.0, x_mode="sample"
        )
        self.assertIn("\x1b[38;2;", "".join(rows))

    def test_visible_width_is_unchanged_by_colour(self):
        cs = make_channels(["a", "b"])
        plain = Renderer(Palette(depth="none"), charset=ASCII)
        color = Renderer(Palette(depth="truecolor"), charset=ASCII)
        layout = plain.layout(80, 24, 2)
        args = {"window": 10.0, "now": 60.0, "x_mode": "sample"}
        a = plain.render_plot_rows(cs, ["a", "b"], layout, 0.0, 10.0, **args)
        b = color.render_plot_rows(cs, ["a", "b"], layout, 0.0, 10.0, **args)
        self.assertEqual([visible_len(r) for r in a], [visible_len(r) for r in b])

    def test_split_panel_title_keeps_its_width_with_colour(self):
        cs = make_channels(["alpha", "beta"])
        renderer = Renderer(Palette(depth="truecolor"), charset=ASCII)
        layout = renderer.layout(80, 24, 2)
        rows = renderer.render_split_rows(
            cs,
            ["alpha", "beta"],
            layout,
            window=10.0,
            now=60.0,
            x_mode="sample",
        )
        for row in rows:
            self.assertEqual(visible_len(row), layout.width)

    def test_no_hue_is_ever_reused_across_slots(self):
        pal = Palette()
        codes = {pal.series_hex(i) for i in range(MAX_SERIES)}
        self.assertEqual(len(codes), MAX_SERIES)

    def test_out_of_range_slot_falls_back_to_muted_not_a_reused_hue(self):
        pal = Palette()
        self.assertEqual(pal.series_fg(MAX_SERIES), pal.muted())
        self.assertEqual(pal.series_fg(-1), pal.muted())


if __name__ == "__main__":
    unittest.main()
