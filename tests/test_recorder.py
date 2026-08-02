"""Recorder tests: the file has to stay readable when channels arrive late."""

from __future__ import annotations

import csv
import os
import tempfile
import unittest

from termscope.recorder import Recorder, RecorderError


class RecorderTestCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "capture.csv")

    def tearDown(self):
        for name in os.listdir(self.dir):
            os.unlink(os.path.join(self.dir, name))
        os.rmdir(self.dir)

    def read_rows(self, path: str | None = None):
        with open(path or self.path, newline="", encoding="utf-8") as handle:
            return list(csv.reader(handle))

    @staticmethod
    def read_text(path: str) -> str:
        with open(path, encoding="utf-8") as handle:
            return handle.read()

    @staticmethod
    def write_text(path: str, text: str) -> None:
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)


class TestCsvRecording(RecorderTestCase):
    def test_header_then_rows(self):
        with Recorder(self.path) as rec:
            rec.write_values({"a": 1.0, "b": 2.0}, 100.0)
            rec.write_values({"a": 3.0, "b": 4.0}, 101.0)
            rec.flush()
        rows = self.read_rows()
        self.assertEqual(rows[0], ["time_s", "a", "b"])
        self.assertEqual(rows[1][1:], ["1.0", "2.0"])
        self.assertEqual(rows[2][1:], ["3.0", "4.0"])

    def test_time_column_is_relative_to_the_first_sample(self):
        with Recorder(self.path) as rec:
            start = rec._start
            rec.write_values({"a": 1.0}, start + 2.5)
            rec.flush()
        self.assertAlmostEqual(float(self.read_rows()[1][0]), 2.5, places=3)

    def test_a_channel_appearing_late_gets_its_own_column(self):
        with Recorder(self.path) as rec:
            rec.write_values({"a": 1.0}, 100.0)
            rec.write_values({"a": 2.0, "b": 9.0}, 101.0)
            rec.flush()
        text = self.read_text(self.path)
        self.assertIn("new channels: b", text)
        rows = [r for r in self.read_rows() if r and not r[0].startswith("#")]
        self.assertEqual(rows[-1][1:], ["2.0", "9.0"])

    def test_missing_channels_leave_empty_cells_not_zeros(self):
        # Writing 0.0 for "no reading" would invent data that never arrived.
        with Recorder(self.path) as rec:
            rec.write_values({"a": 1.0, "b": 2.0}, 100.0)
            rec.write_values({"a": 3.0}, 101.0)
            rec.flush()
        rows = self.read_rows()
        self.assertEqual(rows[-1][2], "")

    def test_row_count_is_tracked(self):
        with Recorder(self.path) as rec:
            for i in range(5):
                rec.write_values({"a": float(i)}, 100.0 + i)
            self.assertEqual(rec.rows_written, 5)

    def test_values_round_trip_exactly(self):
        value = 0.1 + 0.2  # 0.30000000000000004
        with Recorder(self.path) as rec:
            rec.write_values({"a": value}, 100.0)
            rec.flush()
        self.assertEqual(float(self.read_rows()[1][1]), value)

    def test_raw_writes_are_ignored_in_csv_mode(self):
        with Recorder(self.path) as rec:
            rec.write_raw("junk")
            self.assertEqual(rec.rows_written, 0)


class TestRawRecording(RecorderTestCase):
    def test_lines_are_written_verbatim(self):
        path = os.path.join(self.dir, "raw.log")
        with Recorder(path, mode="raw") as rec:
            rec.write_raw("hello")
            rec.write_raw("world")
            rec.flush()
        self.assertEqual(self.read_text(path).splitlines(), ["hello", "world"])

    def test_value_writes_are_ignored_in_raw_mode(self):
        path = os.path.join(self.dir, "raw.log")
        with Recorder(path, mode="raw") as rec:
            rec.write_values({"a": 1.0}, 0.0)
            self.assertEqual(rec.rows_written, 0)


class TestGuards(RecorderTestCase):
    def test_existing_file_is_not_clobbered_by_default(self):
        self.write_text(self.path, "")
        with self.assertRaises(RecorderError):
            Recorder(self.path)

    def test_overwrite_is_opt_in(self):
        self.write_text(self.path, "old")
        Recorder(self.path, overwrite=True).close()
        self.assertEqual(self.read_text(self.path), "")

    def test_unknown_mode_is_rejected(self):
        with self.assertRaises(ValueError):
            Recorder(self.path, mode="parquet")

    def test_unwritable_path_reports_a_recorder_error(self):
        with self.assertRaises(RecorderError):
            Recorder(os.path.join(self.dir, "no-such-dir", "x.csv"))

    def test_close_is_idempotent(self):
        rec = Recorder(self.path)
        rec.close()
        rec.close()
        self.assertTrue(rec.closed)

    def test_writes_after_close_do_not_raise(self):
        rec = Recorder(self.path)
        rec.close()
        rec.write_values({"a": 1.0}, 0.0)


if __name__ == "__main__":
    unittest.main()
