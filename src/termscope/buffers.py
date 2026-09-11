"""Fixed-capacity sample storage, one buffer per channel.

A scope only ever draws the most recent screenful, so samples live in a ring
buffer that overwrites the oldest entry instead of growing without bound. A
board left streaming at 1 kHz overnight uses exactly as much memory as one
started a second ago.

Timestamps are stored alongside values because serial data arrives at whatever
rate the firmware felt like, and a plot indexed purely by sample number hides
dropped frames and stalls.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

__all__ = ["ChannelBuffer", "ChannelSet", "Stats", "moving_average"]


def moving_average(values: list[float], window: int) -> list[float]:
    """Causal mean of the last ``window`` samples. ``window<=1`` is a no-op."""
    if window <= 1 or len(values) <= 1:
        return list(values)
    width = min(int(window), len(values))
    out: list[float] = []
    acc = 0.0
    for i, value in enumerate(values):
        acc += value
        if i >= width:
            acc -= values[i - width]
            n = width
        else:
            n = i + 1
        out.append(acc / n)
    return out


@dataclass(frozen=True)
class Stats:
    """Summary of a window of samples."""

    count: int
    minimum: float
    maximum: float
    last: float
    mean: float


class ChannelBuffer:
    """Ring buffer of ``(timestamp, value)`` samples for one channel."""

    __slots__ = (
        "_len",
        "_start",
        "_ts",
        "_val",
        "capacity",
        "color_index",
        "name",
        "smooth",
        "total",
        "visible",
    )

    def __init__(
        self,
        capacity: int = 4096,
        *,
        name: str = "",
        color_index: int = -1,
        smooth: int = 1,
    ) -> None:
        if capacity <= 0:
            raise ValueError("capacity must be positive")
        self.capacity = capacity
        self.smooth = max(1, int(smooth))
        self._ts = [0.0] * capacity
        self._val = [0.0] * capacity
        self._start = 0
        self._len = 0
        self.name = name
        #: Categorical colour slot, fixed when the channel is first seen and
        #: never reassigned. Hiding one trace must not repaint the others.
        #: -1 means "no slot left" -- see :meth:`ChannelSet.ensure`.
        self.color_index = color_index
        #: Whether this channel is currently drawn. Toggled by number keys.
        self.visible = True
        #: Lifetime sample count, unaffected by ring wrap-around.
        self.total = 0

    def __len__(self) -> int:
        return self._len

    def append(self, value: float, timestamp: float) -> None:
        """Store one sample, discarding the oldest if the ring is full."""
        if self._len < self.capacity:
            idx = (self._start + self._len) % self.capacity
            self._len += 1
        else:
            idx = self._start
            self._start = (self._start + 1) % self.capacity
        self._ts[idx] = timestamp
        self._val[idx] = value
        self.total += 1

    def clear(self) -> None:
        self._start = 0
        self._len = 0
        self.total = 0

    def _index(self, i: int) -> int:
        return (self._start + i) % self.capacity

    def values(self, count: int | None = None) -> list[float]:
        """The most recent ``count`` values, oldest first."""
        n = self._len if count is None else min(count, self._len)
        first = self._len - n
        raw = [self._val[self._index(first + i)] for i in range(n)]
        return moving_average(raw, self.smooth)

    def timestamps(self, count: int | None = None) -> list[float]:
        """The most recent ``count`` timestamps, oldest first."""
        n = self._len if count is None else min(count, self._len)
        first = self._len - n
        return [self._ts[self._index(first + i)] for i in range(n)]

    def window(self, count: int | None = None) -> tuple[list[float], list[float]]:
        """``(timestamps, values)`` for the most recent ``count`` samples."""
        n = self._len if count is None else min(count, self._len)
        first = self._len - n
        ts, val = [], []
        for i in range(n):
            idx = self._index(first + i)
            ts.append(self._ts[idx])
            val.append(self._val[idx])
        return ts, moving_average(val, self.smooth)

    def last(self) -> float | None:
        if not self._len:
            return None
        return self._val[self._index(self._len - 1)]

    def stats(self, count: int | None = None) -> Stats | None:
        """Min/max/mean over the most recent ``count`` samples."""
        vals = self.values(count)
        if not vals:
            return None
        total = math.fsum(vals)
        return Stats(
            count=len(vals),
            minimum=min(vals),
            maximum=max(vals),
            last=vals[-1],
            mean=total / len(vals),
        )


class ChannelSet:
    """Ordered collection of channels, created on demand as names appear.

    Insertion order is the order channels were first seen, which keeps colour
    assignment stable for the lifetime of a session -- a channel does not
    change colour just because it went quiet for a while.
    """

    def __init__(
        self,
        capacity: int = 4096,
        max_channels: int = 32,
        color_slots: int = 8,
        smooth: int = 1,
    ) -> None:
        self.capacity = capacity
        self.max_channels = max_channels
        self.color_slots = color_slots
        self.smooth = max(1, int(smooth))
        self._buffers: dict[str, ChannelBuffer] = {}
        #: Timestamp of the most recent sample, across all channels. This is
        #: the right-hand edge of the plot when replaying a capture with
        #: recorded times, where wall-clock "now" is meaningless.
        self.latest_timestamp: float | None = None

    def __contains__(self, name: object) -> bool:
        return name in self._buffers

    def __getitem__(self, name: str) -> ChannelBuffer:
        return self._buffers[name]

    def __len__(self) -> int:
        return len(self._buffers)

    def __iter__(self):
        return iter(self._buffers)

    @property
    def names(self) -> list[str]:
        return list(self._buffers)

    def visible_names(self) -> list[str]:
        return [n for n, b in self._buffers.items() if b.visible]

    def ensure(self, name: str) -> ChannelBuffer | None:
        """Get or create a channel. Returns None once the cap is reached.

        The first :data:`~termscope.palette.MAX_SERIES` channels take a
        categorical colour slot. There is no ninth hue that stays distinct
        under colour-vision deficiency, so later channels are created with no
        slot and start hidden -- the user picks which eight to watch rather
        than getting two traces the same colour.
        """
        buf = self._buffers.get(name)
        if buf is not None:
            return buf
        if len(self._buffers) >= self.max_channels:
            return None
        slot = len(self._buffers)
        if slot >= self.color_slots:
            buf = ChannelBuffer(self.capacity, name=name, color_index=-1, smooth=self.smooth)
            buf.visible = False
        else:
            buf = ChannelBuffer(self.capacity, name=name, color_index=slot, smooth=self.smooth)
        self._buffers[name] = buf
        return buf

    @property
    def overflow_names(self) -> list[str]:
        """Channels that arrived after every colour slot was taken."""
        return [n for n, b in self._buffers.items() if b.color_index < 0]

    def add(self, values: dict[str, float], timestamp: float) -> None:
        """Append one sample for each named channel in ``values``."""
        appended = False
        for name, value in values.items():
            buf = self.ensure(name)
            if buf is not None:
                buf.append(value, timestamp)
                appended = True
        if appended and (self.latest_timestamp is None or timestamp > self.latest_timestamp):
            self.latest_timestamp = timestamp

    def clear(self) -> None:
        for buf in self._buffers.values():
            buf.clear()
        self.latest_timestamp = None

    def extent(self, count: int | None = None, *, only_visible: bool = True):
        """Combined ``(min, max)`` across channels, or None if there is no data.

        This is what drives shared-axis autoscaling: every visible channel is
        measured together so their relative magnitudes stay readable.
        """
        lo = math.inf
        hi = -math.inf
        for buf in self._buffers.values():
            if only_visible and not buf.visible:
                continue
            st = buf.stats(count)
            if st is None:
                continue
            lo = min(lo, st.minimum)
            hi = max(hi, st.maximum)
        if lo is math.inf:
            return None
        return lo, hi
