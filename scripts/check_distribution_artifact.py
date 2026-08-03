#!/usr/bin/env python3
"""Verify the built distribution and every identity-bearing manifest."""

from __future__ import annotations

import configparser
import json
import sys
import zipfile
from email.parser import Parser
from pathlib import Path

import tomllib

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
EXPECTED_NAME = "hypermnesia-mcp-viz"
EXPECTED_VERSION = "2.8.0"
EXPECTED_REGISTRY_ID = f"io.github.cdeust/{EXPECTED_NAME}"
EXPECTED_ENTRY_POINT = "cortex_viz.__main__:main"


def require_one_wheel() -> Path:
    wheels = sorted(DIST.glob("*.whl"))
    if len(wheels) != 1:
        raise AssertionError(f"expected one wheel in {DIST}, found {wheels}")
    return wheels[0]


def wheel_member(wheel: zipfile.ZipFile, suffix: str) -> str:
    matches = [name for name in wheel.namelist() if name.endswith(suffix)]
    if len(matches) != 1:
        raise AssertionError(f"expected one {suffix} in wheel, found {matches}")
    return wheel.read(matches[0]).decode("utf-8")


def main() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert project["project"]["name"] == EXPECTED_NAME
    assert project["project"]["version"] == EXPECTED_VERSION
    assert project["project"]["scripts"] == {
        "cortex-viz": EXPECTED_ENTRY_POINT,
        "hypermnesia-mcp-viz": EXPECTED_ENTRY_POINT,
    }

    server = json.loads((ROOT / "server.json").read_text())
    assert server["name"] == EXPECTED_REGISTRY_ID
    assert server["version"] == EXPECTED_VERSION
    assert server["packages"] == [
        {
            "registryType": "pypi",
            "identifier": EXPECTED_NAME,
            "version": EXPECTED_VERSION,
            "runtimeHint": "python",
            "transport": {"type": "stdio"},
        }
    ]

    plugin = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text())
    assert plugin["name"] == "cortex-viz", "display/plugin compatibility changed"
    assert plugin["version"] == EXPECTED_VERSION

    with zipfile.ZipFile(require_one_wheel()) as wheel:
        metadata = Parser().parsestr(wheel_member(wheel, ".dist-info/METADATA"))
        assert metadata["Name"] == EXPECTED_NAME
        assert metadata["Version"] == EXPECTED_VERSION

        entry_points = configparser.ConfigParser()
        entry_points.read_string(wheel_member(wheel, ".dist-info/entry_points.txt"))
        assert dict(entry_points["console_scripts"]) == {
            "cortex-viz": EXPECTED_ENTRY_POINT,
            "hypermnesia-mcp-viz": EXPECTED_ENTRY_POINT,
        }

    print(f"distribution identity OK: {EXPECTED_NAME} {EXPECTED_VERSION}")


if __name__ == "__main__":
    try:
        main()
    except (AssertionError, KeyError, OSError, ValueError) as exc:
        print(f"distribution identity FAILED: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
