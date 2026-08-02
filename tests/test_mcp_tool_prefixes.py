"""Repo-wide gate: every plugin-scoped MCP tool prefix named anywhere in this
repository must be one the host can actually resolve.

Why this exists. The host derives a plugin-scoped MCP tool name as
``mcp__plugin_<plugin-name>_<server-key>__<tool>`` -- BOTH halves are in the
name. Cortex v4.15.0 renamed its plugin ``cortex`` -> ``hypermnesia-mcp`` while
keeping the server key ``cortex``, and the rename commit asserted that "tool
names ... are untouched" because the server key had not moved. That premise was
false. This repo survived only by luck (``trace_source._memory_op`` scopes on
the substring "cortex", which both spellings contain), and the survival was
invisible: an unrecognized name there returns ``None`` and the memory op is
silently dropped from the trace.

A prefix that lives in a docstring or a UI string cannot fail a type-check or a
unit test, so nothing here could have caught a rename that DID break us. This
test is that missing gate. It is a pytest rather than a new CI workflow so it
runs inside the existing ``test`` job, which already gates every PR.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

#: Prefixes the host can resolve for this repo's declared dependencies.
#:
#: Adding a row is a deliberate act: it asserts a plugin by that name, exposing
#: an MCP server by that key, is actually installed at runtime. Derive it from
#: the dependency's own ``.claude-plugin/plugin.json`` (``name`` + the key under
#: ``mcpServers``) -- never from memory.
KNOWN_MCP_PREFIXES: frozenset[str] = frozenset(
    {
        # Cortex memory. plugin.json: name "hypermnesia-mcp", mcpServers key
        # "cortex". Renamed from "cortex" in v4.15.0 over a directory collision.
        "mcp__plugin_hypermnesia-mcp_cortex__",
    }
)

#: A line carrying this marker may name a prefix outside the allowlist.
#:
#: Reserved for text that must quote a dead or foreign spelling on purpose --
#: migration notes, and test fixtures asserting that a pre-rename or
#: other-server name is handled correctly. It is NOT an escape hatch for a
#: stale reference: an unmarked stale prefix is the defect this gate fails on.
LEGACY_MARKER = "mcp-prefix-allow-legacy"

PREFIX_RE = re.compile(r"mcp__plugin_[a-zA-Z0-9_.\-]+?__")

_BINARY_SUFFIXES = frozenset(
    {
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".ico",
        ".pdf",
        ".zip",
        ".gz",
        ".woff",
        ".woff2",
        ".ttf",
    }
)


def _tracked_files() -> list[Path]:
    """Repo files git tracks -- excludes .venv/node_modules by construction."""
    out = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return [REPO_ROOT / p for p in out.split("\0") if p]


def _scan() -> tuple[list[str], set[str], int]:
    """Return (offences, prefixes_seen, files_scanned)."""
    offences: list[str] = []
    seen: set[str] = set()
    scanned = 0

    for path in _tracked_files():
        if path.suffix.lower() in _BINARY_SUFFIXES:
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        scanned += 1
        if "mcp__plugin_" not in content:
            continue
        rel = path.relative_to(REPO_ROOT)
        for lineno, line in enumerate(content.splitlines(), start=1):
            for prefix in PREFIX_RE.findall(line):
                seen.add(prefix)
                if prefix in KNOWN_MCP_PREFIXES or LEGACY_MARKER in line:
                    continue
                offences.append(f"{rel}:{lineno}\n    {prefix}\n    > {line.strip()}")
    return offences, seen, scanned


def test_only_resolvable_mcp_prefixes_are_named() -> None:
    offences, _, _ = _scan()
    assert not offences, (
        "Unresolvable MCP tool prefix(es). The host drops these SILENTLY -- no "
        "error will ever tell you.\n"
        + "\n".join(offences)
        + f"\n\nFix the reference, or if the dead spelling is quoted on purpose, "
        f'add the marker "{LEGACY_MARKER}" to that line.'
    )


def test_scan_is_not_vacuous() -> None:
    """A green result must mean "checked and clean", never "checked nothing".

    If the scan silently matched nothing -- wrong root, git failure, regex drift
    -- the assertion above passes while verifying nothing. That is precisely the
    silent-success failure mode this gate was built to end, so the gate itself
    must not be able to reproduce it.
    """
    _, seen, scanned = _scan()
    assert scanned > 50, (
        f"only {scanned} files scanned; the walk is not reaching the repo"
    )
    assert seen, "no MCP prefix found anywhere; the regex or the root is wrong"
    assert "mcp__plugin_hypermnesia-mcp_cortex__" in seen


@pytest.mark.parametrize(
    "dead_prefix",
    [
        "mcp__plugin_cortex_cortex__",  # mcp-prefix-allow-legacy
        "mcp__plugin_someone-else_other__",  # mcp-prefix-allow-legacy
    ],
)
def test_allowlist_rejects_dead_and_foreign_prefixes(dead_prefix: str) -> None:
    """Pins the gate's discriminating power.

    Without this, an allowlist that had accidentally grown to contain every
    prefix would still satisfy both tests above.
    """
    assert dead_prefix not in KNOWN_MCP_PREFIXES
