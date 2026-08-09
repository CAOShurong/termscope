"""Use a timestamp carried in the stream, instead of the clock on the wall.

Plenty of firmware prints its own time base -- ``millis()`` on Arduino, a tick
counter on an RTOS -- and every capture termscope records has a ``time_s``
column. When one of those is present it is strictly better than the arrival
time of the line: it survives buffering, it survives a replay running faster
than real time, and it shows the gaps where the device stalled rather than
where the reader did.

So a stream that carries a clock gets plotted against that clock. Replaying a
capture then reproduces the original timing exactly, which is also what makes
the figures in the README reproducible.
"""

from __future__ import annotations

from .parser import TELEPLOT_TIME_KEY

__all__ = ["CANDIDATES", "TimeBase"]

#: Channel names understood as a time base, mapped to seconds-per-unit.
#: Ordered by how specific the name is: a stream with both ``time_s`` and
#: ``millis`` means the first, and a bare ``t`` is the weakest signal.
CANDIDATES: tuple[tuple[str, float], ...] = (
    (TELEPLOT_TIME_KEY, 1e-3),
    ("time_s", 1.0),
    ("timestamp", 1.0),
    ("time", 1.0),
    ("seconds", 1.0),
    ("millis", 1e-3),
    ("ms", 1e-3),
    ("micros", 1e-6),
    ("us", 1e-6),
    ("t", 1.0),
)


class TimeBase:
    """Pulls a timestamp channel out of each sample, if there is one.

    Detection happens once, on the first sample that contains a candidate
    name, and then sticks. A channel is either the time axis for the whole
    session or it is data -- switching midway would put the plot in two
    coordinate systems at once.
    """

    def __init__(self, column: str | None = None, *, auto: bool = True) -> None:
        #: Explicitly requested column name, or None to auto-detect.
        self.requested = column
        self.auto = auto and column is None
        self.column: str | None = None
        self.scale = 1.0
        self._resolved = column is None and not auto
        self._origin: float | None = None
        if column:
            self.column = column
            self.scale = self._scale_for(column)
            self._resolved = True

    @staticmethod
    def _scale_for(name: str) -> float:
        lowered = name.strip().lower()
        for candidate, scale in CANDIDATES:
            if lowered == candidate:
                return scale
        return 1.0

    @property
    def active(self) -> bool:
        return self.column is not None

    def _detect(self, values: dict[str, float]) -> None:
        lowered = {name.strip().lower(): name for name in values}
        for candidate, scale in CANDIDATES:
            if candidate in lowered:
                self.column = lowered[candidate]
                self.scale = scale
                break
        self._resolved = True

    def apply(
        self, values: dict[str, float], fallback: float
    ) -> tuple[dict[str, float], float]:
        """Split a sample into ``(plottable values, timestamp)``.

        ``fallback`` is used whenever the stream carries no usable time -- a
        live serial port, or a line where the time column happened to be
        missing.
        """
        if self.auto and not self._resolved:
            self._detect(values)

        # A Teleplot timestamp is protocol metadata.  ``--no-time-column`` or
        # an explicitly selected clock may choose not to use it, but it must
        # never leak through as a plotted data series.
        if TELEPLOT_TIME_KEY in values and self.column != TELEPLOT_TIME_KEY:
            values = {key: value for key, value in values.items() if key != TELEPLOT_TIME_KEY}

        if self.column is None or self.column not in values:
            return values, fallback

        raw = values[self.column]
        remaining = {k: v for k, v in values.items() if k != self.column}
        if not remaining:
            # A line with nothing but a timestamp carries no data to plot;
            # treating it as a sample would draw an empty channel.
            return remaining, fallback

        seconds = raw * self.scale
        if self._origin is None:
            self._origin = seconds
        elif seconds < self._origin:
            # The device rebooted and its counter restarted. Re-anchor rather
            # than emitting a timestamp before everything already plotted.
            self._origin = seconds
        return remaining, seconds - self._origin
