"""Canvas tests: dot addressing, line drawing, and the ASCII fallback."""

from __future__ import annotations

import unittest

from termscope.canvas import ASCII, ASCII_TABLE, BRAILLE_BASE, Canvas


class TestGeometry(unittest.TestCase):
    def test_dot_dimensions(self):
        c = Canvas(10, 4)
        self.assertEqual((c.dot_width, c.dot_height), (20, 16))

    def test_rejects_empty(self):
        with self.assertRaises(ValueError):
            Canvas(0, 4)
        with self.assertRaises(ValueError):
            Canvas(4, -1)

    def test_rejects_unknown_charset(self):
        with self.assertRaises(ValueError):
            Canvas(4, 4, charset="emoji")


class TestDots(unittest.TestCase):
    def test_blank_canvas_is_all_blank_braille(self):
        c = Canvas(3, 1)
        self.assertEqual(c.to_text(), chr(BRAILLE_BASE) * 3)

    def test_each_dot_sets_a_distinct_bit(self):
        # All eight dots in one cell must produce the full glyph; if two
        # coordinates mapped to the same bit this would come up short.
        c = Canvas(1, 1)
        for x in range(2):
            for y in range(4):
                c.set(x, y)
        self.assertEqual(c.to_text(), chr(BRAILLE_BASE + 0xFF))

    def test_out_of_bounds_returns_false_and_draws_nothing(self):
        c = Canvas(2, 1)
        self.assertFalse(c.set(-1, 0))
        self.assertFalse(c.set(0, 99))
        self.assertFalse(c.set(4, 0))
        self.assertEqual(c.to_text(), chr(BRAILLE_BASE) * 2)

    def test_clear_resets_dots_and_colour(self):
        c = Canvas(2, 1)
        c.set(0, 0, 3)
        c.clear()
        self.assertEqual(c.to_text(), chr(BRAILLE_BASE) * 2)
        self.assertIsNone(c.rows()[0][0][1])

    def test_colour_is_recorded_per_cell(self):
        c = Canvas(2, 1)
        c.set(0, 0, 5)
        rows = c.rows()
        self.assertEqual(rows[0][0][1], 5)
        self.assertIsNone(rows[0][1][1])

    def test_last_writer_owns_a_shared_cell(self):
        c = Canvas(1, 1)
        c.set(0, 0, 1)
        c.set(1, 3, 2)
        self.assertEqual(c.rows()[0][0][1], 2)


class TestLines(unittest.TestCase):
    def test_horizontal_line_fills_every_column(self):
        c = Canvas(4, 1)
        c.line(0, 0, 7, 0)
        for glyph, _ in c.rows()[0]:
            self.assertNotEqual(glyph, chr(BRAILLE_BASE))

    def test_vertical_line_terminates(self):
        # A vertical run is the case where a naive Bresenham loop never hits
        # its end condition and spins forever.
        c = Canvas(1, 4)
        c.line(0, 0, 0, 15)
        self.assertEqual(len(c.rows()), 4)
        for row in c.rows():
            self.assertNotEqual(row[0][0], chr(BRAILLE_BASE))

    def test_single_point_line(self):
        c = Canvas(1, 1)
        c.line(0, 0, 0, 0)
        self.assertEqual(c.to_text(), chr(BRAILLE_BASE + 0x01))

    def test_line_is_symmetric(self):
        forward, backward = Canvas(8, 4), Canvas(8, 4)
        forward.line(0, 0, 15, 15)
        backward.line(15, 15, 0, 0)
        self.assertEqual(forward.to_text(), backward.to_text())

    def test_line_clips_without_raising(self):
        c = Canvas(4, 2)
        c.line(-50, -50, 50, 50)
        self.assertEqual(len(c.to_text().split("\n")), 2)

    def test_hline_spans_the_canvas(self):
        c = Canvas(5, 1)
        c.hline(0)
        self.assertNotIn(chr(BRAILLE_BASE), c.to_text())


class TestAsciiFallback(unittest.TestCase):
    def test_table_covers_every_pattern(self):
        self.assertEqual(len(ASCII_TABLE), 256)
        self.assertEqual(ASCII_TABLE[0], " ")

    def test_output_is_pure_ascii(self):
        c = Canvas(6, 3, charset=ASCII)
        c.line(0, 0, 11, 11)
        text = c.to_text()
        text.encode("ascii")  # raises if any glyph escaped the ASCII range

    def test_span_within_a_cell_reads_as_a_vertical_stroke(self):
        c = Canvas(1, 1, charset=ASCII)
        c.set(0, 0)
        c.set(0, 3)
        self.assertEqual(c.to_text(), "|")

    def test_single_dot_position_maps_to_a_distinct_glyph(self):
        glyphs = []
        for y in range(4):
            c = Canvas(1, 1, charset=ASCII)
            c.set(0, y)
            glyphs.append(c.to_text())
        self.assertEqual(len(set(glyphs)), 4)


if __name__ == "__main__":
    unittest.main()
