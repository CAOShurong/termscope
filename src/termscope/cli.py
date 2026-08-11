"""Command-line entry point.

Argument handling leans on one idea: the common case should need no
arguments. Running bare ``termscope`` finds the likeliest board and opens it,
because on a typical machine the port list is one USB bridge and a handful of
Bluetooth modems nobody wants.

Everything else is an escape hatch for when that guess is wrong.
"""

from __future__ import annotations

import argparse
import math
import sys
import time

from . import __version__
from .app import App, Options
from .palette import Palette
from .plot import Renderer
from .sources import (
    DemoSource,
    FileSource,
    SerialSource,
    Source,
    SourceError,
    StdinSource,
    autodetect_port,
    list_ports,
)

EPILOG = """\
examples:
  termscope                       open the auto-detected board at 115200
  termscope COM3 --baud 9600      a specific port and rate
  termscope --demo                a simulated robot; no hardware needed
  pio device monitor | termscope -  plot whatever another tool prints
  termscope --replay capture.csv  replay a recording
  termscope --demo --record run.csv   plot and log at the same time

input formats (detected automatically):
  pitch:1.23 roll:-4.5      key:value or key=value
  1.23, -4.5, 0.02          bare numbers  (Arduino Serial Plotter style)
  {"pitch": 1.23}           one JSON object per line
  >temp:1000:23.5            Teleplot time series (timestamps are milliseconds)
  a CSV header line names the columns for the bare rows that follow
"""


def positive_seconds(value: str) -> float:
    """Parse a strictly positive duration for retry and timing options."""
    try:
        seconds = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a number of seconds") from exc
    if not math.isfinite(seconds) or seconds <= 0:
        raise argparse.ArgumentTypeError("must be a finite number greater than zero")
    return seconds


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="termscope",
        description="A live oscilloscope for serial data, in your terminal.",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "port",
        nargs="?",
        help="serial port (COM3, /dev/ttyUSB0). '-' reads stdin. Omit to auto-detect.",
    )
    parser.add_argument("--version", action="version", version=f"termscope {__version__}")

    source = parser.add_argument_group("source")
    source.add_argument(
        "-b", "--baud", type=int, default=115200, help="baud rate (default: 115200)"
    )
    source.add_argument(
        "--demo",
        action="store_true",
        help="simulate a balancing robot instead of reading hardware",
    )
    source.add_argument("--replay", metavar="FILE", help="replay a previously captured log")
    source.add_argument(
        "--replay-rate",
        type=float,
        default=0.0,
        metavar="LPS",
        help="replay speed in lines/second (default: as fast as possible)",
    )
    source.add_argument("-l", "--list", action="store_true", help="list serial ports and exit")
    source.add_argument(
        "--reconnect",
        nargs="?",
        type=positive_seconds,
        const=1.0,
        default=None,
        metavar="SEC",
        help="keep retrying the same serial port after disconnects "
        "(default interval: 1 second)",
    )

    parsing = parser.add_argument_group("parsing")
    parsing.add_argument(
        "--prefix", metavar="STR", help="only parse lines starting with STR (e.g. '>')"
    )
    parsing.add_argument(
        "--only",
        metavar="NAME",
        action="append",
        default=[],
        help="plot only this channel; repeatable",
    )
    parsing.add_argument(
        "--max-channels",
        type=int,
        default=32,
        metavar="N",
        help="stop tracking new channels past N (default: 32)",
    )
    parsing.add_argument(
        "--time-column",
        metavar="NAME",
        help="plot against this channel as the clock instead "
        "of arrival time (e.g. millis). Auto-detected "
        "for names like time_s, millis, micros.",
    )
    parsing.add_argument(
        "--no-time-column",
        action="store_true",
        help="ignore any timestamp in the stream and use arrival time",
    )

    display = parser.add_argument_group("display")
    display.add_argument(
        "-w",
        "--window",
        type=float,
        default=10.0,
        metavar="SEC",
        help="seconds of history on screen (default: 10)",
    )
    display.add_argument(
        "--samples",
        action="store_true",
        help="index the x-axis by sample number instead of time",
    )
    display.add_argument(
        "--split", action="store_true", help="one panel per channel, each independently scaled"
    )
    display.add_argument("--no-grid", action="store_true", help="hide gridlines")
    display.add_argument(
        "--light", action="store_true", help="colours stepped for a light-background terminal"
    )
    display.add_argument(
        "--color",
        choices=("auto", "truecolor", "256", "16", "none"),
        default="auto",
        help="force a colour depth (default: auto)",
    )
    display.add_argument("--fps", type=float, default=30.0, help="redraw rate (default: 30)")
    display.add_argument(
        "--capacity",
        type=int,
        default=8192,
        metavar="N",
        help="samples kept per channel (default: 8192)",
    )
    display.add_argument(
        "--raw", action="store_true", help="start in raw text view instead of the plot"
    )
    display.add_argument(
        "--charset",
        choices=("auto", "braille", "ascii"),
        default="auto",
        help="plot glyphs; 'ascii' for terminals or locales "
        "that cannot show braille (default: auto)",
    )
    display.add_argument(
        "--once",
        nargs="?",
        type=float,
        const=3.0,
        metavar="SEC",
        help="collect for SEC seconds, print one frame, exit "
        "(default 3; useful for docs and CI)",
    )

    record = parser.add_argument_group("recording")
    record.add_argument(
        "-r", "--record", metavar="FILE", help="write samples to FILE as CSV while plotting"
    )
    record.add_argument(
        "--record-raw",
        action="store_true",
        help="record the unparsed stream instead of parsed samples",
    )
    record.add_argument(
        "--force", action="store_true", help="overwrite the recording file if it exists"
    )
    return parser


