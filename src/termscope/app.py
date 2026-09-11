"""The interactive scope: input, state, and the render loop.

The loop is deliberately boring -- drain, parse, store, draw, sleep -- because
everything that could block has already been pushed onto a source thread. One
pass never waits on anything, so the frame rate is set by the redraw interval
rather than by how talkative the device happens to be.

Frames are painted with cursor-home plus erase-to-end-of-line per row instead
of a full-screen clear. Clearing first is what makes terminal UIs flicker: for
one refresh the screen is genuinely blank.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from .buffers import ChannelSet
from .canvas import ASCII, BRAILLE
from .palette import MAX_SERIES, Palette
from .parser import StreamParser
from .plot import Layout, Renderer
from .recorder import Recorder, RecorderError
from .sources import Source
from .term import ESC, Terminal, ensure_utf8
from .timebase import TimeBase
from .trigger import TriggerSpec, TriggerWatch

__all__ = ["App", "Options", "resolve_charset"]


def resolve_charset(requested: str | None) -> str:
    """Decide between braille and ASCII output.

    An explicit request is honoured. Otherwise we try to put the output
    stream into UTF-8 and use braille if that works, because the usual reason
    it does not is a locale encoding rather than a terminal that cannot draw
    the glyph.
    """
    if requested in (BRAILLE, ASCII):
        return requested
    return BRAILLE if ensure_utf8() else ASCII


CLEAR_LINE = f"{ESC}[K"
CURSOR_HOME = f"{ESC}[H"
CLEAR_BELOW = f"{ESC}[J"

HELP_LINES = [
    "termscope - keys",
    "",
    "  space    hold / resume        a    autoscale on / off",
    "           (also rearms a --trigger after it fires)",
    "  1-8      toggle one channel   0    show every channel",
    "  m        shared / split axes  t    x-axis: time / samples",
    "  [  ]     shorter / longer window",
    "  +  -     zoom y  (when autoscale is off)",
    "  up/down  pan y   (when autoscale is off)",
    "  g        gridlines            v    raw text view",
    "  s        min/max/mean in legend",
    "  r        start / stop recording",
    "  c        clear buffers        q    quit",
    "",
    "  press any key to close this help",
]


@dataclass
class Options:
    """Everything the CLI can set on a session."""

    window: float = 10.0
    capacity: int = 8192
    fps: float = 30.0
    x_mode: str = "time"  # "time" | "sample"
    split: bool = False
    grid: bool = True
    light: bool = False
    color_depth: str | None = None
    prefix: str | None = None
    max_channels: int = 32
    record_path: str | None = None
    record_mode: str = "csv"
    force_overwrite: bool = False
    raw_view: bool = False
    charset: str | None = None  # None = detect from the output encoding
    time_column: str | None = None
    auto_time_column: bool = True
    only: list[str] = field(default_factory=list)
    ylim: tuple[float, float] | None = None
    stats: bool = False
    trigger: TriggerSpec | None = None
    smooth: int = 1


class App:
    """Owns the session state and drives the render loop."""

    def __init__(self, source: Source, options: Options | None = None) -> None:
        self.source = source
        self.opt = options or Options()
        self.palette = Palette(dark=not self.opt.light, depth=self.opt.color_depth)
        self.renderer = Renderer(self.palette, charset=resolve_charset(self.opt.charset))
        self.parser = StreamParser(prefix=self.opt.prefix, max_channels=self.opt.max_channels)
        self.channels = ChannelSet(
            capacity=self.opt.capacity,
            max_channels=self.opt.max_channels,
            color_slots=MAX_SERIES,
            smooth=self.opt.smooth,
        )
        self.timebase = TimeBase(self.opt.time_column, auto=self.opt.auto_time_column)
        self.recorder: Recorder | None = None
        self._watch = TriggerWatch(self.opt.trigger) if self.opt.trigger else None

        self.running = True
        self.paused = False
        self.autoscale = self.opt.ylim is None
        self.show_help = False
        self.held_range: tuple[float, float] | None = self.opt.ylim
        self.dropped_while_paused = 0
        self.status_message = ""
        self.status_until = 0.0
        self.raw_lines: list[str] = []
        self.started = time.monotonic()
        self._last_rate_check = self.started
        self._last_rate_count = 0
        self.sample_rate = 0.0
        self._error: str | None = None

    # -- lifecycle ---------------------------------------------------------

    def run(self) -> int:
        """Run until the user quits or the source ends. Returns an exit code."""
        if self.opt.record_path:
            try:
                self.recorder = Recorder(
                    self.opt.record_path,
                    mode=self.opt.record_mode,
                    overwrite=self.opt.force_overwrite,
                )
            except (RecorderError, ValueError) as exc:
                print(f"termscope: {exc}")
                return 2

        self.source.start()
        frame_interval = 1.0 / max(1.0, self.opt.fps)
        next_frame = time.monotonic()
        try:
            with Terminal() as term:
                while self.running:
                    self._handle_keys(term)
                    self._ingest()
                    if self._check_source_error():
                        break
                    now = time.monotonic()
                    if now >= next_frame:
                        self._draw(term)
                        next_frame = now + frame_interval
                    # A short sleep keeps a quiet stream from spinning a core
                    # while staying well inside one frame's budget.
                    time.sleep(min(0.005, frame_interval / 4))
        except KeyboardInterrupt:
            pass
        finally:
            self.source.stop()
            if self.recorder is not None:
                self.recorder.close()

        if self._error:
            print(f"termscope: {self._error}")
            return 1
        self._print_summary()
        return 0

    def _print_summary(self) -> None:
        total = sum(self.channels[n].total for n in self.channels)
        input_drops = self.source.dropped_input_lines
        if not total:
            print("termscope: no samples were parsed.")
            if self.parser.skipped_count:
                print(
                    f"  {self.parser.skipped_count} lines arrived but matched no known "
                    "format.\n"
                    "  Try --prefix to select telemetry lines, or --raw to see the "
                    "stream verbatim."
                )
            if input_drops:
                print(f"  {input_drops} input lines were dropped under overload.")
            return
        elapsed = time.monotonic() - self.started
        print(
            f"termscope: {total} samples across {len(self.channels)} channels in {elapsed:.1f}s"
        )
        if self.recorder is not None:
            print(f"  wrote {self.recorder.rows_written} rows to {self.opt.record_path}")
        if input_drops:
            print(
                f"  {input_drops} input lines were dropped under overload; "
                "newest live data was kept."
            )

    def _check_source_error(self) -> bool:
        exc = self.source.check_error()
        if exc is None:
            return False
        self._error = str(exc)
        return True

    # -- data --------------------------------------------------------------

    def _ingest(self) -> None:
        lines = self.source.drain()
        if not lines:
            self._update_rate()
            return
        if self.paused:
            # Hold means hold: incoming data is counted, not stored, so the
            # frozen trace on screen stays exactly as captured.
            self.dropped_while_paused += len(lines)
            self._update_rate()
            return

        now = time.time()
        for line in lines:
            parsed = self.parser.feed(line)
            if self.recorder is not None and self.opt.record_mode == "raw":
                self.recorder.write_raw(parsed.text or line)
            if self.opt.raw_view and parsed.text:
                self.raw_lines.append(parsed.text)
            if not parsed.samples:
                continue
            for sample in parsed.samples:
                values, stamp = self.timebase.apply(sample, now)
                if self.opt.only:
                    values = {k: v for k, v in values.items() if k in self.opt.only}
                if not values:
                    continue
                self.channels.add(values, stamp)
                if self.recorder is not None and self.opt.record_mode == "csv":
                    self.recorder.write_values(values, stamp)
                if self._watch is not None and self._watch.observe(values):
                    self.paused = True
                    self.dropped_while_paused = 0
                    self._flash(f"triggered {self._watch.spec.describe()}")
                    self._update_rate()
                    return
        if len(self.raw_lines) > 500:
            del self.raw_lines[:-500]
        self._update_rate()

    def _update_rate(self) -> None:
        """Samples per second, averaged over a sliding half-second."""
        now = time.monotonic()
        elapsed = now - self._last_rate_check
        if elapsed < 0.5:
            return
        total = self.parser.parsed_count
        self.sample_rate = (total - self._last_rate_count) / elapsed
        self._last_rate_count = total
        self._last_rate_check = now

    # -- input -------------------------------------------------------------

    def _handle_keys(self, term: Terminal) -> None:
        while True:
            key = term.read_key()
            if key is None:
                return
            if self.show_help:
                # Any key dismisses help, and does nothing else -- so a
                # muscle-memory "q" closes the overlay rather than the app.
                self.show_help = False
                continue
            self._dispatch(key)

    def _dispatch(self, key: str) -> None:
        if key in ("q", "\x03", "\x04", "escape"):
            self.running = False
        elif key == " ":
            self.paused = not self.paused
            if self.paused:
                self.dropped_while_paused = 0
                self._flash("held")
            else:
                if self._watch is not None and self._watch.fired:
                    self._watch.rearm()
                    self._flash("rearmed")
                else:
                    self._flash("running")
        elif key == "a":
            self.autoscale = not self.autoscale
            if not self.autoscale:
                self.held_range = self._current_range()
            self._flash(f"autoscale {'on' if self.autoscale else 'off'}")
        elif key == "g":
            self.opt.grid = not self.opt.grid
        elif key == "s":
            self.opt.stats = not self.opt.stats
            self._flash("legend stats on" if self.opt.stats else "legend stats off")
        elif key == "m":
            self.opt.split = not self.opt.split
            self._flash("split axes" if self.opt.split else "shared axis")
        elif key == "t":
            self.opt.x_mode = "sample" if self.opt.x_mode == "time" else "time"
            self._flash(f"x-axis: {self.opt.x_mode}")
        elif key == "v":
            self.opt.raw_view = not self.opt.raw_view
        elif key == "c":
            self.channels.clear()
            self.raw_lines.clear()
            self._flash("cleared")
        elif key in ("?", "h"):
            self.show_help = True
        elif key == "r":
            self._toggle_recording()
        elif key == "]":
            self.opt.window = min(600.0, self.opt.window * 1.5)
            self._flash(f"window {self.opt.window:.1f}s")
        elif key == "[":
            self.opt.window = max(0.2, self.opt.window / 1.5)
            self._flash(f"window {self.opt.window:.1f}s")
        elif key in ("+", "=", "-", "_"):
            self._zoom(0.8 if key in ("+", "=") else 1.25)
        elif key in ("up", "down"):
            self._pan(-1 if key == "up" else 1)
        elif key == "0":
            for name in self.channels:
                self.channels[name].visible = True
            self._flash("all channels shown")
        elif key.isdigit():
            self._toggle_channel(int(key) - 1)

    def _toggle_channel(self, index: int) -> None:
        names = self.channels.names
        if not 0 <= index < len(names):
            return
        buf = self.channels[names[index]]
        if buf.color_index < 0 and not buf.visible:
            # Beyond the eighth channel there is no colour slot left. Showing
            # it would mean reusing a hue, so say why instead of doing it.
            self._flash(f"{names[index]}: no colour slot free (8 max)")
            return
        buf.visible = not buf.visible

    def _toggle_recording(self) -> None:
        if self.recorder is not None:
            rows = self.recorder.rows_written
            self.recorder.close()
            self.recorder = None
            self._flash(f"recording stopped ({rows} rows)")
            return
        path = self.opt.record_path or time.strftime("termscope-%Y%m%d-%H%M%S.csv")
        try:
            self.recorder = Recorder(path, mode=self.opt.record_mode, overwrite=True)
        except (RecorderError, ValueError) as exc:
            self._flash(str(exc))
            return
        self.opt.record_path = path
        self._flash(f"recording to {path}")

    def _zoom(self, factor: float) -> None:
        if self.autoscale:
            self._flash("press 'a' to turn autoscale off first")
            return
        lo, hi = self.held_range or self._current_range()
        mid = (lo + hi) / 2
        half = (hi - lo) / 2 * factor
        self.held_range = (mid - half, mid + half)

    def _pan(self, direction: int) -> None:
        if self.autoscale:
            self._flash("press 'a' to turn autoscale off first")
            return
        lo, hi = self.held_range or self._current_range()
        step = (hi - lo) * 0.1 * direction
        self.held_range = (lo + step, hi + step)

    def _flash(self, message: str, seconds: float = 1.6) -> None:
        self.status_message = message
        self.status_until = time.monotonic() + seconds

    # -- rendering ---------------------------------------------------------

    def _sample_window(self, layout: Layout) -> int:
        """How many samples the plot can show, for stats and sample x-mode."""
        return max(1, layout.plot_width * 2)

    def _current_range(self) -> tuple[float, float]:
        extent = self.channels.extent()
        if extent is None:
            return -1.0, 1.0
        return self.renderer.pad_range(*extent)

    def _visible_names(self) -> list[str]:
        return [n for n in self.channels.names if self.channels[n].visible]

    def _plot_now(self) -> float:
        """The timestamp at the right-hand edge of the plot.

        With a stream-supplied clock the newest sample *is* the present;
        pinning the edge to wall-clock time would scroll a finished replay
        off the screen.
        """
        if self.timebase.active and self.channels.latest_timestamp is not None:
            return self.channels.latest_timestamp
        return time.time()

    def _draw(self, term: Terminal) -> None:
        width, height = term.size()
        names = self._visible_names()
        layout = self.renderer.layout(width, height, len(self.channels))

        if self.show_help:
            rows = self._help_rows(width, height)
        elif self.opt.raw_view:
            rows = self._raw_rows(layout)
        else:
            rows = self._plot_rows(layout, names)

        out = [CURSOR_HOME]
        for row in rows[:height]:
            out.append(CLEAR_LINE)
            out.append(row)
            out.append("\r\n")
        out.append(CLEAR_BELOW)
        term.write("".join(out))
        term.flush()

    def _plot_rows(self, layout: Layout, names: list[str]) -> list[str]:
        now = self._plot_now()
        rows = [
            self.renderer.render_header(
                self.source.description, self._header_right(), layout.width
            )
        ]

        if not self.channels.names:
            rows.extend(self._waiting_rows(layout))
        elif self.opt.split:
            rows.extend(
                self.renderer.render_split_rows(
                    self.channels,
                    names,
                    layout,
                    window=self.opt.window,
                    now=now,
                    x_mode=self.opt.x_mode,
                    sample_window=self._sample_window(layout),
                    ylim=None if self.autoscale else self.held_range,
                )
            )
        else:
            if self.autoscale:
                extent = self.channels.extent(self._sample_window(layout))
                lo, hi = self.renderer.pad_range(*extent) if extent else (-1.0, 1.0)
                self.held_range = (lo, hi)
            else:
                lo, hi = self.held_range or self._current_range()
            rows.extend(
                self.renderer.render_plot_rows(
                    self.channels,
                    names,
                    layout,
                    lo,
                    hi,
                    window=self.opt.window,
                    now=now,
                    x_mode=self.opt.x_mode,
                    show_grid=self.opt.grid,
                )
            )

        rows.append(
            self.renderer.render_xaxis(
                layout,
                window=self.opt.window,
                x_mode=self.opt.x_mode,
                sample_count=self._sample_window(layout),
            )
        )
        rows.extend(
            self.renderer.render_legend(
                self.channels,
                layout,
                sample_window=self._sample_window(layout),
                show_stats=self.opt.stats,
            )
        )
        rows.append(
            self.renderer.render_status(
                self._status_text(),
                layout.width,
                highlight=time.monotonic() < self.status_until,
            )
        )
        return rows

    def _waiting_rows(self, layout: Layout) -> list[str]:
        """Placeholder while nothing has parsed yet.

        A blank plot is indistinguishable from a broken one, so this states
        what is happening and what to try -- most first runs that "hang" are
        really a baud-rate mismatch or a stream the parser cannot key.
        """
        skipped = self.parser.skipped_count
        lines = ["", "  waiting for data...", ""]
        if skipped:
            lines += [
                f"  {skipped} lines received, none recognised as numbers.",
                "",
                "  If the device prints something like  pitch:1.23  or  1.23,4.56",
                "  it should parse. Otherwise:",
                "    --prefix '>'   only read lines starting with >",
                "    --raw          show the stream verbatim (also the 'v' key)",
                "    --baud N       a wrong baud rate looks like garbage or silence",
            ]
        else:
            lines += ["  Nothing has arrived on this source yet.", ""]
        rows = [line[: layout.width] for line in lines]
        while len(rows) < layout.plot_height:
            rows.append("")
        return rows[: layout.plot_height]

    def _raw_rows(self, layout: Layout) -> list[str]:
        """Verbatim stream view: the escape hatch when parsing goes wrong."""
        rows = [
            self.renderer.render_header(
                f"{self.source.description} - raw", self._header_right(), layout.width
            )
        ]
        body_height = layout.plot_height + 1
        tail = self.raw_lines[-body_height:]
        for line in tail:
            rows.append(line[: layout.width])
        while len(rows) < body_height + 1:
            rows.append("")
        rows.extend(
            self.renderer.render_legend(self.channels, layout, show_stats=self.opt.stats)
        )
        rows.append(self.renderer.render_status(self._status_text(), layout.width))
        return rows

    def _help_rows(self, width: int, height: int) -> list[str]:
        rows = [line[:width] for line in HELP_LINES]
        while len(rows) < height:
            rows.append("")
        return rows[:height]

    def _header_right(self) -> str:
        bits = []
        if self.paused:
            if self._watch is not None and self._watch.fired:
                bits.append(f"TRIG {self._watch.spec.describe()}")
            else:
                bits.append(f"HELD (+{self.dropped_while_paused} dropped)")
        else:
            bits.append(f"{self.sample_rate:.0f}/s")
        if self.recorder is not None:
            bits.append(f"REC {self.recorder.rows_written}")
        input_drops = self.source.dropped_input_lines
        if input_drops:
            bits.append(f"DROP {input_drops}")
        return "  ".join(bits)

    def _status_text(self) -> str:
        if time.monotonic() < self.status_until and self.status_message:
            return f" {self.status_message} "
        input_drops = self.source.dropped_input_lines
        if input_drops:
            return (
                f"  {input_drops} input line(s) dropped under overload; "
                "newest live data kept   ? help"
            )
        overflow = self.channels.overflow_names
        if overflow:
            return (
                f"  {len(overflow)} channel(s) without a colour slot: "
                f"{', '.join(overflow[:3])}  -  8 series max   ? help"
            )
        return (
            "  space hold   a autoscale   m split   t x-axis   [ ] window   "
            "r record   ? help   q quit"
        )
