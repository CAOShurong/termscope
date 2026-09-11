"""Ring buffer and channel-set tests.

The wrap-around cases matter most: a scope that has been running for an hour
spends all of its time in the overwritten state, so that is the state the
buffer has to be correct in.
"""

from __future__ import annotations

import unittest

from termscope.buffers import ChannelBuffer, ChannelSet, moving_average


class TestChannelBuffer(unittest.TestCase):
    def test_moving_average_is_causal(self):
        self.assertEqual(moving_average([0.0, 10.0, 10.0], 2), [0.0, 5.0, 10.0])
        self.assertEqual(moving_average([1.0, 2.0, 3.0], 1), [1.0, 2.0, 3.0])

    def test_smooth_window_filters_values_not_timestamps(self):
        b = ChannelBuffer(8, smooth=2)
        for i, value in enumerate([0.0, 10.0, 10.0]):
            b.append(value, float(i))
        self.assertEqual(b.values(), [0.0, 5.0, 10.0])
        ts, vals = b.window()
        self.assertEqual(ts, [0.0, 1.0, 2.0])
        self.assertEqual(vals, [0.0, 5.0, 10.0])

    def test_append_and_read_back(self):
        b = ChannelBuffer(4)
        for i in range(3):
            b.append(float(i), float(i))
        self.assertEqual(len(b), 3)
        self.assertEqual(b.values(), [0.0, 1.0, 2.0])

    def test_capacity_is_a_hard_bound(self):
        b = ChannelBuffer(3)
        for i in range(100):
            b.append(float(i), float(i))
        self.assertEqual(len(b), 3)

    def test_oldest_samples_are_discarded_in_order(self):
        b = ChannelBuffer(3)
        for i in range(5):
            b.append(float(i), float(i))
        self.assertEqual(b.values(), [2.0, 3.0, 4.0])

    def test_timestamps_track_values_through_the_wrap(self):
        b = ChannelBuffer(3)
        for i in range(5):
            b.append(float(i) * 10, float(i))
        ts, vals = b.window()
        self.assertEqual(ts, [2.0, 3.0, 4.0])
        self.assertEqual(vals, [20.0, 30.0, 40.0])

    def test_total_counts_lifetime_not_contents(self):
        b = ChannelBuffer(2)
        for i in range(7):
            b.append(float(i), float(i))
        self.assertEqual(b.total, 7)
        self.assertEqual(len(b), 2)

    def test_partial_window_request(self):
        b = ChannelBuffer(10)
        for i in range(6):
            b.append(float(i), float(i))
        self.assertEqual(b.values(2), [4.0, 5.0])

    def test_window_larger_than_contents_is_clamped(self):
        b = ChannelBuffer(10)
        b.append(1.0, 0.0)
        self.assertEqual(b.values(99), [1.0])

    def test_empty_buffer_reads_are_safe(self):
        b = ChannelBuffer(4)
        self.assertEqual(b.values(), [])
        self.assertIsNone(b.last())
        self.assertIsNone(b.stats())

    def test_stats(self):
        b = ChannelBuffer(8)
        for v in (1.0, 5.0, 3.0):
            b.append(v, 0.0)
        st = b.stats()
        self.assertEqual((st.minimum, st.maximum, st.last, st.count), (1.0, 5.0, 3.0, 3))
        self.assertAlmostEqual(st.mean, 3.0)

    def test_clear_resets_everything(self):
        b = ChannelBuffer(4)
        b.append(1.0, 0.0)
        b.clear()
        self.assertEqual(len(b), 0)
        self.assertEqual(b.total, 0)
        self.assertIsNone(b.last())

    def test_rejects_zero_capacity(self):
        with self.assertRaises(ValueError):
            ChannelBuffer(0)


class TestChannelSet(unittest.TestCase):
    def test_channels_are_created_on_first_sight(self):
        cs = ChannelSet()
        cs.add({"a": 1.0, "b": 2.0}, 0.0)
        self.assertEqual(cs.names, ["a", "b"])

    def test_colour_slots_follow_first_seen_order(self):
        cs = ChannelSet()
        cs.add({"z": 1.0}, 0.0)
        cs.add({"a": 1.0}, 0.0)
        self.assertEqual(cs["z"].color_index, 0)
        self.assertEqual(cs["a"].color_index, 1)

    def test_hiding_a_channel_does_not_repaint_the_others(self):
        # Colour identifies the entity. A reader who learned "pitch is blue"
        # must not find it repainted because another trace was toggled off.
        cs = ChannelSet()
        cs.add({"a": 1.0, "b": 2.0, "c": 3.0}, 0.0)
        before = {n: cs[n].color_index for n in cs.names}
        cs["a"].visible = False
        after = {n: cs[n].color_index for n in cs.names}
        self.assertEqual(before, after)

    def test_channels_past_the_colour_cap_get_no_slot_and_start_hidden(self):
        cs = ChannelSet(color_slots=3)
        cs.add({f"c{i}": float(i) for i in range(5)}, 0.0)
        self.assertEqual(cs["c3"].color_index, -1)
        self.assertFalse(cs["c3"].visible)
        self.assertTrue(cs["c2"].visible)
        self.assertEqual(cs.overflow_names, ["c3", "c4"])

    def test_max_channels_stops_creating_new_buffers(self):
        cs = ChannelSet(max_channels=2)
        cs.add({"a": 1.0, "b": 2.0, "c": 3.0}, 0.0)
        self.assertEqual(cs.names, ["a", "b"])
        self.assertIsNone(cs.ensure("d"))

    def test_extent_spans_visible_channels_only(self):
        cs = ChannelSet()
        cs.add({"a": 1.0, "b": 100.0}, 0.0)
        self.assertEqual(cs.extent(), (1.0, 100.0))
        cs["b"].visible = False
        self.assertEqual(cs.extent(), (1.0, 1.0))

    def test_extent_is_none_without_data(self):
        self.assertIsNone(ChannelSet().extent())

    def test_extent_ignores_hidden_only_when_asked(self):
        cs = ChannelSet()
        cs.add({"a": 1.0, "b": 9.0}, 0.0)
        cs["b"].visible = False
        self.assertEqual(cs.extent(only_visible=False), (1.0, 9.0))

    def test_sparse_channels_keep_their_own_lengths(self):
        # Channels need not appear on every line; one going quiet must not
        # disturb the others.
        cs = ChannelSet()
        cs.add({"a": 1.0, "b": 1.0}, 0.0)
        cs.add({"a": 2.0}, 1.0)
        self.assertEqual(len(cs["a"]), 2)
        self.assertEqual(len(cs["b"]), 1)

    def test_visible_names(self):
        cs = ChannelSet()
        cs.add({"a": 1.0, "b": 2.0}, 0.0)
        cs["a"].visible = False
        self.assertEqual(cs.visible_names(), ["b"])


if __name__ == "__main__":
    unittest.main()