def print_ports() -> int:
    ports = list_ports()
    if not ports:
        print("No serial ports found.")
        print("(If you expected some, install pyserial: pip install 'termscope[serial]')")
        return 1
    width = max(len(device) for device, _ in ports)
    print(f"{len(ports)} port(s):")
    for device, label in ports:
        print(f"  {device.ljust(width)}  {label}")
    return 0


def make_source(args: argparse.Namespace) -> Source:
    """Pick a source from the arguments, or raise SourceError explaining why not."""
    if args.demo:
        return DemoSource()
    if args.replay:
        return FileSource(args.replay, rate=args.replay_rate)
    if args.port == "-":
        return StdinSource()
    if args.port:
        return SerialSource(args.port, args.baud, reconnect_interval=args.reconnect)

    if not sys.stdin.isatty():
        # Being on the receiving end of a pipe is an unambiguous signal.
        return StdinSource()

    port = autodetect_port()
    if port is None:
        raise SourceError(
            "No serial port found.\n"
            "  termscope --demo     try it with simulated data\n"
            "  termscope --list     show detected ports\n"
            "  pip install 'termscope[serial]'   if pyserial is missing"
        )
    return SerialSource(port, args.baud, reconnect_interval=args.reconnect)


def options_from_args(args: argparse.Namespace) -> Options:
    return Options(
        window=max(0.2, args.window),
        capacity=max(64, args.capacity),
        fps=max(1.0, args.fps),
        x_mode="sample" if args.samples else "time",
        split=args.split,
        grid=not args.no_grid,
        light=args.light,
        color_depth=None if args.color == "auto" else args.color,
        prefix=args.prefix,
        max_channels=max(1, args.max_channels),
        record_path=args.record,
        record_mode="raw" if args.record_raw else "csv",
        force_overwrite=args.force,
        raw_view=args.raw,
        charset=None if args.charset == "auto" else args.charset,
        time_column=args.time_column,
        auto_time_column=not args.no_time_column,
        only=list(args.only),
    )


