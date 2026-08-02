"""Series colours, and the escape codes to paint them.

The eight categorical hues are not hand-picked. They are a documented palette
validated for lightness band, chroma floor, contrast against the surface, and
-- the part that actually matters here -- separation under simulated
protanopia and deuteranopia. Both sets clear every gate on the adjacent-pair
list that line charts are measured against:

    dark   worst adjacent CVD dE 8.4, normal-vision dE 19.3
    light  worst adjacent CVD dE 9.1, normal-vision dE 19.6

Three light-mode hues sit below 3:1 against a white surface. That is a known
and permitted relaxation *provided* values stay readable without colour, which
is why the legend always shows each channel's name and current value as text.
Colour identifies a trace; it is never the only way to read one.

Two rules follow from this and are enforced in code rather than left to habit:

* A channel's colour is fixed when the channel is first seen and never
  reassigned. Hiding one trace must not repaint the others.
* Hues are never cycled. Past eight channels there is no ninth colour that
  survives CVD simulation, so extra channels stay hidden until the user picks
  which eight to watch.
"""

from __future__ import annotations

import os

__all__ = ["MAX_SERIES", "SERIES_DARK", "SERIES_LIGHT", "Palette"]

#: Validated categorical slots, in fixed order, for a dark terminal.
SERIES_DARK = (
    "#3987e5",  # blue
    "#d95926",  # orange
    "#199e70",  # aqua
    "#c98500",  # yellow
    "#d55181",  # magenta
    "#008300",  # green
    "#9085e9",  # violet
    "#e66767",  # red
)

#: The same eight hues stepped for a light terminal.
SERIES_LIGHT = (
    "#2a78d6",
    "#eb6834",
    "#1baf7a",
    "#eda100",
    "#e87ba4",
    "#008300",
    "#4a3aa7",
    "#e34948",
)

MAX_SERIES = len(SERIES_DARK)

#: Chrome. Muted grey is mode-invariant, so axis furniture reads correctly
#: whatever the terminal background is.
INK_MUTED = "#898781"
AXIS_DARK = "#383835"
AXIS_LIGHT = "#c3c2b7"


def _hex_to_rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


def _xterm256(rgb: tuple[int, int, int]) -> int:
    """Nearest xterm-256 index for an RGB triple.

    The 256-colour space is a 6x6x6 cube plus a 24-step grey ramp. Near-grey
    colours land closer on the ramp than in the cube, so both are tried and
    the better match wins -- without this, muted grey chrome picks up a
    distinctly purple cast on some terminals.
    """
    r, g, b = rgb
    levels = (0, 95, 135, 175, 215, 255)

    def nearest_level(v: int) -> int:
        return min(range(6), key=lambda i: abs(levels[i] - v))

    ri, gi, bi = nearest_level(r), nearest_level(g), nearest_level(b)
    cube_idx = 16 + 36 * ri + 6 * gi + bi
    cube_err = (levels[ri] - r) ** 2 + (levels[gi] - g) ** 2 + (levels[bi] - b) ** 2

    grey_avg = (r + g + b) // 3
    step = min(23, max(0, round((grey_avg - 8) / 10)))
    grey_val = 8 + 10 * step
    grey_err = (grey_val - r) ** 2 + (grey_val - g) ** 2 + (grey_val - b) ** 2

    return 232 + step if grey_err < cube_err else cube_idx


def detect_depth(force: str | None = None) -> str:
    """Colour capability: ``truecolor``, ``256``, ``16`` or ``none``."""
    if force:
        return force
    if os.environ.get("NO_COLOR"):
        return "none"
    term = os.environ.get("TERM", "")
    if term == "dumb":
        return "none"
    colorterm = os.environ.get("COLORTERM", "").lower()
    if colorterm in ("truecolor", "24bit"):
        return "truecolor"
    # Windows Terminal, VS Code and modern conhost all do 24-bit.
    if os.environ.get("WT_SESSION") or os.environ.get("TERM_PROGRAM") in (
        "vscode",
        "iTerm.app",
        "WezTerm",
        "ghostty",
    ):
        return "truecolor"
    if "256" in term:
        return "256"
    if term:
        return "16"
    return "256" if os.name == "nt" else "16"


class Palette:
    """Maps slot indices to escape codes at the terminal's colour depth."""

    def __init__(self, *, dark: bool = True, depth: str | None = None) -> None:
        self.dark = dark
        self.depth = detect_depth(depth)
        self.series = SERIES_DARK if dark else SERIES_LIGHT
        self.axis_hex = AXIS_DARK if dark else AXIS_LIGHT

    @property
    def enabled(self) -> bool:
        return self.depth != "none"

    def fg(self, hex_color: str) -> str:
        """SGR foreground sequence for a hex colour, at the current depth."""
        if self.depth == "none":
            return ""
        rgb = _hex_to_rgb(hex_color)
        if self.depth == "truecolor":
            return f"\x1b[38;2;{rgb[0]};{rgb[1]};{rgb[2]}m"
        if self.depth == "256":
            return f"\x1b[38;5;{_xterm256(rgb)}m"
        # 16-colour terminals: fall back to the nearest basic hue.
        return f"\x1b[{self._nearest_ansi16(rgb)}m"

    @staticmethod
    def _nearest_ansi16(rgb: tuple[int, int, int]) -> int:
        basics = {
            31: (205, 49, 49),
            32: (13, 188, 121),
            33: (229, 229, 16),
            34: (36, 114, 200),
            35: (188, 63, 188),
            36: (17, 168, 205),
            37: (229, 229, 229),
            90: (102, 102, 102),
            91: (241, 76, 76),
            92: (35, 209, 139),
            93: (245, 245, 67),
            94: (59, 142, 234),
            95: (214, 112, 214),
            96: (41, 184, 219),
        }
        return min(
            basics,
            key=lambda code: sum((basics[code][i] - rgb[i]) ** 2 for i in range(3)),
        )

    def series_fg(self, slot: int) -> str:
        """Colour for categorical slot ``slot``.

        Slots are never cycled -- an out-of-range slot is a bug in the caller,
        and returning muted grey makes it visible instead of silently
        duplicating an existing hue.
        """
        if not 0 <= slot < MAX_SERIES:
            return self.muted()
        return self.fg(self.series[slot])

    def series_hex(self, slot: int) -> str:
        if not 0 <= slot < MAX_SERIES:
            return INK_MUTED
        return self.series[slot]

    def muted(self) -> str:
        return self.fg(INK_MUTED)

    def axis(self) -> str:
        return self.fg(self.axis_hex)

    def reset(self) -> str:
        return "\x1b[0m" if self.enabled else ""

    def dim(self) -> str:
        return "\x1b[2m" if self.enabled else ""

    def bold(self) -> str:
        return "\x1b[1m" if self.enabled else ""

    def reverse(self) -> str:
        return "\x1b[7m" if self.enabled else ""
