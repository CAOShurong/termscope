#!/usr/bin/env python3
"""Render the animated demo in the README.

A still frame undersells a scope: the whole point is that the trace moves. So
this replays the committed capture through the real renderer, grabs a frame
every few samples, and draws each one with a monospace font at the palette's
own colours.

Nothing is faked. The frames come from the same code path the terminal uses --
if rendering changes, regenerating this GIF shows it.

    python docs/build_demo_gif.py

Needs Pillow, which is not a runtime dependency:

    python -m pip install pillow
"""

from __future__ import annotations

import argparse
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from termscope.buffers import ChannelSet  # noqa: E402
from termscope.canvas import BRAILLE  # noqa: E402
from termscope.palette import MAX_SERIES, Palette  # noqa: E402
from termscope.parser import StreamParser  # noqa: E402
from termscope.plot import Renderer  # noqa: E402
from termscope.timebase import TimeBase  # noqa: E402

FIXTURE = ROOT / "examples" / "balancing-robot.csv"
OUT = ROOT / "docs" / "demo.gif"

COLS, ROWS = 78, 20
# The capture is about ten seconds long. A four-second window leaves six
# seconds of scrolling once the window has filled, which is what the animation
# shows -- starting before it fills would open on a stub in the corner.
WINDOW = 4.0
FRAMES = 64
SAMPLES_PER_FRAME = 6
FRAME_MS = 90

# The validated dark chart surface, so the image matches what the palette was
# checked against rather than an arbitrary black.
BACKGROUND = "#1a1a19"
DEFAULT_INK = "#c3c2b7"

# Runs emitted by the renderer look like  \x1b[38;2;R;G;Bm<text>
SGR = re.compile(r"\x1b\[([0-9;]*)m")

FONT_CANDIDATES = [
    r"C:\Windows\Fonts\consola.ttf",
    r"C:\Windows\Fonts\cour.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    "/System/Library/Fonts/Menlo.ttc",
]

BRAILLE_LO, BRAILLE_HI = 0x2800, 0x28FF

# Bit -> (column, row) inside the 2x4 cell. Mirrors _DOT_BITS in canvas.py; the
# Unicode block numbers its dots historically rather than in raster order.
DOT_POSITIONS = (
    (0x01, 0, 0),
    (0x02, 0, 1),
    (0x04, 0, 2),
    (0x40, 0, 3),
    (0x08, 1, 0),
    (0x10, 1, 1),
    (0x20, 1, 2),
    (0x80, 1, 3),
)


def load_font(size: int):
    """A monospace face for the labels. Braille never goes through it."""
    from PIL import ImageFont

    for path in FONT_CANDIDATES:
        if pathlib.Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    from PIL import ImageFont as _f

    return _f.load_default()


def split_runs(line: str) -> list[tuple[str, str]]:
    """Split one styled row into ``(text, hex colour)`` runs."""
    runs: list[tuple[str, str]] = []
    color = DEFAULT_INK
    pos = 0
    for m in SGR.finditer(line):
        if m.start() > pos:
            runs.append((line[pos : m.start()], color))
        params = m.group(1)
        if params in ("", "0"):
            color = DEFAULT_INK
        elif params.startswith("38;2;"):
            r, g, b = (int(v) for v in params.split(";")[2:5])
            color = f"#{r:02x}{g:02x}{b:02x}"
        pos = m.end()
    if pos < len(line):
        runs.append((line[pos:], color))
    return runs