def run_once(source: Source, opt: Options, seconds: float) -> int:
    """Collect for a while, print one frame, exit.

    No alternate screen and no raw mode, so the output is plain text that can
    be redirected into a file. This is what generates the screenshots in the
    README, which means the README cannot drift from what the tool draws.
    """
    from .app import resolve_charset
    from .buffers import ChannelSet
    from .palette import MAX_SERIES
    from .parser import StreamParser
    from .recorder import Recorder, RecorderError
    from .timebase import TimeBase

    palette = Palette(dark=not opt.light, depth=opt.color_depth)
    renderer = Renderer(palette, charset=resolve_charset(opt.charset))
    parser = StreamParser(prefix=opt.prefix, max_channels=opt.max_channels)
    channels = ChannelSet(
        capacity=opt.capacity, max_channels=opt.max_channels, color_slots=MAX_SERIES
    )

    recorder = None
    if opt.record_path:
        try:
            recorder = Recorder(
                opt.record_path, mode=opt.record_mode, overwrite=opt.force_overwrite
            )
        except (RecorderError, ValueError) as exc:
            print(f"termscope: {exc}", file=sys.stderr)
            return 2

    timebase = TimeBase(opt.time_column, auto=opt.auto_time_column)

    def consume() -> None:
        for line in source.drain():
            parsed = parser.feed(line)
            if recorder is not None and opt.record_mode == "raw":
                recorder.write_raw(parsed.text or line)
            if not parsed.samples:
                continue
            now = time.time()
            for sample in parsed.samples:
                values, stamp = timebase.apply(sample, now)
                if opt.only:
                    values = {k: v for k, v in values.items() if k in opt.only}
                if not values:
                    continue
                channels.add(values, stamp)
                if recorder is not None and opt.record_mode == "csv":
                    recorder.write_values(values, stamp)

    source.start()
    deadline = time.monotonic() + max(0.1, seconds)
    try:
        while time.monotonic() < deadline:
            consume()
            if source.finished and not channels.names:
                break
            time.sleep(0.01)
        consume()
    finally:
        source.stop()
        if recorder is not None:
            recorder.close()

    err = source.check_error()
    if err is not None:
        print(f"termscope: {err}", file=sys.stderr)
        return 1
    if not channels.names:
        last_disconnect = getattr(source, "last_disconnect", None)
        if last_disconnect:
            print(
                f"termscope: no samples parsed; last serial error: {last_disconnect}",
                file=sys.stderr,
            )
        else:
            print("termscope: no samples parsed.", file=sys.stderr)
        return 1

    from .term import terminal_size

    width, height = terminal_size()
    layout = renderer.layout(width, height, len(channels))
    names = [n for n in channels.names if channels[n].visible]
    now = (
        channels.latest_timestamp
        if timebase.active and channels.latest_timestamp is not None
        else time.time()
    )

    rows = [renderer.render_header(source.description, "snapshot", width)]
    if opt.split:
        rows.extend(
            renderer.render_split_rows(
                channels,
                names,
                layout,
                window=opt.window,
                now=now,
                x_mode=opt.x_mode,
                sample_window=layout.plot_width * 2,
            )
        )
    else:
        extent = channels.extent(layout.plot_width * 2)
        lo, hi = renderer.pad_range(*extent) if extent else (-1.0, 1.0)
        rows.extend(
            renderer.render_plot_rows(
                channels,
                names,
                layout,
                lo,
                hi,
                window=opt.window,
                now=now,
                x_mode=opt.x_mode,
                show_grid=opt.grid,
            )
        )
    rows.append(
        renderer.render_xaxis(
            layout,
            window=opt.window,
            x_mode=opt.x_mode,
            sample_count=layout.plot_width * 2,
        )
    )
    rows.extend(renderer.render_legend(channels, layout, sample_window=layout.plot_width * 2))
    print("\n".join(rows))
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.list:
        return print_ports()

    try:
        source = make_source(args)
    except SourceError as exc:
        print(f"termscope: {exc}", file=sys.stderr)
        return 2

    opt = options_from_args(args)

    if args.once is not None:
        return run_once(source, opt, args.once)

    return App(source, opt).run()


if __name__ == "__main__":
    raise SystemExit(main())
