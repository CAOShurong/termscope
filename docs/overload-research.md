# Why TermScope bounds its input handoff

Research date: 2026-08-11. This note records the evidence and decisions behind
v0.4.0. It does not claim a maximum supported device rate or lossless serial
capture.

## The falsifiable gap

TermScope already bounded plotted samples per channel and drained at most 4,096
lines per render-loop pass, but v0.3.0 handed producer lines to the consumer
through Python's `queue.SimpleQueue`. Python documents `SimpleQueue` as an
unbounded FIFO. If input stayed faster than parsing and rendering, the backlog
could therefore grow for the lifetime of the process.

A local baseline injected 300,000 distinct short lines through the real
`Source._emit` path without starting the consumer. v0.3.0 retained all 300,000
and `tracemalloc` observed about 20.3 MB in that queue. This is a controlled
stress experiment, not a hardware throughput benchmark, but it directly
falsified the intended memory boundary.

The mechanism and user pain are independently documented:

- [Python queue documentation](https://docs.python.org/3/library/queue.html)
  defines a non-positive `Queue.maxsize` as infinite and `SimpleQueue` as an
  unbounded FIFO.
- [Serial Monitor Rust](https://github.com/hacknus/serial-monitor-rust) lists
  unlimited command and data history as open work because an upper limit is
  needed to prevent huge memory usage.
- Practitioners report that plotting can become the slow side of a serial
  pipeline and that high-rate visualisation may drop partial data or freeze:
  [embedded discussion](https://www.reddit.com/r/embedded/comments/mxo9jy/problem_with_reading_and_realtime_visualization_of/),
  [Arduino plotting discussion](https://www.reddit.com/r/arduino/comments/d8krjg/serialprint/).

## Maintained alternatives checked

| Alternative | Overload fit | Dependency, platform, cost, and migration |
|---|---|---|
| Python `queue.Queue(maxsize)` | Provides the bounded, blocking primitive TermScope needs. | Python standard library with no new package, service, or license conflict. Selected for lossless replay/stdin backpressure. |
| [Serial Studio 4.0.3](https://github.com/Serial-Studio/Serial-Studio) | Advertises a high-throughput threaded/circular-buffer pipeline and CI performance gates. | Large Qt desktop telemetry suite. Source components are GPL-3.0-or-later; official binaries and Pro features use a commercial/trial model. Replacing a zero-dependency terminal tool has high install and workflow migration cost. |
| [SerialPlot 0.13.0](https://github.com/hyOzd/serialplot) | Mature real-time desktop plotter with explicit sample/window controls. | GPL-3.0 Qt 6 application for Windows/macOS/Linux; substantially heavier than an SSH-friendly Python terminal tool. |
| [Serial Monitor Rust 0.3.6](https://github.com/hacknus/serial-monitor-rust) | Cross-platform GUI plotting, but its own README still flags unlimited data history as a memory risk. | GPL-3.0 GUI with multi-megabyte platform bundles and a different interaction model. |
| [pyserial 3.5](https://github.com/pyserial/pyserial) | Reads the OS serial buffer; it does not choose an application queue limit or loss policy. | Existing optional BSD-style dependency. No migration is needed, but it cannot solve the consumer backlog by itself. |

No alternative code was copied. The smallest compatible fix reuses Python's
bounded queue and preserves TermScope's zero-dependency core.

## Selected policies

One overload rule cannot preserve both a current live view and lossless finite
input, so sources are separated by semantics:

- **Serial and demo are live:** the handoff holds 4,096 complete lines. On
  overflow, the oldest queued line is discarded and the newest line is kept.
  Showing stale backlog would make a live scope progressively less current.
- **File replay and stdin are lossless at this boundary:** their producer
  blocks in short interruptible waits until the consumer makes space. For a
  pipe, this naturally applies backpressure to the upstream process.
- **Loss is never silent:** the source counts dropped complete lines. The live
  header, status, snapshot, and exit summary expose that count. A recording
  made during `DROP N` is necessarily incomplete because recording happens
  after the handoff.
- **Stop remains bounded:** a stop event interrupts a producer waiting for
  queue space, so a full lossless queue cannot hang shutdown.

The 4,096 value equals one maximum render-loop drain and absorbs short scheduler
stalls without granting an indefinite backlog. It is a complete-line count,
not a byte cap; input line length remains a separate residual risk.

## What the counter does not prove

`DROP N` counts only complete lines discarded inside TermScope's own handoff.
It cannot see bytes or frames already lost in firmware, USB/UART hardware, an
operating-system driver, or pyserial. It also does not count lines deliberately
discarded while the user holds the plot, or lines rejected by the parser.
Therefore `DROP 0` is not evidence of a lossless acquisition or an independent
backup.

## Acceptance

Regression tests cover newest-line retention and exact counts, lossless
backpressure, shutdown while blocked, replay with a one-line queue, serial's
live policy, and user-visible warnings. The original 300,000-line experiment is
repeated after the change to verify that queue length stays at 4,096 and the
remaining 295,904 lines are reported as dropped. On the same Windows/Python
3.13 process, `tracemalloc` observed 255,072 current bytes after the change,
versus 20,283,226 before it. These numbers describe this synthetic fixture and
environment only; they are not a device throughput or cross-platform memory
benchmark.
