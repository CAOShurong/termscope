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
    TELEPLOT = "teleplot"  # >temp:millis:value;millis:value§unit|flags


# Teleplot timestamps are metadata, not a visible channel.  Keeping them in
# the sample until TimeBase sees it lets the existing clock-normalisation code
# handle buffered points and device reboots without coupling the parser to the
# renderer.
TELEPLOT_TIME_KEY = "__termscope_teleplot_millis"


# A number: optional sign, decimal or integer, optional exponent. Also accepts
# the bare-integer and ".5" forms that show up in hand-rolled printf output.
_NUMBER = r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?"

# key:value or key=value. Keys are the identifier-ish things firmware uses;
# we allow dots and brackets so "imu.gyro[0]" survives intact.
_LABELLED_RE = re.compile(
    r"(?<![A-Za-z0-9_.\[\]/-])(?P<key>[A-Za-z_][A-Za-z0-9_.\[\]/-]*)\s*[:=]\s*(?P<val>"
    + _NUMBER
    + r")"
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
    samples: list[dict[str, float]] = field(default_factory=list)

    def __post_init__(self) -> None:
        # ``values`` remains the convenient, backwards-compatible view of a
        # normal one-sample line.  ``samples`` carries Teleplot batches through
        # the ingest pipeline without silently dropping all but one point.
        if self.samples and not self.values:
            self.values = self.samples[-1]
        elif self.values and not self.samples:
            self.samples = [self.values]

    def __bool__(self) -> bool:
        return bool(self.samples)


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


_TELEPLOT_UNSUPPORTED_FLAGS = {"clr", "np", "t", "xy"}


def _parse_teleplot(line: str) -> list[dict[str, float]] | None:
    """Parse Teleplot's serial time-series subset.

    ``None`` means the line is not Teleplot and should be offered to the
    ordinary labelled parser.  An empty list means it *is* Teleplot, but uses
    semantics termscope cannot represent honestly (XY, text, no-plot, or
    clear-history), so it must remain visible only in the raw pane.

    Teleplot timestamps are milliseconds.  Batched time-series points require
    one timestamp per point, matching the protocol rather than guessing a
    sample interval that the device never supplied.
    """
    if not line.startswith(">"):
        return None
    if line.startswith(">3D|"):
        return []

    name, separator, payload = line[1:].partition(":")
    name = name.strip()
    if not separator:
        return None
    if not name:
        # ``>:message`` is a Teleplot log line, not a numeric series.
        return []
    if any(char in name for char in ":|;"):
        return None

    data, flag_separator, flag_text = payload.partition("|")
    flags = {
        flag.strip().lower() for flag in flag_text.split(",") if flag_separator and flag.strip()
    }
    if flags & _TELEPLOT_UNSUPPORTED_FLAGS:
        return []

    # Units belong in presentation metadata; termscope currently keys series
    # by their stable protocol name, so a late unit does not create a second
    # channel for the same signal.
    data = data.partition("§")[0].strip()
    if not data:
        return None

    points = [point.strip() for point in data.split(";")]
    if any(not point for point in points):
        return None

    batched = len(points) > 1
    samples: list[dict[str, float]] = []
    for point in points:
        fields = [field.strip() for field in point.split(":")]
        if len(fields) == 1 and not batched:
            value = _to_float(fields[0])
            if value is None:
                return None
            samples.append({name: value})
            continue

        if len(fields) != 2:
            return None
        timestamp = _to_float(fields[0])
        value = _to_float(fields[1])
        if timestamp is None or value is None:
            return None
        samples.append({TELEPLOT_TIME_KEY: timestamp, name: value})

    return samples


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

        teleplot_candidate = text if text.startswith(">") else text_body
        teleplot_samples = _parse_teleplot(teleplot_candidate)
        detected = Format.TELEPLOT if teleplot_samples is not None else self._detect(text_body)

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
        samples: list[dict[str, float]] = []
        if self.fmt == Format.TELEPLOT:
            samples = teleplot_samples if detected == Format.TELEPLOT else []
        elif self.fmt == Format.LABELLED:
            # A timestamped Teleplot line also contains ``name:number``.  Do
            # not fall back to the labelled regex and plot the timestamp as if
            # it were the measurement.
            values = self._parse_labelled(text_body) if detected != Format.TELEPLOT else {}
            if values:
                samples = [values]
        elif self.fmt == Format.JSON:
            values = self._parse_json(text_body) if detected == Format.JSON else {}
            if values:
                samples = [values]
        elif self.fmt == Format.BARE:
            values = self._parse_bare(text_body) if detected == Format.BARE else {}
            if values:
                samples = [values]

        if not samples:
            self.skipped_count += 1
            return ParsedLine(text=text, fmt=self.fmt)

        accepted: list[dict[str, float]] = []
        for sample in samples:
            for name in sample:
                if name == TELEPLOT_TIME_KEY or name in self.channels:
                    continue
                if len(self.channels) >= self.max_channels:
                    # Silently ignore channels past the cap rather than
                    # letting a runaway stream exhaust memory.
                    continue
                self.channels.append(name)

            filtered = {
                key: value
                for key, value in sample.items()
                if key == TELEPLOT_TIME_KEY or key in self.channels
            }
            # A timestamp without an accepted data channel is not a sample.
            if any(key != TELEPLOT_TIME_KEY for key in filtered):
                accepted.append(filtered)

        if not accepted:
            self.skipped_count += 1
            return ParsedLine(text=text, fmt=self.fmt)

        self.parsed_count += len(accepted)
        return ParsedLine(text=text, samples=accepted, fmt=self.fmt)
