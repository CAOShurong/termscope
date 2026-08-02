"""Terminal setup, teardown and keyboard input, on Windows and POSIX alike.

Nothing here is exotic, but it is the part that most terminal tools get
subtly wrong: leaving the cursor hidden after a crash, or restoring cooked
mode only on the happy path. :class:`Terminal` is a context manager, and its
exit path runs even when the body raised.

On Windows 10+ the console understands ANSI once
``ENABLE_VIRTUAL_TERMINAL_PROCESSING`` is switched on, so a single escape-code
renderer covers every supported platform and there is no second Win32 path.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import sys

__all__ = [
    "ESC",
    "Terminal",
    "ensure_utf8",
    "supports_braille",
    "supports_color",
]

ESC = "\x1b"

IS_WINDOWS = os.name == "nt"

# -- escape sequences ------------------------------------------------------

ALT_SCREEN_ON = f"{ESC}[?1049h"
ALT_SCREEN_OFF = f"{ESC}[?1049l"
CURSOR_HIDE = f"{ESC}[?25l"
CURSOR_SHOW = f"{ESC}[?25h"
CLEAR_SCREEN = f"{ESC}[2J"
CURSOR_HOME = f"{ESC}[H"
RESET_ATTRS = f"{ESC}[0m"


def _enable_windows_vt() -> bool:
    """Turn on ANSI interpretation for the current console.

    Returns False on consoles too old to support it, in which case the caller
    falls back to plain output.
    """
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        if handle in (0, -1):
            return False
        mode = wintypes.DWORD()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        enable_vt = 0x0004  # ENABLE_VIRTUAL_TERMINAL_PROCESSING
        if mode.value & enable_vt:
            return True
        return bool(kernel32.SetConsoleMode(handle, mode.value | enable_vt))
    except Exception:
        return False


def supports_color(stream=None) -> bool:
    """Whether it is safe to emit SGR colour on ``stream``.

    Honours the ``NO_COLOR`` convention and refuses to colour a redirected
    stream, so ``termscope --once > plot.txt`` produces clean text.
    """
    stream = stream or sys.stdout
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("TERM") == "dumb":
        return False
    if not hasattr(stream, "isatty") or not stream.isatty():
        return False
    if IS_WINDOWS:
        return _enable_windows_vt()
    return True


def ensure_utf8(stream=None) -> bool:
    """Switch ``stream`` to UTF-8 if it is not already.

    Python picks the *locale* encoding for a console or a pipe, which on a
    Chinese or Japanese Windows install is GBK or CP932 -- neither of which
    can encode a braille glyph. The terminal itself is perfectly capable of
    displaying one; it is the encoder in between that fails, so the fix is to
    re-point the encoder rather than to give up on the character.

    Returns True if the stream can encode braille afterwards.
    """
    stream = stream or sys.stdout
    if supports_braille(stream):
        return True
    try:
        stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        return False
    return supports_braille(stream)


def supports_braille(stream=None) -> bool:
    """Whether ``stream``'s encoder can represent the braille block."""
    stream = stream or sys.stdout
    encoding = getattr(stream, "encoding", None)
    if not encoding:
        return False
    try:
        "⣿⠁".encode(encoding)
    except (LookupError, UnicodeEncodeError):
        return False
    return True


def terminal_size(default: tuple[int, int] = (80, 24)) -> tuple[int, int]:
    """Current ``(columns, rows)``, falling back when there is no tty."""
    try:
        size = shutil.get_terminal_size(fallback=default)
        return max(20, size.columns), max(8, size.lines)
    except Exception:
        return default


class Terminal:
    """Raw-mode terminal on the alternate screen, restored on the way out."""

    def __init__(self, *, alt_screen: bool = True, stream=None) -> None:
        self.stream = stream or sys.stdout
        self.alt_screen = alt_screen
        self.color = supports_color(self.stream)
        self._saved_termios = None
        self._raw = False

    # -- context manager ---------------------------------------------------

    def __enter__(self) -> Terminal:
        self.setup()
        return self

    def __exit__(self, *exc_info) -> bool:
        self.restore()
        return False  # never swallow the exception

    def setup(self) -> None:
        self._enter_raw()
        if self.alt_screen:
            self.write(ALT_SCREEN_ON)
        self.write(CURSOR_HIDE + CLEAR_SCREEN + CURSOR_HOME)
        self.flush()

    def restore(self) -> None:
        # Written in one go so a half-restored terminal is not possible if
        # the process is killed between calls.
        tail = CURSOR_SHOW + RESET_ATTRS
        if self.alt_screen:
            tail += ALT_SCREEN_OFF
        try:
            self.write(tail)
            self.flush()
        except Exception:
            pass
        self._exit_raw()

    # -- raw mode ----------------------------------------------------------

    def _enter_raw(self) -> None:
        if IS_WINDOWS or self._raw:
            return
        try:
            import termios
            import tty

            fd = sys.stdin.fileno()
            self._saved_termios = termios.tcgetattr(fd)
            tty.setcbreak(fd)
            self._raw = True
        except Exception:
            # No tty (piped input, CI). Key handling degrades to nothing,
            # which is exactly right for a non-interactive run.
            self._saved_termios = None

    def _exit_raw(self) -> None:
        if self._saved_termios is None:
            return
        try:
            import termios

            termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, self._saved_termios)
        except Exception:
            pass
        finally:
            self._saved_termios = None
            self._raw = False

    # -- output ------------------------------------------------------------

    def write(self, text: str) -> None:
        self.stream.write(text)

    def flush(self) -> None:
        with contextlib.suppress(Exception):
            self.stream.flush()

    def size(self) -> tuple[int, int]:
        return terminal_size()

    # -- input -------------------------------------------------------------

    def read_key(self) -> str | None:
        """One keypress, or None if nothing is waiting. Never blocks.

        Multi-byte escape sequences (arrow keys) are collapsed to friendly
        names: ``up``, ``down``, ``left``, ``right``, ``home``, ``end``.
        """
        if IS_WINDOWS:
            return self._read_key_windows()
        return self._read_key_posix()

    def _read_key_windows(self) -> str | None:
        try:
            import msvcrt
        except ImportError:
            return None
        if not msvcrt.kbhit():
            return None
        ch = msvcrt.getwch()
        if ch in ("\x00", "\xe0"):  # extended key: a second read gives the code
            if not msvcrt.kbhit():
                return None
            code = msvcrt.getwch()
            return {
                "H": "up",
                "P": "down",
                "K": "left",
                "M": "right",
                "G": "home",
                "O": "end",
            }.get(code)
        return ch

    def _read_key_posix(self) -> str | None:
        import select

        try:
            fd = sys.stdin.fileno()
        except (AttributeError, ValueError):
            return None
        if not select.select([fd], [], [], 0)[0]:
            return None
        try:
            ch = os.read(fd, 1).decode("utf-8", errors="replace")
        except OSError:
            return None
        if ch != ESC:
            return ch
        # Possible CSI sequence. Read what is immediately available; a lone
        # Esc leaves nothing behind and falls through as "escape".
        if not select.select([fd], [], [], 0.02)[0]:
            return "escape"
        try:
            rest = os.read(fd, 8).decode("utf-8", errors="replace")
        except OSError:
            return "escape"
        return {
            "[A": "up",
            "[B": "down",
            "[C": "right",
            "[D": "left",
            "[H": "home",
            "[F": "end",
            "OH": "home",
            "OF": "end",
        }.get(rest, "escape")
