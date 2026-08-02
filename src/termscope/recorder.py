"""Write the live stream to disk while it is being plotted.

Two formats, for two different jobs:

``csv``
    one row per sample with a column per channel -- what you open in a
    spreadsheet or feed to pandas afterwards.
``raw``
    the bytes as they arrived, unparsed -- what you want when the interesting
    part is the line the parser *could not* read.

Channels are discovered as the stream runs, so the CSV header cannot be
written until the first row is known. Late-appearing channels get their own
column and are back-filled as empty for rows that predate them, which keeps
the file rectangular without buffering the whole capture in memory.
"""

from __future__ import annotations

import contextlib
import csv
import os
import time
from typing import TextIO

__all__ = ["Recorder", "RecorderError"]


class RecorderError(RuntimeError):
    """Recording could not start or continue."""


class Recorder:
    """Append samples to a CSV or raw log."""

    def __init__(self, path: str, *, mode: str = "csv", overwrite: bool = False) -> None:
        if mode not in ("csv", "raw"):
            raise ValueError(f"unknown recording mode: {mode!r}")
        self.path = path
        self.mode = mode
        self.rows_written = 0
        self._handle: TextIO | None = None
        self._writer = None
        self._columns: list[str] = []
        self._start = time.time()

        if not overwrite and os.path.exists(path):
            raise RecorderError(f"{path} already exists (pass --force to overwrite)")
        try:
            # newline="" is required for the csv module to control line
            # endings itself; without it Windows produces blank rows.
            self._handle = open(path, "w", encoding="utf-8", newline="")  # noqa: SIM115
        except OSError as exc:
            raise RecorderError(f"could not open {path}: {exc}") from exc
        if mode == "csv":
            self._writer = csv.writer(self._handle)

    # -- writing -----------------------------------------------------------

    def write_values(self, values: dict[str, float], timestamp: float) -> None:
        """Record one parsed sample. Ignored in raw mode."""
        if self.mode != "csv" or self._writer is None:
            return
        new = [name for name in values if name not in self._columns]
        if new:
            if self._columns:
                # A channel appeared mid-capture. Note it inline rather than
                # rewriting the header, so earlier rows stay valid as read.
                self._writer.writerow([f"# new channels: {', '.join(new)}"])
            self._columns.extend(new)
            self._writer.writerow(["time_s", *self._columns])
        row = [f"{timestamp - self._start:.6f}"]
        for name in self._columns:
            value = values.get(name)
            row.append("" if value is None else repr(value))
        self._writer.writerow(row)
        self.rows_written += 1

    def write_raw(self, line: str) -> None:
        """Record one unparsed line. Ignored in csv mode."""
        if self.mode != "raw" or self._handle is None:
            return
        self._handle.write(line + "\n")
        self.rows_written += 1

    def flush(self) -> None:
        if self._handle is not None:
            with contextlib.suppress(OSError):
                self._handle.flush()

    def close(self) -> None:
        handle, self._handle = self._handle, None
        self._writer = None
        if handle is not None:
            with contextlib.suppress(OSError):
                handle.close()

    def __enter__(self) -> Recorder:
        return self

    def __exit__(self, *exc_info) -> bool:
        self.close()
        return False

    @property
    def closed(self) -> bool:
        return self._handle is None
