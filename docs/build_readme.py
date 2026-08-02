#!/usr/bin/env python3
"""Regenerate README.md, capturing every plot from a real termscope run.

The README claims its screenshots are produced by the tool itself. This script
is what makes that true: it runs ``termscope --once`` at a fixed terminal size
for each figure and substitutes the output into the template. Nothing is
hand-transcribed, so a rendering change shows up as a README diff.

    python docs/build_readme.py           # rewrite README.md
    python docs/build_readme.py --check   # fail if it would change (for CI)
"""

from __future__ import annotations

import argparse
import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "docs" / "readme_template.md"
README = ROOT / "README.md"

# Each figure: placeholder -> (terminal size, extra CLI arguments).
FIGURES = {
    "<!--SHOT_SHARED-->": ((78, 22), ["--window", "8"]),
    "<!--SHOT_SPLIT-->": ((78, 24), ["--window", "8", "--split"]),
    "<!--SHOT_ASCII-->": ((78, 16), ["--window", "6", "--charset", "ascii"]),
}

# Figures replay a committed capture rather than running the live simulator.
# The capture carries its own timestamps, so the frame drawn from it is
# identical on every machine and every run -- which is what lets --check be
# a meaningful CI gate rather than a coin flip.
FIXTURE = ROOT / "examples" / "balancing-robot.csv"
CAPTURE_SECONDS = "3"


def capture(size: tuple[int, int], extra: list[str]) -> str:
    """Render one frame at a fixed size and return it."""
    env = dict(os.environ)
    env["COLUMNS"], env["LINES"] = str(size[0]), str(size[1])
    env["PYTHONPATH"] = str(ROOT / "src")
    env["PYTHONIOENCODING"] = "utf-8"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "termscope",
            "--replay",
            str(FIXTURE.relative_to(ROOT)),
            "--once",
            CAPTURE_SECONDS,
            "--color",
            "none",
            *extra,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        cwd=ROOT,
        check=False,
    )
    if result.returncode != 0:
        raise SystemExit(f"capture failed ({' '.join(extra)}): {result.stderr.strip()}")
    return result.stdout.rstrip("\n")


def build() -> str:
    text = TEMPLATE.read_text(encoding="utf-8")
    for placeholder, (size, extra) in FIGURES.items():
        if placeholder not in text:
            raise SystemExit(f"template is missing {placeholder}")
        frame = capture(size, extra)
        text = text.replace(placeholder, f"```\n{frame}\n```")
    return text


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true", help="exit non-zero if README.md is out of date"
    )
    args = parser.parse_args()

    generated = build()
    if args.check:
        current = README.read_text(encoding="utf-8") if README.exists() else ""
        if current != generated:
            print("README.md is out of date; run: python docs/build_readme.py")
            return 1
        print("README.md is up to date.")
        return 0

    README.write_text(generated, encoding="utf-8")
    print(f"wrote {README.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
