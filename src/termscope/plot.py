"""Compose a frame: axes, gridlines, traces, legend.

Two layout modes, and the difference between them is deliberate:

``shared``
    every channel on one y-axis, so relative magnitudes are honest.
``split``
    one stacked panel per channel, each with its own axis -- small multiples.

There is no third mode where two channels get two different y-scales on the
same plot. That is the classic dual-axis chart, and the alignment between the
two scales is arbitrary, so it invents correlations that are not in the data.
When channels differ in magnitude the answer is ``split``, not a second axis.

Drawing order inside the plot area is gridlines, then the zero rule, then
traces. Braille cells carry one colour each, so whatever is drawn last owns
the cell -- painting data last means a gridline never steals a trace's colour.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .buffers import ChannelSet
from .canvas import BRAILLE, CELL_H, Canvas
from .palette import Palette

__all__ = ["Layout", "Renderer", "format_value", "nice_ticks"]


def _format_seconds(seconds: float) -> str:
    """Compact duration for the x-axis band: ``250ms``, ``10s``, ``2m30s``."""
    if seconds < 1:
        return f"{seconds * 1000:.0f}ms"
    if seconds < 60:
        text = f"{seconds:.1f}".rstrip("0").rstrip(".")
        return f"{text}s"
    minutes, rest = divmod(int(seconds), 60)
    return f"{minutes}m{rest:02d}s" if rest else f"{minutes}m"


# Widest y-axis label we will render, including sign and decimals.
GUTTER_MAX = 10
GUTTER_MIN = 6


def format_value(value: float, *, width: int = 8) -> str:
    """Render a number compactly enough for an axis tick or a legend cell.

    Switches to scientific notation only when a fixed-point rendering would
    not fit, so ordinary telemetry stays readable as plain decimals.
    """
    if value != value:  # NaN
        return "nan"
    if value in (float("inf"), float("-inf")):
        return "inf" if value > 0 else "-inf"
    magnitude = abs(value)
    if magnitude != 0 and (magnitude >= 10**width or magnitude < 10 ** -(width - 3)):
        return f"{value:.{max(1, width - 7)}e}"
    if magnitude >= 1000:
        text = f"{value:.0f}"
    elif magnitude >= 100:
        text = f"{value:.1f}"
    elif magnitude >= 10:
        text = f"{value:.2f}"
    elif magnitude >= 1:
        text = f"{value:.3f}"
    else:
        text = f"{value:.4f}"
    if len(text) > width:
        text = f"{value:.{max(1, width - 7)}e}"
    return text


def format_tick(value: float, step: float, *, width: int = 9) -> str:
    """Format an axis tick with the precision the *step* justifies.

    Formatting each tick independently is what produces the ragged
    ``0.0000 / 5.000 / 10.00`` column seen on many terminal plots: the same
    axis ends up with a different number of decimals on every row. Deriving
    the precision from the step instead gives one consistent column.
    """
    if not math.isfinite(value) or not math.isfinite(step) or step <= 0:
        return format_value(value, width=width)
    decimals = max(0, min(6, -math.floor(math.log10(step))))
    if abs(value) >= 10**width or (value != 0 and abs(value) < 10**-6):
        return format_value(value, width=width)
    text = f"{value:.{decimals}f}"
    if text in ("-0", "-0.0", "-0.00", "-0.000", "-0.0000"):
        text = text[1:]
    return text if len(text) <= width else format_value(value, width=width)


def tick_step(ticks: list[float]) -> float:
    """Spacing between ticks, for choosing label precision."""
    if len(ticks) < 2:
        return 0.0
    return abs(ticks[1] - ticks[0])


def nice_ticks(lo: float, hi: float, target: int = 5) -> list[float]:
    """Tick positions on a 1/2/5-times-power-of-ten lattice.

    Ticks land on values a person would choose (0.5, 10, 250) rather than on
    whatever the data range divides into, which is what makes an axis
    readable at a glance.
    """
    if not math.isfinite(lo) or not math.isfinite(hi):
        return []
    if hi < lo:
        lo, hi = hi, lo
    span = hi - lo
    if span <= 0:
        return [lo]
    target = max(2, target)
    raw_step = span / target
    exponent = math.floor(math.log10(raw_step))
    base = 10.0**exponent
    for multiple in (1, 2, 2.5, 5, 10):
        step = multiple * base
        if raw_step <= step:
            break
    first = math.ceil(lo / step) * step
    ticks = []
    value = first
    # Bound the loop independently of floating-point drift near the top end.
    for _ in range(target * 4 + 4):
        if value > hi + step * 1e-9:
            break
        # Snap values that are a hair off an exact multiple (e.g. 0.30000004).
        ticks.append(0.0 if abs(value) < step * 1e-9 else value)
        value += step
    return ticks


@dataclass
class Layout:
    """Where each band of the frame lives, in terminal rows and columns."""

    width: int
    height: int
    gutter: int
    plot_top: int
    plot_height: int
    legend_rows: int

    @property
    def plot_width(self) -> int:
        return max(1, self.width - self.gutter)


class Renderer:
    """Turns a :class:`~termscope.buffers.ChannelSet` into styled text rows."""

    def __init__(self, palette: Palette, *, charset: str = BRAILLE) -> None:
        self.palette = palette
        #: ``braille`` for full 2x4 resolution, ``ascii`` where the output
        #: encoding cannot carry the braille block.
        self.charset = charset
        # Chrome follows the same capability test as the plot glyphs. An
        # encoding that cannot carry braille generally cannot carry box-draw
        # characters or bullets either, so they move together.
        unicode_ok = charset == BRAILLE
        self.rule_char = "─" if unicode_ok else "-"
        self.marker_on = "●" if unicode_ok else "*"
        self.marker_off = "○" if unicode_ok else "."

    # -- layout ------------------------------------------------------------

    def layout(self, width: int, height: int, channel_count: int) -> Layout:
        """Split the terminal into header, plot, x-axis band and legend.

        The x-axis band and legend get their rows before the plot does. A plot
        that fills the window and pushes its own axis labels out of view is a
        common and self-defeating bug; here the plot takes what is left.
        """
        legend_rows = 1 if channel_count <= 4 else 2 if channel_count <= 8 else 3
        legend_rows = min(legend_rows, max(1, height - 6))
        # header(1) + x-axis band(1) + status(1) + legend
        chrome = 3 + legend_rows
        plot_height = max(2, height - chrome)
        return Layout(
            width=width,
            height=height,
            gutter=self._gutter_width(width),
            plot_top=1,
            plot_height=plot_height,
            legend_rows=legend_rows,
        )

    @staticmethod
    def _gutter_width(width: int) -> int:
        if width < 40:
            return GUTTER_MIN
        return min(GUTTER_MAX, max(GUTTER_MIN, width // 10))

    # -- geometry ----------------------------------------------------------

    @staticmethod
    def pad_range(lo: float, hi: float) -> tuple[float, float]:
        """Add headroom so a trace never rides flush against the frame.

        A flat signal has zero span; without a synthetic range it would map to
        a single row and look like a fault rather than a steady reading.
        """
        if not (math.isfinite(lo) and math.isfinite(hi)):
            return -1.0, 1.0
        if hi - lo < 1e-12:
            magnitude = max(abs(lo), 1.0) * 0.05
            return lo - magnitude, hi + magnitude
        pad = (hi - lo) * 0.08
        return lo - pad, hi + pad

    # -- drawing -----------------------------------------------------------

    def _draw_traces(
        self,
        canvas: Canvas,
        channels: ChannelSet,
        names: list[str],
        lo: float,
        hi: float,
        window: float,
        now: float,
        *,
        x_mode: str,
    ) -> dict[str, tuple[int, int]]:
        """Plot each named channel; return the endpoint dot of each trace."""
        span = hi - lo
        if span <= 0:
            span = 1.0
        endpoints: dict[str, tuple[int, int]] = {}
        max_y = canvas.dot_height - 1
        max_x = canvas.dot_width - 1

        for name in names:
            buf = channels[name]
            color = buf.color_index
            if x_mode == "sample":
                values = buf.values(canvas.dot_width)
                if not values:
                    continue
                count = len(values)
                # Right-align: the newest sample owns the rightmost column.
                points = [
                    (max_x - (count - 1 - i), self._to_dot_y(v, lo, span, max_y))
                    for i, v in enumerate(values)
                ]
            else:
                timestamps, values = buf.window()
                if not values:
                    continue
                start = now - window
                points = []
                for ts, value in zip(timestamps, values):
                    if ts < start:
                        continue
                    frac = (ts - start) / window if window > 0 else 1.0
                    x = round(frac * max_x)
                    points.append((x, self._to_dot_y(value, lo, span, max_y)))
                if not points:
                    continue

            prev = points[0]
            canvas.set(prev[0], prev[1], color)
            for point in points[1:]:
                canvas.line(prev[0], prev[1], point[0], point[1], color)
                prev = point
            endpoints[name] = prev
        return endpoints

    @staticmethod
    def _to_dot_y(value: float, lo: float, span: float, max_y: int) -> int:
        frac = (value - lo) / span
        # Clamp rather than clip: a spike beyond the axis should pin to the
        # edge so the reader sees that it went off-scale.
        frac = min(1.0, max(0.0, frac))
        return round((1.0 - frac) * max_y)

    #: Subpixel columns between grid dots. Wide enough that the grid reads as
    #: a faint ruler behind the data rather than as content of its own -- at
    #: every fourth column it competes with the traces for attention.
    GRID_PITCH = 10
    ZERO_PITCH = 4

    def _draw_grid(self, canvas: Canvas, ticks: list[float], lo: float, hi: float) -> None:
        """Recessive hairlines at each tick, one shade off the background.

        Drawn before the traces so data always wins the cell. Grid dots use
        colour slot -1, which the palette renders as muted grey.
        """
        span = hi - lo
        if span <= 0:
            return
        max_y = canvas.dot_height - 1
        for tick in ticks:
            # Zero gets its own denser rule; skip it here so the two do not
            # overprint into a heavier line than either intends.
            if lo < 0.0 < hi and abs(tick) < span * 1e-9:
                continue
            y = self._to_dot_y(tick, lo, span, max_y)
            for x in range(0, canvas.dot_width, self.GRID_PITCH):
                canvas.set(x, y, -1)

    def _draw_zero(self, canvas: Canvas, lo: float, hi: float) -> None:
        """A denser rule at y=0 when the range straddles it.

        Zero is the one gridline worth emphasising on a scope: it is where
        a signal changes sign.
        """
        if not (lo < 0.0 < hi):
            return
        span = hi - lo
        y = self._to_dot_y(0.0, lo, span, canvas.dot_height - 1)
        for x in range(0, canvas.dot_width, self.ZERO_PITCH):
            canvas.set(x, y, -1)

    # -- frame assembly ----------------------------------------------------

    def render_plot_rows(
        self,
        channels: ChannelSet,
        names: list[str],
        layout: Layout,
        lo: float,
        hi: float,
        *,
        window: float,
        now: float,
        x_mode: str,
        show_grid: bool = True,
    ) -> list[str]:
        """The plot area: y-axis labels in the gutter, canvas to the right."""
        canvas = Canvas(layout.plot_width, layout.plot_height, charset=self.charset)
        # Roughly one tick every three rows, capped: a label on every other
        # row turns the gutter into a wall of numbers and the plot into grid.
        ticks = nice_ticks(lo, hi, target=max(2, min(6, layout.plot_height // 3)))

        if show_grid:
            self._draw_grid(canvas, ticks, lo, hi)
        self._draw_zero(canvas, lo, hi)
        endpoints = self._draw_traces(
            canvas, channels, names, lo, hi, window, now, x_mode=x_mode
        )

        # Mark the newest sample of each trace so the eye can tie a line to
        # its legend entry without following it off the right edge.
        for name, (x, y) in endpoints.items():
            buf = channels[name]
            for dx in (-1, 0):
                for dy in (-1, 0, 1):
                    canvas.set(x + dx, y + dy, buf.color_index)

        label_rows = self._gutter_labels(ticks, lo, hi, layout)
        rows = []
        for row_index, cells in enumerate(canvas.rows()):
            gutter = label_rows.get(row_index, " " * layout.gutter)
            rows.append(self._style_gutter(gutter) + self._style_cells(cells))
        return rows

    def render_split_rows(
        self,
        channels: ChannelSet,
        names: list[str],
        layout: Layout,
        *,
        window: float,
        now: float,
        x_mode: str,
        sample_window: int | None = None,
        ylim: tuple[float, float] | None = None,
    ) -> list[str]:
        """Small multiples: one stacked panel per channel, each on its own axis.

        This is the honest answer to channels of wildly different magnitude --
        a battery in volts beside a motor command in percent. Each panel is
        independently scaled, and because the panels are physically separate
        no reader can mistake one axis for the other.
        """
        if not names:
            return [" " * layout.width for _ in range(layout.plot_height)]

        panels = min(len(names), max(1, layout.plot_height // 2))
        shown = names[:panels]
        base = layout.plot_height // panels
        extra = layout.plot_height - base * panels

        rows: list[str] = []
        for i, name in enumerate(shown):
            # Spread the remainder over the top panels so no row is wasted.
            panel_height = base + (1 if i < extra else 0)
            if panel_height <= 0:
                continue
            buf = channels[name]
            stats = buf.stats(sample_window)
            if ylim is not None:
                lo, hi = ylim
            elif stats is None:
                lo, hi = -1.0, 1.0
            else:
                lo, hi = self.pad_range(stats.minimum, stats.maximum)
            sub = Layout(
                width=layout.width,
                height=panel_height,
                gutter=layout.gutter,
                plot_top=0,
                plot_height=panel_height,
                legend_rows=0,
            )
            panel_rows = self.render_plot_rows(
                channels,
                [name],
                sub,
                lo,
                hi,
                window=window,
                now=now,
                x_mode=x_mode,
                show_grid=panel_height >= 3,
            )
            panel_rows[0] = self._overlay_panel_title(panel_rows[0], name, buf, layout)
            rows.extend(panel_rows)
        return rows[: layout.plot_height]

    def _overlay_panel_title(self, row: str, name: str, buf, layout: Layout) -> str:
        """Prefix a split panel with its channel name.

        The name replaces the gutter's top tick label rather than floating
        over the plot, so it can never sit on top of the data.
        """
        pal = self.palette
        label = name[: layout.gutter - 1].ljust(layout.gutter - 1) + " "
        swatch = self._color_for(buf.color_index)
        styled = f"{swatch}{label}{pal.reset()}" if pal.enabled else label
        # Drop the original gutter, which occupies the first `gutter` visible
        # columns; with colour on, that is the first styled span.
        if pal.enabled:
            reset = pal.reset()
            idx = row.find(reset)
            remainder = row[idx + len(reset) :] if idx >= 0 else row
        else:
            remainder = row[layout.gutter :]
        return styled + remainder

    def _gutter_labels(
        self, ticks: list[float], lo: float, hi: float, layout: Layout
    ) -> dict[int, str]:
        """Map terminal row -> right-aligned tick label.

        Two ticks can round into the same character row on a short plot; the
        first one there keeps it rather than overwriting, so labels stay in
        ascending order down the gutter.
        """
        span = hi - lo
        if span <= 0:
            return {}
        max_dot_y = layout.plot_height * CELL_H - 1
        step = tick_step(ticks)
        out: dict[int, str] = {}
        for tick in ticks:
            row = min(
                layout.plot_height - 1,
                self._to_dot_y(tick, lo, span, max_dot_y) // CELL_H,
            )
            if row in out:
                continue
            text = format_tick(tick, step, width=layout.gutter - 1)
            out[row] = text.rjust(layout.gutter - 1) + " "
        return out

    def _style_gutter(self, text: str) -> str:
        pal = self.palette
        if not pal.enabled:
            return text
        return f"{pal.muted()}{text}{pal.reset()}"

    def _color_for(self, index: int | None) -> str:
        """Escape code for a cell's colour slot; grid and chrome use muted."""
        if index is None or index < 0:
            return self.palette.muted()
        return self.palette.series_fg(index)

    # -- chrome ------------------------------------------------------------

    def render_header(self, title: str, right: str, width: int) -> str:
        """Title bar: source on the left, live state on the right."""
        pal = self.palette
        room = max(0, width - len(right) - 1)
        left = title[:room]
        pad = " " * max(1, width - len(left) - len(right))
        if not pal.enabled:
            return (left + pad + right)[:width]
        return f"{pal.bold()}{left}{pal.reset()}{pad}{pal.muted()}{right}{pal.reset()}"

    def render_xaxis(
        self, layout: Layout, *, window: float, x_mode: str, sample_count: int
    ) -> str:
        """The x-axis band under the plot.

        Always drawn as its own row. A plot that expands to fill the window
        and pushes its axis labels out of sight is a real and common bug; the
        band owning a row of its own is what prevents it.
        """
        pal = self.palette
        if x_mode == "sample":
            left, right = f"-{sample_count} samples", "now"
        else:
            left, right = f"-{_format_seconds(window)}", "now"
        inner = layout.plot_width
        rule = self.rule_char * max(0, inner - len(left) - len(right))
        band = " " * layout.gutter + left + rule + right
        band = band[: layout.width]
        if not pal.enabled:
            return band
        return f"{pal.axis()}{band}{pal.reset()}"

    def render_legend(
        self,
        channels: ChannelSet,
        layout: Layout,
        *,
        sample_window: int | None = None,
        show_stats: bool = False,
    ) -> list[str]:
        """One entry per channel: colour swatch, name, current value.

        This is also the accessibility relief for the palette. Several hues
        sit below 3:1 against a light background, which is permitted only
        because every value is legible as text right here -- colour marks
        which trace is which, it never carries the reading itself.
        """
        pal = self.palette
        entries: list[str] = []
        plain: list[str] = []
        for index, name in enumerate(channels.names):
            buf = channels[name]
            summary = buf.stats(sample_window)
            value = format_value(summary.last, width=8) if summary else "--"
            extra = ""
            if show_stats and summary is not None:
                extra = (
                    f" min {format_value(summary.minimum, width=7)}"
                    f" max {format_value(summary.maximum, width=7)}"
                    f" mean {format_value(summary.mean, width=7)}"
                )
            key = str(index + 1) if index < 9 else " "
            marker = self.marker_on if buf.visible else self.marker_off
            text = f"{key}{marker}{name} {value}{extra}"
            plain.append(text)
            if not pal.enabled:
                entries.append(text)
                continue
            if buf.visible:
                swatch = f"{self._color_for(buf.color_index)}{marker}{pal.reset()}"
                entries.append(f"{pal.muted()}{key}{pal.reset()}{swatch}{name} {value}")
            else:
                entries.append(
                    f"{pal.dim()}{pal.muted()}{key}{marker}{name} {value}{pal.reset()}"
                )
        return self._wrap_entries(entries, plain, layout.width, layout.legend_rows)

    @staticmethod
    def _wrap_entries(
        styled: list[str], plain: list[str], width: int, max_rows: int
    ) -> list[str]:
        """Pack legend entries into rows, measuring visible width only.

        Styled strings carry escape sequences that occupy no columns, so the
        plain twin is what gets measured. Anything that will not fit is
        reported as a count instead of being silently dropped.
        """
        rows: list[str] = []
        current: list[str] = []
        used = 0
        gap = 3
        dropped = 0
        for styled_entry, plain_entry in zip(styled, plain):
            need = len(plain_entry) + (gap if current else 0)
            if used + need > width:
                if len(rows) + 1 >= max_rows and current:
                    dropped += 1
                    continue
                if current:
                    rows.append((" " * gap).join(current))
                    current, used = [], 0
                    need = len(plain_entry)
                if need > width:
                    dropped += 1
                    continue
            current.append(styled_entry)
            used += need
        if current:
            rows.append((" " * gap).join(current))
        if dropped and rows:
            note = f" +{dropped} more"
            rows[-1] = rows[-1] + note
        while len(rows) < max_rows:
            rows.append("")
        return rows[:max_rows]

    def render_status(self, text: str, width: int, *, highlight: bool = False) -> str:
        pal = self.palette
        body = text[:width]
        if not pal.enabled:
            return body
        if highlight:
            return f"{pal.reverse()}{body}{pal.reset()}"
        return f"{pal.muted()}{body}{pal.reset()}"

    def _style_cells(self, cells) -> str:
        """Emit a canvas row, switching colour only where it changes.

        Re-emitting an SGR sequence for every cell would several times over
        the bytes written each frame; on a wide terminal at 30 fps that is
        the difference between a smooth trace and visible tearing.
        """
        pal = self.palette
        if not pal.enabled:
            return "".join(glyph for glyph, _ in cells)
        parts: list[str] = []
        current: object = object()  # sentinel: never equal to a real slot
        for glyph, color in cells:
            if color != current:
                parts.append(self._color_for(color))
                current = color
            parts.append(glyph)
        parts.append(pal.reset())
        return "".join(parts)
