"""Unit tests for the git file-diff parser (http_file_diff).

Covers the pure parsing logic that turns ``git diff`` unified output into the
{type,text} line list ui/unified/js/detail_diff.js renders — the part most
likely to regress. The git-subprocess resolution is exercised live against a
throwaway repo so the diff_type selection is covered without mocking git.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from cortex_viz.server.http_file_diff import (
    _MAX_LINES,
    _full_content_as_adds,
    _parse_unified,
    _resolve_by_relative_fragment,
    _resolve_diff,
)

_SAMPLE = """diff --git a/f.txt b/f.txt
index 1234567..89abcde 100644
--- a/f.txt
+++ b/f.txt
@@ -1,3 +1,3 @@
 keep
-old line
+new line
 tail
"""


def test_parse_unified_classifies_lines():
    lines, truncated = _parse_unified(_SAMPLE)
    assert not truncated
    types = [ln["type"] for ln in lines]
    # header (diff/index/---/+++) dropped; hunk + context/del/add kept.
    assert types == ["hunk", "context", "del", "add", "context"]
    by = {ln["type"]: ln["text"] for ln in lines}
    assert by["hunk"].startswith("@@ -1,3 +1,3 @@")
    assert by["del"] == "old line"  # leading '-' stripped
    assert by["add"] == "new line"  # leading '+' stripped
    assert by["context"] == "tail"  # leading ' ' stripped


def test_parse_unified_truncates():
    big = "@@ -1,1 +1,9999 @@\n" + "\n".join(
        "+l%d" % i for i in range(_MAX_LINES + 500)
    )
    lines, truncated = _parse_unified(big)
    assert truncated
    assert len(lines) <= _MAX_LINES


def test_full_content_as_adds(tmp_path: Path):
    f = tmp_path / "new.txt"
    f.write_text("alpha\nbeta\ngamma\n")
    lines, truncated = _full_content_as_adds(str(tmp_path), "new.txt")
    assert not truncated
    assert lines[0]["type"] == "hunk" and "+1,3" in lines[0]["text"]
    assert [ln["text"] for ln in lines[1:]] == ["alpha", "beta", "gamma"]
    assert all(ln["type"] == "add" for ln in lines[1:])


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def test_resolve_diff_type_selection(tmp_path: Path):
    _git(tmp_path, "init")
    _git(tmp_path, "config", "user.email", "t@t.t")
    _git(tmp_path, "config", "user.name", "t")
    f = tmp_path / "a.txt"

    # Untracked new file → full content as additions.
    f.write_text("one\ntwo\n")
    r = _resolve_diff(str(tmp_path), "a.txt")
    assert r["diff_type"] == "untracked" and any(
        line["type"] == "add" for line in r["lines"]
    )

    # Committed, then modified in the working tree → uncommitted.
    _git(tmp_path, "add", "a.txt")
    _git(tmp_path, "commit", "-m", "init")
    f.write_text("one\ntwo\nthree\n")
    r = _resolve_diff(str(tmp_path), "a.txt")
    assert r["diff_type"] == "uncommitted"
    assert any(line["type"] == "add" and line["text"] == "three" for line in r["lines"])

    # Clean working tree → falls back to the last commit.
    _git(tmp_path, "add", "a.txt")
    _git(tmp_path, "commit", "-m", "add three")
    r = _resolve_diff(str(tmp_path), "a.txt")
    assert r["diff_type"] == "last_commit" and r["lines"]


def test_resolve_by_relative_fragment_rejects_path_traversal():
    # A '..' segment must never be joined onto a repo root — otherwise a
    # crafted ``name`` query param could escape the repo (CWE-22).
    abs_path, reason = _resolve_by_relative_fragment(None, "../../etc/passwd")
    assert abs_path is None
    assert reason == "unresolved relative name: path traversal rejected"
