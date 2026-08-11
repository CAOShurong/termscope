# Why TermScope added opt-in serial reconnection

Research date: 2026-08-11. This note records the evidence and trade-offs used
for v0.3.0; it is not a claim of adoption or independent hardware validation.

## The falsifiable gap

Before v0.3.0, `SerialSource` opened a port once and converted every later read
exception into a fatal `SourceError`. The concrete failure was therefore:

> If a board reset, firmware upload, or brief USB disconnect invalidated the
> serial handle, TermScope ended instead of continuing the plot when the same
> port returned.

This is part of the normal embedded-development loop, not an invented edge
case. Arduino documents that its IDE closes Serial Monitor before an upload and
opens it again afterward; it also documents that opening the serial connection
can reset some boards. Practitioner reports describe serial monitors remaining
disconnected or failing to reconnect after upload:

- [Arduino Support: If your board runs the sketch twice](https://support.arduino.cc/hc/en-us/articles/4839084114460-If-your-board-runs-the-sketch-twice)
- [Arduino Forum: Serial Plotter/Monitor show disconnected when they are not](https://forum.arduino.cc/t/rc3-serial-plotter-monitor-show-disconnected-when-they-are-not/947524)
- [Visual Micro forum: Auto re-connect serial monitor after upload fail](https://www.visualmicro.com/forums/YaBB.pl?num=1702793567%2F3)

## Maintained alternatives checked

The alternatives solve adjacent problems, but none is a drop-in way to keep
TermScope's terminal plot and recording session alive. Metadata and current
release assets were checked through the GitHub API on the research date.

| Alternative | Reconnect fit | Platform and operating cost | License and migration |
|---|---|---|---|
| [Arduino IDE](https://github.com/arduino/arduino-ide) | Its own monitor is closed and reopened around upload. | Cross-platform desktop IDE; a GUI session is required. | AGPL-3.0; replacing a terminal/SSH workflow with the full IDE is a high migration cost. |
| [tio 3.9](https://github.com/tio/tio) | Mature automatic reconnect, stable by-ID and topology guidance. | Small native terminal program on Linux/macOS and Windows through MSYS2, but it is a serial terminal, not a plotter. | GPL-2.0-or-later. It can feed TermScope through a pipe only by becoming the port owner, adding another process and losing TermScope's direct write path. |
| [serial-monitor-rust 0.3.6](https://github.com/hacknus/serial-monitor-rust) | Plotting plus automatic reconnect. | Cross-platform GUI. Current release assets were about 8 MB on macOS and 100-113 MB on Linux; the latest release exposed no Windows asset when checked. | GPL-3.0. A sound alternative for GUI users, but a different interface, packaging footprint, and SSH fit. |
| [SimpleCom 1.2.7](https://github.com/YaSuenag/SimpleCom) | Explicit reconnect pause and timeout controls. | Lightweight Windows console program, with no plotter and no macOS/Linux build. | GPL-2.0. Replacing TermScope loses plotting and cross-platform parity. |
| [pyserial 3.5](https://github.com/pyserial/pyserial) | Provides the low-level open/read/close primitives, not an application reconnect policy. | Optional Python dependency already used by TermScope; no service or account. | BSD-3-Clause-compatible license. Reusing it avoids another runtime dependency. |

No GPL implementation was copied. The selected change composes TermScope's
existing pyserial adapter with a small retry state and adds no dependency,
cloud service, or network cost. The published 0.2.0 wheel was about 52 KB; this
change preserves the zero-dependency core and optional serial extra.

## Selected behavior and boundaries

- Reconnection is opt-in with `--reconnect [SEC]`; scripts keep the prior
  fail-fast default.
- The first retry is one second later unless the user provides another
  strictly positive interval.
- Only the originally resolved operating-system path is reopened. TermScope
  does not scan for another port, because silently attaching a different board
  would be a data-integrity and device-command risk.
- Missing pyserial and invalid settings are permanent errors and are not
  retried. A stop request interrupts the wait immediately.
- Plot history stays in memory. An unterminated byte fragment is discarded at
  the session boundary so bytes from two device sessions are never fabricated
  into one line.
- A port path does not authenticate a physical device. Linux users can reduce
  accidental renumbering with `/dev/serial/by-id`, but local path replacement
  remains outside TermScope's trust model.

## Acceptance and remaining uncertainty

Automated tests use a pyserial-shaped fixture to prove the exact state
transition: one connection emits a line and fails, the next emits another
line, and both reach the same source without a fatal error. Separate tests
prove default fail-fast behavior, prompt cancellation, permanent-error
handling, and partial-line isolation.

Those tests verify TermScope's retry policy, not every USB driver or board.
Physical unplug/reset behavior still requires independent fixtures on Windows,
macOS, and Linux. Reports should name the board, USB bridge, operating system,
port path, and whether that path changed after re-enumeration.
