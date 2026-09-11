# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- `--ylim LO HI` pins the y-axis instead of autoscaling, so a settling PID
  error keeps a stable scale. Interactive sessions still toggle autoscale
  with `a`; split mode honours the same pinned range.
- `--stats` (key `s`) shows min / max / mean next to each legend value.

## [0.4.1] — 2026-08-12

### Fixed

- Labelled telemetry detection now requires a token boundary, so the `T08:27`
  fragment inside an ISO timestamp such as `2026-08-11T08:27:28Z` is ignored
  instead of creating a fictitious `T08` channel in timestamped logs.
- Long diagnostic prefixes no longer trigger quadratic labelled-token scans;
  replaying a 70 KB line followed by valid telemetry now completes promptly.

## [0.4.0] — 2026-08-11

### Added

- Observable overload reporting. The live header, status line, snapshot, and
  exit summary now show how many complete input lines TermScope dropped.

### Changed

- Replaced the unbounded producer/consumer queue with a 4,096-line handoff.
  Live serial and demo sources discard the oldest queued line under sustained
  overload so the plot catches up to current data; file replay and stdin use
  backpressure and remain lossless at this boundary.
- Documented that `DROP 0` measures only TermScope's own queue and cannot prove
  that firmware, USB/UART hardware, drivers, or pyserial delivered every byte.

## [0.3.0] — 2026-08-11

### Added

- Opt-in serial recovery with `--reconnect [SEC]`. TermScope now keeps the
  plot and recording session alive across a board reset, firmware upload, or
  brief USB disconnect while retrying the same operating-system port path.
- The live header reports `reconnecting` while the device is unavailable.

### Safety

- Fail-fast behavior remains the default. Missing pyserial and invalid serial
  settings are never retried, retry waits stop promptly on exit, and a partial
  line is not joined across two device sessions.
- Reconnection does not scan for a substitute device or authenticate the
  hardware behind a reused path; this boundary is documented in the README
  and security policy.

## [0.2.0] — 2026-08-09

### Added

- Native support for Teleplot's serial time-series subset: single values,
  explicit millisecond timestamps, units, and timestamped point batches.
- Batched samples now travel through parsing, plotting, filtering, and CSV
  recording without discarding all but one point.

### Fixed

- Timestamped Teleplot messages such as `>temp:1000:23.5` no longer plot the
  timestamp (`1000`) as if it were the measurement (`23.5`). Unsupported XY,
  text, no-plot, clear-history, log, and 3D messages are kept out of the time
  series instead of being partially interpreted by the labelled-value parser.

## [0.1.1] — 2026-08-03

### Fixed

- Repository and image URLs, after the GitHub account was renamed from
  `TeresaCSR` to `CAOShurong`. GitHub redirects repository links, but
  `raw.githubusercontent.com` does not, so the screenshots in the project
  description on PyPI stopped loading. A published description is a snapshot,
  which is why this needs a release rather than a commit.

## [0.1.0] — 2026-08-02

First release.

### Added

- **Live plotting of serial data in the terminal.** Traces are drawn with
  braille glyphs, giving a 2×4 grid of dots per character cell.
- **Automatic format detection** for the four shapes firmware actually prints:
  `key:value` pairs, bare numbers (Arduino Serial Plotter style), one JSON
  object per line, and CSV with a header row naming the columns.
- **Tolerant parsing.** Debug output interleaved with telemetry is skipped
  rather than fatal; ANSI colour and control bytes are stripped; NaN and inf
  are dropped before they can collapse the axis; a detected format is locked
  in so one bad line cannot re-key every channel.
- **Sources**: serial port (with auto-detection), stdin, and log replay.
- **A simulated balancing robot** under `--demo`, so the tool is usable with no
  hardware attached — and testable in CI.
- **Two multi-channel layouts**: a shared y-axis, and `--split` small multiples
  with one independently scaled panel per channel.
- **Stream-supplied time base.** `time_s`, `millis`, `micros` and similar
  columns are detected and used as the x-axis in place of arrival time, so a
  replay reproduces the timing of the original capture.
- **Recording** to CSV or raw log while plotting, with late-appearing channels
  given their own column and earlier rows left empty rather than back-filled.
- **Interactive controls**: hold, autoscale, per-channel visibility, window
  length, y zoom and pan, gridlines, raw text view, and recording.
- **`--once`** renders a single frame to stdout for use in CI or an issue
  report. Every plot in the README is generated this way.
- **A validated colour palette.** Eight categorical slots, checked for
  lightness band, chroma floor, surface contrast and separation under
  simulated protanopia and deuteranopia, in both dark and light variants.
  Colours are assigned per channel on first sight and never reassigned or
  cycled.
- **Graceful degradation**: 24-bit colour down through 256, 16 and none;
  braille down to ASCII where the output encoding cannot carry it. On a
  CJK-locale Windows install the output stream is switched to UTF-8 rather
  than falling back unnecessarily.
- **Zero dependencies** in the core; pyserial only for opening a real port.

[Unreleased]: https://github.com/CAOShurong/termscope/compare/v0.4.0...HEAD
[0.4.0]: https://github.com/CAOShurong/termscope/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/CAOShurong/termscope/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/CAOShurong/termscope/compare/v0.1.1...v0.2.0
[0.1.1]: https://github.com/CAOShurong/termscope/releases/tag/v0.1.1
[0.1.0]: https://github.com/CAOShurong/termscope/releases/tag/v0.1.0
