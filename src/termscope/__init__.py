"""termscope - a live oscilloscope for serial data, in your terminal.

Plots whatever a microcontroller prints, without a GUI and without a fixed
output format. Point it at a board::

    termscope /dev/ttyUSB0

or try it with no hardware at all::

    termscope --demo

The public API is small enough to embed in another tool: build a
:class:`~termscope.buffers.ChannelSet`, feed it from a
:class:`~termscope.parser.StreamParser`, and hand both to a
:class:`~termscope.plot.Renderer`.
"""

from __future__ import annotations

__version__ = "0.1.0"
__all__ = [
    "Canvas",
    "ChannelBuffer",
    "ChannelSet",
    "Palette",
    "ParsedLine",
    "Renderer",
    "StreamParser",
    "__version__",
    "main",
]


def __getattr__(name: str):
    """Resolve the public names lazily.

    Importing the package should not drag in the terminal or threading
    machinery -- ``import termscope`` runs during CLI startup and in tests
    that only want the parser.
    """
    if name == "Canvas":
        from .canvas import Canvas

        return Canvas
    if name in ("ChannelBuffer", "ChannelSet"):
        from . import buffers

        return getattr(buffers, name)
    if name in ("StreamParser", "ParsedLine"):
        from . import parser

        return getattr(parser, name)
    if name == "Renderer":
        from .plot import Renderer

        return Renderer
    if name == "Palette":
        from .palette import Palette

        return Palette
    if name == "main":
        from .cli import main

        return main
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
