# Contributing to termscope

Thanks for taking a look. Issues and pull requests are both welcome, including
small ones — a fix for a serial format that does not parse is exactly the kind
of report this project needs, because there is no way to collect them except
from people with the hardware in front of them.

## Getting set up

```bash
git clone https://github.com/CAOShurong/termscope
cd termscope
python -m pip install -e ".[dev]"
```

The test suite is standard-library only, so it also runs against a bare
interpreter with nothing installed:

```bash
PYTHONPATH=src python -m unittest discover -s tests
```

That is not an accident, and CI checks it. The core deliberately has zero
dependencies — pyserial is needed only to open a physical port, which is why
`--demo`, pipes and log replay all work without it.

## Before you open a pull request

```bash
ruff check src tests docs
ruff format src tests docs
python -m unittest discover -s tests
python docs/build_readme.py --check
```

That last one matters: every plot in the README is rendered by the tool from
[`examples/balancing-robot.csv`](examples/balancing-robot.csv). If you change
anything about rendering, run `python docs/build_readme.py` and commit the
result alongside your change.

## Reporting a stream that does not parse

This is the most useful bug report you can file. Please include:

1. A dozen or so raw lines exactly as the device sends them — `termscope --raw`
   or `termscope --record-raw out.log` will capture them verbatim.
2. What you expected the channels to be called.
3. Your board and toolchain, if it is relevant.

## A few things that are decided on purpose

These come up in review often enough to be worth stating up front. None of them
are sacred, but changing one needs an argument, not just a patch.

**No dual-axis plots.** Two y-scales on one set of axes align arbitrarily, so
the chart invents a correlation the data does not contain. Channels of
different magnitude get `--split` — one panel each, own axis, physically
separate. This is why there is no `--scale-per-channel`.

**Colours are never cycled past eight.** There is no ninth hue that stays
distinguishable under simulated protanopia and deuteranopia, so a ninth channel
does not get one — it stays hidden until the user chooses it. The eight slots
come from a documented palette and are validated for lightness band, chroma
floor, contrast and CVD separation. If you change one, re-validate the set; do
not eyeball it.

**A channel's colour is fixed when it first appears.** Hiding one trace must
not repaint the others. Colour follows the entity, never its position in a
filtered list.

**Colour is never the only way to read a value.** The legend always shows each
channel's name and current reading as text. Several of the light-mode hues sit
below 3:1 contrast, which is only permissible because of that.

**The parser must never raise.** Serial input is arbitrary bytes from a device
that may be half-programmed. An unparseable line is normal input, not an error.

**No dependencies in the core.** A new import in `src/termscope/` that is not
standard library needs a strong reason.

## Style

`ruff format` decides formatting; there is nothing to argue about. Beyond that:
comments should say *why*, since the *what* is usually already in the code.

## License

Contributions are accepted under the [MIT license](LICENSE).
