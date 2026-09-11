"""Edge trigger: freeze the scope when a channel crosses a threshold.

A serial plotter is most useful while you are turning a PID knob, and the
moment that matters is the overshoot, not the ten seconds around it. The
trigger holds the trace on that crossing the same way a bench scope does.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

__all__ = ["TriggerSpec", "TriggerWatch", "parse_trigger"]

_EDGES = ("rising", "falling", "either")
_SPEC = re.compile(r"^(?P<name>.+?)(?P<op>>=|<=|>|<|:|=)(?P<value>.+)$")
_OP_EDGE = {
    ">": "rising",
    ">=": "rising",
    "<": "falling",
    "<=": "falling",
}


@dataclass(frozen=True)
class TriggerSpec:
    """One named channel and the threshold that should freeze the plot."""

    name: str
    threshold: float
    edge: str = "rising"

    def describe(self) -> str:
        if self.edge == "falling":
            op = "<"
        elif self.edge == "either":
            op = ":"
        else:
            op = ">"
        return f"{self.name}{op}{self.threshold:g}"


def parse_trigger(spec: str, *, default_edge: str = "rising") -> TriggerSpec:
    """Parse ``pitch:5``, ``pitch>5`` or ``pitch<0`` into a :class:`TriggerSpec`.

    ``:`` and ``=`` take ``default_edge`` (the ``--trigger-edge`` flag).
    ``>`` / ``<`` set the edge themselves so a one-token spec is enough.
    """
    if default_edge not in _EDGES:
        raise ValueError(f"unknown trigger edge {default_edge!r}")
    text = spec.strip()
    match = _SPEC.match(text)
    if match is None:
        raise ValueError(
            f"invalid trigger {spec!r}; expected NAME:VALUE, NAME>VALUE, or NAME<VALUE"
        )
    name = match.group("name").strip()
    raw_value = match.group("value").strip()
    if not name or not any(ch.isalnum() for ch in name):
        raise ValueError("trigger is missing a channel name")
    try:
        threshold = float(raw_value)
    except ValueError as exc:
        raise ValueError(f"trigger threshold {raw_value!r} is not a number") from exc
    if not math.isfinite(threshold):
        raise ValueError("trigger threshold must be finite")
    op = match.group("op")
    edge = _OP_EDGE.get(op, default_edge)
    return TriggerSpec(name=name, threshold=threshold, edge=edge)


class TriggerWatch:
    """Stateful observer: armed until the configured edge fires once."""

    def __init__(self, spec: TriggerSpec) -> None:
        self.spec = spec
        self.armed = True
        self.fired = False
        self._last: float | None = None

    def observe(self, values: dict[str, float]) -> bool:
        """Return True on the sample that crosses the threshold.

        The first sample only seeds the previous value, so a channel that
        starts already past the threshold does not fire until it recrosses.
        """
        if self.spec.name not in values:
            return False
        value = values[self.spec.name]
        prev = self._last
        self._last = value
        if not self.armed or prev is None:
            return False
        rising = prev < self.spec.threshold <= value
        falling = prev > self.spec.threshold >= value
        crossed = (
            (self.spec.edge == "rising" and rising)
            or (self.spec.edge == "falling" and falling)
            or (self.spec.edge == "either" and (rising or falling))
        )
        if not crossed:
            return False
        self.armed = False
        self.fired = True
        return True

    def rearm(self) -> None:
        """Wait for another crossing. Keeps the last sample so it cannot
        immediately re-fire while the signal is still on the far side."""
        self.armed = True
        self.fired = False
