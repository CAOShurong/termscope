"""Turn whatever a microcontroller prints into named numeric channels.

There is no standard for serial telemetry. Every firmware invents its own
shape, and almost every real stream is a mix of data and unrelated debug
chatter from some library that logs to the same UART. So the parser is built
around two rules:

* Accept the five formats people actually print (see :class:`StreamParser`).
* Never let a junk line kill the plot -- unparseable input is returned as text
  for the raw pane and otherwise ignored.

Auto-detection is sticky. Once a stream commits to a format we keep using it,
so one malformed line in a thousand cannot silently re-key every channel.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

__all__ = ["Format", "ParsedLine", "StreamParser"]


class Format:
    """Recognised stream shapes."""

    UNKNOWN = "unknown"
    LABELLED = "labelled"  # pitch:1.23 roll=4.5
    JSON = "json"  # {"pitch": 1.23}
    CSV_HEADER = "csv-header"  # time,pitch,roll  then  10,1.2,3.4
    BARE = "bare"  # 1.23 4.56   /   1.23,4.56


# A number: optional sign, decimal or integer, optional exponent. Also accepts
# the bare-integer and ".5" forms that show up in hand-rolled printf output.
_NUMBER = r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?"

# key:value or key=value. Keys are the identifier-ish things firmware uses;
# we allow dots and brackets so "imu.gyro[0]" survives intact.
_LABELLED_RE = re.compile(
    r"(?P<key>[A-Za-z_][A-Za-z0-9_.\[\]/-]*)\s*[:=]\s*(?P<val>" + _NUMBER + r")"
)

_BARE_NUMBER_RE = re.compile(_NUMBER)

# A line that is nothing but numbers and separators -- the Arduino Serial
# Plotter convention.
_ALL_NUMERIC_RE = re.compile(r"^\s*" + _NUMBER + r"(?:\s*[,;\t ]\s*" + _NUMBER + r")*\s*$")

# A plausible CSV header: comma/tab separated words, no numbers-only fields.
_CSV_HEADER_RE = re.compile(
    r"^\s*[A-Za-z_][A-Za-z0-9_.\[\]/ -]*(?:\s*[,;\t]\s*[A-Za-z_][A-Za-z0-9_.\[\]/ -]*)+\s*$"
)

_SPLIT_RE = re.compile(r"[,;\t ]+")

# Control bytes and ANSI colour that firmware emits around log lines. Stripped
# before parsing so "\x1b[32mtemp:24.5\x1b[0m" still yields a reading.
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
_CTRL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


@dataclass
class ParsedLine:
    """One line of input after parsing.

    ``values`` is empty for lines that carried no readings; ``text`` always
    holds the cleaned original so the raw pane can show it.
    """

    text: str
    values: dict[str, float] = field(default_factory=dict)
    fmt: str = Format.UNKNOWN

    def __bool__(self) -> bool:
        return bool(self.values)


def _clean(line: str) -> str:
    return _CTRL_RE.sub("", _ANSI_RE.sub("", line)).strip()


def _to_float(tok: str) -> float | None:
    try:
        val = float(tok)
    except (TypeError, ValueError):
        return None
    # inf/nan would poison autoscaling; drop them at the door.
    if val != val or val in (float("inf"), float("-inf")):
        return None
    return val


class StreamParser:
    """Incremental parser that locks onto a stream's format.

    Usage::

        p = StreamParser()
        for line in serial_lines:
            parsed = p.feed(line)
            if parsed:
                update_plot(parsed.values)

    The parser is deliberately tolerant: a line that does not match the locked
    format is not an error, it is just text. That is what lets a plot survive
    a firmware that interleaves ``printf("boot ok\\n")`` with telemetry.
    """

    def __init__(self, *, prefix: str | None = None, max_channels: int = 32) -> None:
        #: Only consider lines starting with this marker (e.g. ``">"``). Many
        #: firmwares tag telemetry precisely so it can be told apart from logs.
        self.prefix = prefix
        self.max_channels = max_channels
        self.fmt = Format.UNKNOWN
        self._csv_fields: list[str] | None = None
        #: Channel names in first-seen order -- plot colours follow this.
        self.channels: list[str] = []
        self.parsed_count = 0
        self.skipped_count = 0

    # -- detection ---------------------------------------------------------

    def _detect(self, line: str) -> str:
        if line.startswith("{") and line.endswith("}"):
            return Format.JSON
        if _LABELLED_RE.search(line):
            return Format.LABELLED
        if _ALL_NUMERIC_RE.match(line):
            return Format.BARE
        if _CSV_HEADER_RE.match(line):
            return Format.CSV_HEADER
        return Format.UNKNOWN

    # -- per-format extraction --------------------------------------------

    def _parse_json(self, line: str) -> dict[str, float]:
        try:
            obj = json.loads(line)
        except (ValueError, RecursionError):
            return {}
        if not isinstance(obj, dict):
            return {}
        out: dict[str, float] = {}
        for key, raw in obj.items():
            if isinstance(raw, bool):
                # bool is an int subclass; plotting True as 1.0 is usually
                # what people want for state flags.
                out[str(key)] = float(raw)
            elif isinstance(raw, (int, float)):
                val = _to_float(raw)
                if val is not None:
                    out[str(key)] = val
        return out

    def _parse_labelled(self, line: str) -> dict[str, float]:
        out: dict[str, float] = {}
        for match in _LABELLED_RE.finditer(line):
            val = _to_float(match.group("val"))
            if val is not None:
                out[match.group("key")] = val
        return out

    def _parse_bare(self, line: str) -> dict[str, float]:
        toks = [t for t in _SPLIT_RE.split(line.strip()) if t]
        out: dict[str, float] = {}
        for i, tok in enumerate(toks):
            val = _to_float(tok)
            if val is None:
                # A bare line must be entirely numeric; a stray word means
                # this was debug output that merely looked like data.
                return {}
            out[self._bare_name(i)] = val
        return out

    def _bare_name(self, index: int) -> str:
        """Name for positional channel ``index``.

        If a CSV header was seen, positions inherit those names; otherwise we
        fall back to ``ch0``, ``ch1``, ...
        """
        if self._csv_fields and index < len(self._csv_fields):
            return self._csv_fields[index]
        return f"ch{index}"

    # -- public API --------------------------------------------------------

    def feed(self, line: str) -> ParsedLine:
        """Parse one line. Never raises."""
        text = _clean(line)
        if not text:
            return ParsedLine(text="", fmt=self.fmt)

        if self.prefix:
            if not text.startswith(self.prefix):
                self.skipped_count += 1
                return ParsedLine(text=text, fmt=self.fmt)
            text_body = text[len(self.prefix) :].strip()
        else:
            text_body = text

        if not text_body:
            return ParsedLine(text=text, fmt=self.fmt)

        detected = self._detect(text_body)

        # A CSV header re-keys the positional channels. It can legitimately
        # appear mid-stream when a board reboots, so it is always honoured.
        if detected == Format.CSV_HEADER:
            fields = [f.strip() for f in re.split(r"[,;\t]", text_body) if f.strip()]
            if 1 < len(fields) <= self.max_channels:
                self._csv_fields = fields
                self.fmt = Format.BARE
            return ParsedLine(text=text, fmt=self.fmt)

        if self.fmt == Format.UNKNOWN:
            if detected == Format.UNKNOWN:
                self.skipped_count += 1
                return ParsedLine(text=text, fmt=self.fmt)
            self.fmt = detected

        # Labelled data may appear inside an otherwise chatty line, so a
        # locked-in labelled stream keeps scanning even when the line as a
        # whole does not look like pure telemetry.
        if self.fmt == Format.LABELLED:
            values = self._parse_labelled(text_body)
        elif self.fmt == Format.JSON:
            values = self._parse_json(text_body) if detected == Format.JSON else {}
        elif self.fmt == Format.BARE:
            values = self._parse_bare(text_body) if detected == Format.BARE else {}
        else:
            values = {}

        if not values:
            self.skipped_count += 1
            return ParsedLine(text=text, fmt=self.fmt)

        for name in values:
            if name not in self.channels:
                if len(self.channels) >= self.max_channels:
                    # Silently ignore channels past the cap rather than
                    # letting a runaway stream exhaust memory.
                    continue
                self.channels.append(name)

        values = {k: v for k, v in values.items() if k in self.channels}
        if not values:
            self.skipped_count += 1
            return ParsedLine(text=text, fmt=self.fmt)

        self.parsed_count += 1
        return ParsedLine(text=text, values=values, fmt=self.fmt)
