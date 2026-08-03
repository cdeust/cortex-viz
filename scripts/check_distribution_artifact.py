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

from cortex_viz.identity import DISTRIBUTION_NAME, MCP_REGISTRY_ID, VERSION

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
EXPECTED_ENTRY_POINT = "cortex_viz.__main__:main"


def require(condition: bool, message: str) -> None:
    """Raise even when Python assertions are disabled."""
    if not condition:
        raise ValueError(message)


def require_one_wheel() -> Path:
    wheels = sorted(DIST.glob("*.whl"))
    require(len(wheels) == 1, f"expected one wheel in {DIST}, found {wheels}")
    return wheels[0]


def wheel_member(wheel: zipfile.ZipFile, suffix: str) -> str:
    matches = [name for name in wheel.namelist() if name.endswith(suffix)]
    require(len(matches) == 1, f"expected one {suffix} in wheel, found {matches}")
    return wheel.read(matches[0]).decode("utf-8")


def main() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())
    require(project["project"]["name"] == DISTRIBUTION_NAME, "project name drifted")
    require(project["project"]["version"] == VERSION, "project version drifted")
    require(
        project["project"]["scripts"]
        == {
            "cortex-viz": EXPECTED_ENTRY_POINT,
            "hypermnesia-mcp-viz": EXPECTED_ENTRY_POINT,
        },
        "console entry points drifted",
    )

    server = json.loads((ROOT / "server.json").read_text())
    require(server["name"] == MCP_REGISTRY_ID, "MCP Registry ID drifted")
    require(server["version"] == VERSION, "MCP Registry version drifted")
    require(
        server["packages"]
        == [
            {
                "registryType": "pypi",
                "identifier": DISTRIBUTION_NAME,
                "version": VERSION,
                "runtimeHint": "python",
                "transport": {"type": "stdio"},
            }
        ],
        "MCP Registry package declaration drifted",
    )

    plugin = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text())
    require(plugin["name"] == "cortex-viz", "display/plugin compatibility changed")
    require(plugin["version"] == VERSION, "Claude plugin version drifted")

    readme = (ROOT / "README.md").read_text()
    require(f"version-{VERSION}-brightgreen" in readme, "README badge drifted")
    require(f'alt="Version {VERSION}"' in readme, "README badge alt drifted")

    with zipfile.ZipFile(require_one_wheel()) as wheel:
        metadata = Parser().parsestr(wheel_member(wheel, ".dist-info/METADATA"))
        require(metadata["Name"] == DISTRIBUTION_NAME, "wheel name drifted")
        require(metadata["Version"] == VERSION, "wheel version drifted")

        entry_points = configparser.ConfigParser()
        entry_points.read_string(wheel_member(wheel, ".dist-info/entry_points.txt"))
        require(
            dict(entry_points["console_scripts"])
            == {
                "cortex-viz": EXPECTED_ENTRY_POINT,
                "hypermnesia-mcp-viz": EXPECTED_ENTRY_POINT,
            },
            "wheel console entry points drifted",
        )

    print(f"distribution identity OK: {DISTRIBUTION_NAME} {VERSION}")


if __name__ == "__main__":
    try:
        main()
    except (AssertionError, KeyError, OSError, ValueError) as exc:
        print(f"distribution identity FAILED: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
