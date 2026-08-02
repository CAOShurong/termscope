# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

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

[Unreleased]: https://github.com/TeresaCSR/termscope/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/TeresaCSR/termscope/releases/tag/v0.1.0
