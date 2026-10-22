#!/usr/bin/env python3
"""Merge Cobertura reports (coverage.py and vitest/istanbul) into one file.

The single CI pipeline reads one ``coverage.xml`` at the repository root and
gates on its root ``line-rate`` (zetetic-team-subagents
docs/superpowers/specs/2026-10-22-single-ci-design.md, decision 7: 90 % of
what unit reports, one merged Cobertura file, no per-language gate). Every
``<package>`` of every input is kept; the root totals are the sums.

    python3 scripts/merge_cobertura.py coverage-py.xml \
        coverage/cobertura-coverage.xml -o coverage.xml
"""

from __future__ import annotations

import argparse
import sys
import time
import xml.etree.ElementTree as ET  # noqa: S405 - reports this job wrote itself
from pathlib import Path


def _totals(root: ET.Element) -> tuple[int, int]:
    """Lines valid and covered: the root attributes, else counted from <line>."""
    valid, covered = root.get("lines-valid"), root.get("lines-covered")
    if valid is not None and covered is not None:
        return int(valid), int(covered)
    lines = list(root.iter("line"))
    return len(lines), sum(1 for line in lines if int(line.get("hits", "0")) > 0)


def merge(paths: list[Path]) -> ET.Element:
    valid = covered = 0
    out = ET.Element("coverage")
    sources = ET.SubElement(out, "sources")
    packages = ET.SubElement(out, "packages")
    for path in paths:
        root = ET.parse(path).getroot()  # noqa: S314 - see the import note
        lines_valid, lines_covered = _totals(root)
        valid += lines_valid
        covered += lines_covered
        for source in root.iter("source"):
            sources.append(source)
        found = root.find("packages")
        if found is not None:
            packages.extend(list(found))
        print(f"{path}: {lines_covered}/{lines_valid} lines", file=sys.stderr)
    rate = covered / valid if valid else 0.0
    out.set("line-rate", f"{rate:.4f}")
    out.set("lines-valid", str(valid))
    out.set("lines-covered", str(covered))
    out.set("branch-rate", "0")
    out.set("version", "merged")
    out.set("timestamp", str(int(time.time())))
    print(f"merged: {covered}/{valid} lines, {rate * 100:.1f} %", file=sys.stderr)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("inputs", nargs="+", type=Path, help="Cobertura XML reports")
    parser.add_argument("-o", "--output", required=True, type=Path)
    args = parser.parse_args(argv)
    missing = [str(p) for p in args.inputs if not p.is_file()]
    if missing:
        print(f"missing report(s): {', '.join(missing)}", file=sys.stderr)
        return 1
    tree = ET.ElementTree(merge(args.inputs))
    tree.write(args.output, encoding="utf-8", xml_declaration=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
