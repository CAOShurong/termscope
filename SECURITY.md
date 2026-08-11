# Security policy

## Supported versions

Security fixes are made on the latest released minor version. Upgrade to the
latest release before reporting a problem that may already have been fixed.

## Reporting a vulnerability

Please use GitHub's **Security > Report a vulnerability** flow so sensitive
details are not posted in a public issue. For non-sensitive hardening ideas,
open a normal GitHub issue.

Include the TermScope and Python versions, operating system, exact command
(with private paths or values removed), the smallest input that reproduces the
problem, and the observed impact. Do not attach telemetry that contains
credentials, private device identifiers, or personal data.

## Trust and data boundaries

- TermScope is a local program. It does not upload telemetry or require a
  network account. CSV and raw recordings are plain local files at the path
  the user chooses; they are not encrypted by TermScope.
- Serial, stdin, pipe, and replay input are untrusted text. TermScope does not
  execute input as code, and the parser strips terminal control bytes before
  display. Resource limits cap tracked channels, buffered samples, per-frame
  drain work, and unterminated serial fragments.
- `--reconnect` retries only the operating-system path selected at startup. It
  does not discover a replacement device. The path itself is not an identity
  check: a different local device can later receive the same `COM` or `/dev`
  name. Use a stable by-ID path where the operating system provides one, and
  leave reconnection disabled when path reuse is outside your trust boundary.
- A clean run proves only that TermScope parsed and displayed the bytes it
  received. It does not verify sensor accuracy, device identity, transmission
  completeness, or that a recording is an independent backup.

## Dependency scope

The plotting, parsing, replay, and terminal core uses only the Python standard
library. Physical serial access is the optional `pyserial` dependency. Build
and test tools are development-only and are not installed with the runtime
package.
