"""Unit tests for the git file-diff parser (http_file_diff).

Covers the pure parsing logic that turns ``git diff`` unified output into the
{type,text} line list ui/unified/js/detail_diff.js renders — the part most
likely to regress. The git-subprocess resolution is exercised live against a
throwaway repo so the diff_type selection is covered without mocking git.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from cortex_viz.server.git_diff_engine import (
    _MAX_LINES,
    _full_content_as_adds,
    _parse_unified,
    _resolve_diff,
)
from cortex_viz.server.http_file_diff import _resolve_by_relative_fragment

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
    big = "@@ -1,1 +1,9999 @@\n" + "\n".join(f"+l{i}" for i in range(_MAX_LINES + 500))
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


# ── _resolve_by_basename / _resolve_by_relative_fragment failure arms ────
# The happy paths and the "store unavailable" arms are covered via
# ``_resolve_name`` in test_git_diff_engine.py; these pin the two arms only
# reachable when a store IS present: the activity-store lookup raising, and
# (for the relative-fragment resolver) the suffix-search fallback.


def test_resolve_by_basename_store_lookup_exception_reports_reason(monkeypatch):
    import cortex_viz.infrastructure.activity_store as activity_store
    from cortex_viz.server.http_file_diff import _resolve_by_basename

    def _boom(store, label):
        raise RuntimeError("db down")

    monkeypatch.setattr(activity_store, "find_abs_path_by_label", _boom)
    abs_path, reason = _resolve_by_basename(object(), "foo.py")
    assert abs_path is None
    assert reason == "unresolved basename: activity store lookup failed"


def test_resolve_by_relative_fragment_store_none_after_no_repo_match_reports_reason():
    # No registry repo matches and store is None -- distinct message from
    # the "no such basename" and "activity store lookup failed" arms.
    abs_path, reason = _resolve_by_relative_fragment(None, "no/such/repo/file.py")
    assert abs_path is None
    assert reason == "unresolved relative name: not found in known repos"


def test_resolve_by_relative_fragment_falls_back_to_activity_suffix_search(
    monkeypatch,
):
    import cortex_viz.infrastructure.activity_store as activity_store
    from cortex_viz.server.http_file_diff import _resolve_by_relative_fragment

    monkeypatch.setattr(
        activity_store,
        "find_abs_path_by_suffix",
        lambda store, name: "/repo/src/found.py" if name == "src/found.py" else None,
    )
    abs_path, reason = _resolve_by_relative_fragment(object(), "src/found.py")
    assert abs_path == "/repo/src/found.py"
    assert reason is None


def test_resolve_by_relative_fragment_suffix_search_not_found_reports_reason(
    monkeypatch,
):
    import cortex_viz.infrastructure.activity_store as activity_store
    from cortex_viz.server.http_file_diff import _resolve_by_relative_fragment

    monkeypatch.setattr(
        activity_store, "find_abs_path_by_suffix", lambda store, name: None
    )
    abs_path, reason = _resolve_by_relative_fragment(object(), "src/missing.py")
    assert abs_path is None
    assert reason == "unresolved relative name: not found in activity index or known repos"


def test_resolve_by_relative_fragment_suffix_search_exception_reports_reason(
    monkeypatch,
):
    import cortex_viz.infrastructure.activity_store as activity_store
    from cortex_viz.server.http_file_diff import _resolve_by_relative_fragment

    def _boom(store, name):
        raise RuntimeError("db down")

    monkeypatch.setattr(activity_store, "find_abs_path_by_suffix", _boom)
    abs_path, reason = _resolve_by_relative_fragment(object(), "src/anything.py")
    assert abs_path is None
    assert reason == "unresolved relative name: activity store lookup failed"


def test_resolve_by_relative_fragment_prefers_known_repo_over_suffix_search(
    monkeypatch, tmp_path
):
    # When a repo-root candidate exists on disk, the suffix-search fallback
    # (and its store) must never even be consulted.
    import cortex_viz.infrastructure.activity_store as activity_store

    repo_dir = tmp_path / "known_repo"
    (repo_dir / "src").mkdir(parents=True)
    (repo_dir / "src" / "file.py").write_text("x\n")

    class _FakeRepoInfo:
        def __init__(self, fs_path: str) -> None:
            self.fs_path = fs_path

    class _FakeRegistry:
        repos = [_FakeRepoInfo(str(repo_dir))]

    monkeypatch.setattr(
        "cortex_viz.shared.domain_mapping._build_registry", lambda: _FakeRegistry()
    )

    def _boom(store, name):  # pragma: no cover - must never run
        raise AssertionError("suffix search must not run when a repo root matches")

    monkeypatch.setattr(activity_store, "find_abs_path_by_suffix", _boom)
    abs_path, reason = _resolve_by_relative_fragment(object(), "src/file.py")
    assert abs_path == str(repo_dir / "src" / "file.py")
    assert reason is None


# ── serve_file_diff — the HTTP entry point itself ────────────────────────


class _FakeHandler:
    def __init__(self, path: str) -> None:
        self.path = path
        self.status: int | None = None
        self.headers: dict[str, str] = {}
        self.body = b""
        self.wfile = self

    def send_response(self, code: int) -> None:
        self.status = code

    def send_header(self, k: str, v: str) -> None:
        self.headers[k] = v

    def end_headers(self) -> None:
        pass

    def write(self, data: bytes) -> None:
        self.body += data


def _decode_json(handler: _FakeHandler) -> dict:
    import json

    return json.loads(handler.body.decode("utf-8"))


def test_serve_file_diff_missing_name_param_reports_no_file_given():
    import cortex_viz.server.http_file_diff as mod_local

    h = _FakeHandler("/api/file-diff")
    mod_local.serve_file_diff(h)
    assert h.status == 200
    payload = _decode_json(h)
    assert payload == {
        "available": False,
        "diff_type": "none",
        "lines": [],
        "truncated": False,
        "reason": "no file given",
    }


def test_serve_file_diff_unresolvable_name_reports_reason_and_unavailable():
    import cortex_viz.server.http_file_diff as mod_local

    h = _FakeHandler("/api/file-diff?name=nowhere.py")
    mod_local.serve_file_diff(h, store=None)
    assert h.status == 200
    payload = _decode_json(h)
    assert payload["available"] is False
    assert payload["diff_type"] == "none"
    assert payload["reason"] == "unresolved basename: activity store unavailable"


def test_serve_file_diff_resolves_absolute_path_and_delegates_to_the_engine(
    tmp_path: Path,
):
    import subprocess

    import cortex_viz.server.http_file_diff as mod_local

    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "-C", str(root), "init", "-q"], check=True)
    (root / "new.txt").write_text("alpha\n")

    h = _FakeHandler(f"/api/file-diff?name={root / 'new.txt'}")
    mod_local.serve_file_diff(h)
    assert h.status == 200
    payload = _decode_json(h)
    assert payload["available"] is True
    assert payload["diff_type"] == "untracked"


def test_serve_file_diff_unexpected_exception_yields_json_error():
    import cortex_viz.server.http_file_diff as mod_local

    class _BrokenHandler(_FakeHandler):
        @property
        def path(self):  # noqa: D401 - raising on access forces the except branch
            raise RuntimeError("boom")

        @path.setter
        def path(self, value):
            pass

    h = _BrokenHandler("/api/file-diff?name=x")
    mod_local.serve_file_diff(h)
    assert h.status == 500
    payload = _decode_json(h)
    assert payload == {"error": "RuntimeError"}