def collect_frames() -> list[list[str]]:
    """Replay the capture, keeping a rendered frame every few samples."""
    if not FIXTURE.exists():
        raise SystemExit(f"missing fixture: {FIXTURE}")

    renderer = Renderer(Palette(dark=True, depth="truecolor"), charset=BRAILLE)
    parser = StreamParser()
    timebase = TimeBase()
    channels = ChannelSet(capacity=4096, color_slots=MAX_SERIES)
    layout = renderer.layout(COLS, ROWS, 3)

    frames: list[list[str]] = []
    since = 0
    with FIXTURE.open(encoding="utf-8") as handle:
        for raw in handle:
            parsed = parser.feed(raw.rstrip("\r\n"))
            if not parsed.values:
                continue
            values, stamp = timebase.apply(parsed.values, 0.0)
            if not values:
                continue
            channels.add(values, stamp)
            since += 1
            if since < SAMPLES_PER_FRAME or channels.latest_timestamp is None:
                continue
            # Hold off until there is a full window of history, so the first
            # frame is a full-width trace rather than a stub in the corner.
            if channels.latest_timestamp < WINDOW:
                continue
            since = 0

            now = channels.latest_timestamp
            extent = channels.extent(layout.plot_width * 2)
            lo, hi = renderer.pad_range(*extent) if extent else (-1.0, 1.0)
            rows = [renderer.render_header("termscope --demo", f"{now:5.1f}s", COLS)]
            rows += renderer.render_plot_rows(
                channels,
                channels.visible_names(),
                layout,
                lo,
                hi,
                window=WINDOW,
                now=now,
                x_mode="time",
            )
            rows.append(
                renderer.render_xaxis(
                    layout,
                    window=WINDOW,
                    x_mode="time",
                    sample_count=layout.plot_width * 2,
                )
            )
            rows += renderer.render_legend(
                channels, layout, sample_window=layout.plot_width * 2
            )
            frames.append(rows)
            if len(frames) >= FRAMES:
                break

    return frames[:FRAMES]


def draw(frames: list[list[str]], font_size: int) -> None:
    from PIL import Image, ImageDraw

    font = load_font(font_size)
    bbox = font.getbbox("M")
    cw = max(1, bbox[2] - bbox[0])
    ch = int(font_size * 1.35)
    pad = 12
    width = cw * COLS + pad * 2
    height = ch * (ROWS + 1) + pad * 2

    # Braille is drawn as dots rather than as glyphs. Almost no system font
    # ships the U+2800 block -- Consolas does not -- so rendering it as text
    # produces a grid of notdef boxes. The codepoint *is* the bit pattern, so
    # painting the dots directly is both font-independent and exact.
    dot_w = max(1, cw // 2 - 1)
    dot_h = max(1, ch // 4 - 1)

    def draw_braille(d, x: int, y: int, code: int, color: str) -> None:
        bits = code - BRAILLE_LO
        for bit, col, row in DOT_POSITIONS:
            if bits & bit:
                dx = x + col * (cw // 2)
                dy = y + row * (ch // 4)
                d.rectangle([dx, dy, dx + dot_w - 1, dy + dot_h - 1], fill=color)

    images = []
    for rows in frames:
        img = Image.new("RGB", (width, height), BACKGROUND)
        d = ImageDraw.Draw(img)
        for i, line in enumerate(rows):
            x = pad
            y = pad + i * ch
            for text, color in split_runs(line):
                if not text:
                    continue
                # Emit runs of ordinary text in one call, and break out only
                # for braille, so label rendering stays cheap.
                buf = []
                for chx in text:
                    code = ord(chx)
                    if BRAILLE_LO <= code <= BRAILLE_HI:
                        if buf:
                            d.text((x, y), "".join(buf), font=font, fill=color)
                            x += cw * len(buf)
                            buf = []
                        draw_braille(d, x, y, code, color)
                        x += cw
                    else:
                        buf.append(chx)
                if buf:
                    d.text((x, y), "".join(buf), font=font, fill=color)
                    x += cw * len(buf)
        # Quantising per frame keeps the palette stable across the animation;
        # letting GIF pick per frame makes the traces shimmer.
        images.append(img.convert("P", palette=Image.ADAPTIVE, colors=128))

    images[0].save(
        OUT,
        save_all=True,
        append_images=images[1:],
        duration=FRAME_MS,
        loop=0,
        optimize=True,
        disposal=2,
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--font-size", type=int, default=13)
    args = ap.parse_args()

    frames = collect_frames()
    if len(frames) < 8:
        raise SystemExit(f"only captured {len(frames)} frames; fixture too short?")
    draw(frames, args.font_size)
    size_kb = OUT.stat().st_size // 1024
    print(f"wrote {OUT.relative_to(ROOT)}  ({len(frames)} frames, {size_kb} KB)")
    if size_kb > 4096:
        print("warning: over 4 MB; GitHub renders it but it will be slow to load")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
