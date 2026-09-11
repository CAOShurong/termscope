"""Trigger parsing and edge detection."""

from __future__ import annotations

import unittest

from termscope.trigger import TriggerWatch, parse_trigger


class TestParseTrigger(unittest.TestCase):
    def test_colon_uses_default_rising_edge(self):
        spec = parse_trigger("pitch:5")
        self.assertEqual(spec.name, "pitch")
        self.assertEqual(spec.threshold, 5.0)
        self.assertEqual(spec.edge, "rising")
        self.assertEqual(spec.describe(), "pitch>5")

    def test_explicit_operators_set_the_edge(self):
        self.assertEqual(parse_trigger("pitch>5").edge, "rising")
        self.assertEqual(parse_trigger("pitch<0").edge, "falling")
        self.assertEqual(parse_trigger("err<=-1").describe(), "err<-1")

    def test_colon_honours_default_edge(self):
        spec = parse_trigger("vbat:12", default_edge="falling")
        self.assertEqual(spec.edge, "falling")
        self.assertEqual(spec.describe(), "vbat<12")

    def test_rejects_junk(self):
        for spec in ("", "pitch", ":>1", "pitch:nan", "pitch:inf", "pitch:nope"):
            with self.assertRaises(ValueError):
                parse_trigger(spec)


class TestTriggerWatch(unittest.TestCase):
    def test_rising_fires_on_the_crossing_sample(self):
        watch = TriggerWatch(parse_trigger("pitch:5"))
        self.assertFalse(watch.observe({"pitch": 1.0}))
        self.assertFalse(watch.observe({"pitch": 4.9}))
        self.assertTrue(watch.observe({"pitch": 5.0}))
        self.assertTrue(watch.fired)
        self.assertFalse(watch.armed)
        # Stays latched.
        self.assertFalse(watch.observe({"pitch": 6.0}))

    def test_does_not_fire_if_already_above_on_the_first_sample(self):
        watch = TriggerWatch(parse_trigger("pitch:5"))
        self.assertFalse(watch.observe({"pitch": 9.0}))
        self.assertFalse(watch.observe({"pitch": 9.1}))

    def test_falling_edge(self):
        watch = TriggerWatch(parse_trigger("pitch<0"))
        self.assertFalse(watch.observe({"pitch": 1.0}))
        self.assertTrue(watch.observe({"pitch": -0.1}))

    def test_rearm_waits_for_another_crossing(self):
        watch = TriggerWatch(parse_trigger("pitch:5"))
        watch.observe({"pitch": 1.0})
        watch.observe({"pitch": 6.0})
        self.assertTrue(watch.fired)
        watch.rearm()
        self.assertFalse(watch.fired)
        self.assertTrue(watch.armed)
        # Still above the threshold: must recross from below.
        self.assertFalse(watch.observe({"pitch": 7.0}))
        self.assertFalse(watch.observe({"pitch": 1.0}))
        self.assertTrue(watch.observe({"pitch": 5.5}))

    def test_ignores_other_channels(self):
        watch = TriggerWatch(parse_trigger("pitch:5"))
        self.assertFalse(watch.observe({"motor": 100.0}))
        self.assertIsNone(watch._last)
