"""Braille-cell drawing surface.

A terminal cell can hold one Unicode braille glyph (U+2800..U+28FF), and each
glyph encodes a 2x4 grid of dots. That gives us eight addressable subpixels per
character cell, so an 80x24 terminal becomes a 160x96 bitmap -- enough
resolution to draw smooth waveforms without any GUI toolkit.

Dot numbering in the Unicode block is historical rather than raster order::

    (0,0) 1  4 (1,0)
    (0,1) 2  5 (1,1)
    (0,2) 3  6 (1,2)
    (0,3) 7  8 (1,3)

so the bit offsets are looked up from a table rather than computed.
"""

from __future__ import annotations

BRAILLE_BASE = 0x2800

CELL_W = 2
CELL_H = 4

# _DOT_BITS[x][y] -> bit to set within the braille glyph.
_DOT_BITS = (
    (0x01, 0x02, 0x04, 0x40),  # left column:  dots 1,2,3,7
    (0x08, 0x10, 0x20, 0x80),  # right column: dots 4,5,6,8
)

# Which subpixel row each bit lives on, for the ASCII fallback.
_BIT_ROWS = {
    0x01: 0,
    0x08: 0,
    0x02: 1,
    0x10: 1,
    0x04: 2,
    0x20: 2,
    0x40: 3,
    0x80: 3,
}


def _build_ascii_table() -> tuple[str, ...]:
    """One ASCII character per 8-dot pattern, for terminals without braille.

    A trace is a thin line, so vertical *position* within the cell carries
    almost all the meaning and density carries almost none. Each character is
    therefore chosen by where the lit dots sit: high in the cell, low in it,
    or spanning it.
    """
    by_row = "\"'.,"  # rows 0..3, from the top of the cell downwards
    table = []
    for pattern in range(256):
        rows = {row for bit, row in _BIT_ROWS.items() if pattern & bit}
        if not rows:
            table.append(" ")
            continue
        span = max(rows) - min(rows)
        if span >= 2:
            table.append("|")
        elif span == 1:
            table.append(":")
        else:
            table.append(by_row[min(rows)])
    return tuple(table)


ASCII_TABLE = _build_ascii_table()

#: Rendering modes for :meth:`Canvas.rows`.
BRAILLE = "braille"
ASCII = "ascii"


class Canvas:
    """A monochrome-per-cell braille bitmap with an attached colour layer.

    Dots are addressed in subpixel coordinates with the origin at the top-left.
    Each character cell also carries a single colour index; when two series
    cross inside one cell the last one drawn wins, which is the same
    compromise every terminal plotter makes.
    """

    __slots__ = (
        "_cells",
        "_colors",
        "charset",
        "dot_height",
        "dot_width",
        "height",
        "width",
    )

    def __init__(self, width: int, height: int, *, charset: str = BRAILLE) -> None:
        if width <= 0 or height <= 0:
            raise ValueError(f"canvas must be at least 1x1, got {width}x{height}")
        if charset not in (BRAILLE, ASCII):
            raise ValueError(f"unknown charset: {charset!r}")
        self.charset = charset
        self.width = width
        self.height = height
        self.dot_width = width * CELL_W
        self.dot_height = height * CELL_H
        self._cells = bytearray(width * height)
        self._colors = [None] * (width * height)

    def clear(self) -> None:
        """Reset every cell to blank with no colour."""
        self._cells = bytearray(self.width * self.height)
        self._colors = [None] * (self.width * self.height)

    def set(self, x: int, y: int, color: int | None = None) -> bool:
        """Light the dot at subpixel ``(x, y)``.

        Returns False when the point falls outside the canvas. Callers plot
        unclipped data all the time, so this is a normal outcome rather than
        an error.
        """
        if not (0 <= x < self.dot_width and 0 <= y < self.dot_height):
            return False
        cx, cy = x // CELL_W, y // CELL_H
        idx = cy * self.width + cx
        self._cells[idx] |= _DOT_BITS[x % CELL_W][y % CELL_H]
        if color is not None:
            self._colors[idx] = color
        return True

    def line(self, x0: int, y0: int, x1: int, y1: int, color: int | None = None) -> None:
        """Draw a straight line between two subpixel points (Bresenham).

        Consecutive samples are joined with lines rather than left as loose
        dots; at high sample rates the difference is invisible, but for slow
        telemetry it is the difference between a waveform and confetti.
        """
        dx = abs(x1 - x0)
        dy = -abs(y1 - y0)
        sx = 1 if x0 < x1 else -1
        sy = 1 if y0 < y1 else -1
        err = dx + dy
        while True:
            self.set(x0, y0, color)
            if x0 == x1 and y0 == y1:
                break
            err2 = 2 * err
            if err2 >= dy:
                # Guard against the degenerate vertical case looping forever.
                if x0 == x1:
                    break
                err += dy
                x0 += sx
            if err2 <= dx:
                if y0 == y1:
                    break
                err += dx
                y0 += sy

    def hline(self, y: int, color: int | None = None) -> None:
        """Light every dot on one horizontal subpixel row."""
        for x in range(self.dot_width):
            self.set(x, y, color)

    def rows(self) -> list[list[tuple[str, int | None]]]:
        """Render to ``(glyph, colour)`` pairs, one list per terminal row."""
        ascii_mode = self.charset == ASCII
        out = []
        for cy in range(self.height):
            row = []
            base = cy * self.width
            for cx in range(self.width):
                idx = base + cx
                pattern = self._cells[idx]
                glyph = ASCII_TABLE[pattern] if ascii_mode else chr(BRAILLE_BASE + pattern)
                row.append((glyph, self._colors[idx]))
            out.append(row)
        return out

    def to_text(self) -> str:
        """Render to a plain string, dropping colour. Used by tests and ``--once``."""
        return "\n".join("".join(glyph for glyph, _ in row) for row in self.rows())
