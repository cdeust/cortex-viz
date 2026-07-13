"""Tests for the shared git-diff engine (contract A.1/A.2/A.4) and the
``/api/file-diff`` name-resolution ladder (contract A.3) and the
``/api/trace/file`` AST opt-in (contract A.5).

Repos are built fresh per test under ``tmp_path`` — no fixture reuse across
tests, so each git history is exactly what the test asserts against.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from cortex_viz.server.git_diff_engine import (
    _last_commit_diff,
    _parse_unified,
    _resolve_diff,
    diff_for_path,
    repo_root_and_relpath,
    resolve_repo_root,
)


def _git(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        check=check,
    )


def _init_repo(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@t.t")
    _git(root, "config", "user.name", "t")
    _git(root, "checkout", "-q", "-B", "main")


# ── (a) clean file + HEAD is a merge commit → last_commit non-empty ────


def test_last_commit_diff_survives_merge_head(tmp_path: Path):
    """Reproduces the dominant bug (contract root cause #1) exactly:

    ``f.txt`` is added on ``main``, then ``main`` merges an unrelated
    ``feature`` branch (which only touches ``g.txt``) via a real merge
    commit. HEAD (the merge commit) is TREESAME to its first parent for
    ``f.txt`` — so ``git show HEAD -- f.txt`` (the old, buggy call) renders
    0 bytes even though the file has real history. The engine must instead
    walk back via ``git log --follow`` to the commit that actually added
    the file and diff THAT commit.
    """
    root = tmp_path / "repo"
    _init_repo(root)
    f = root / "f.txt"
    f.write_text("one\n")
    _git(root, "add", "f.txt")
    _git(root, "commit", "-q", "-m", "c1: add f")

    _git(root, "checkout", "-q", "-b", "feature")
    (root / "g.txt").write_text("g\n")
    _git(root, "add", "g.txt")
    _git(root, "commit", "-q", "-m", "c2: add g on feature")

    _git(root, "checkout", "-q", "main")
    _git(root, "merge", "-q", "--no-ff", "feature", "-m", "merge: bring in feature")

    # Confirms the premise: the naive "diff HEAD against f.txt" the old code
    # used renders nothing, because HEAD is TREESAME to main for f.txt.
    head_diff = _git(root, "show", "HEAD", "--format=", "--unified=3", "--", "f.txt")
    assert head_diff.stdout.strip() == ""

    result = _last_commit_diff(str(root), "f.txt")
    assert result is not None
    assert result["diff_type"] == "last_commit"
    assert result["lines"]
    assert result["commit"]["subject"] == "c1: add f"
    assert any(ln["type"] == "add" and ln["text"] == "one" for ln in result["lines"])

    # Same assertion through the full ladder (uncommitted is empty since
    # the working tree is clean, so it falls through to last_commit).
    full = _resolve_diff(str(root), "f.txt")
    assert full["diff_type"] == "last_commit"
    assert full["lines"]


# ── (b) committed deletion → deletion patch ─────────────────────────────


def test_committed_deletion_yields_deletion_patch(tmp_path: Path):
    root = tmp_path / "repo"
    _init_repo(root)
    f = root / "gone.txt"
    f.write_text("bye\n")
    _git(root, "add", "gone.txt")
    _git(root, "commit", "-q", "-m", "add gone.txt")
    _git(root, "rm", "-q", "gone.txt")
    _git(root, "commit", "-q", "-m", "remove gone.txt")

    result = _resolve_diff(str(root), "gone.txt")
    assert result["diff_type"] == "last_commit"
    types = {ln["type"] for ln in result["lines"]}
    assert "del" in types
    assert "add" not in types


# ── (c) untracked → full content as adds ────────────────────────────────


def test_untracked_file_renders_as_adds(tmp_path: Path):
    root = tmp_path / "repo"
    _init_repo(root)
    (root / "new.txt").write_text("alpha\nbeta\n")

    result = _resolve_diff(str(root), "new.txt")
    assert result["diff_type"] == "untracked"
    assert [ln["text"] for ln in result["lines"] if ln["type"] == "add"] == [
        "alpha",
        "beta",
    ]


# ── (d) ~/... expands before resolution ─────────────────────────────────


def test_tilde_path_resolves_via_expanduser(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    root = tmp_path / "proj"
    _init_repo(root)
    f = root / "f.txt"
    f.write_text("one\n")
    _git(root, "add", "f.txt")
    _git(root, "commit", "-q", "-m", "c1")

    resolved_root, reason = resolve_repo_root("~/proj/f.txt")
    assert reason is None
    assert resolved_root is not None
    import os

    assert os.path.realpath(resolved_root) == os.path.realpath(str(root))


# ── (e) parent directory gone, repo alive → still resolves + last_commit ─


def test_gone_parent_directory_still_resolves_repo_and_last_commit(tmp_path: Path):
    root = tmp_path / "repo"
    _init_repo(root)
    nested = root / "sub" / "gonedir"
    nested.mkdir(parents=True)
    f = nested / "orphan.txt"
    f.write_text("hi\n")
    _git(root, "add", "sub/gonedir/orphan.txt")
    _git(root, "commit", "-q", "-m", "add orphan")
    _git(root, "rm", "-q", "sub/gonedir/orphan.txt")
    _git(root, "commit", "-q", "-m", "remove orphan")
    # ``git rm`` already prunes the now-empty gonedir/sub tree on its own —
    # the directory that held orphan.txt no longer exists on disk at all,
    # which is the scenario that used to crash: subprocess.run(cwd=<gone>).
    import shutil

    if (root / "sub").exists():
        shutil.rmtree(root / "sub")
    assert not (root / "sub").exists()

    result = diff_for_path(str(f))
    assert result["available"] is True
    assert result["diff_type"] == "last_commit"
    assert any(ln["type"] == "del" for ln in result["lines"])


# ── (f) no commit-message leakage into parsed lines ─────────────────────


def test_parse_unified_never_leaks_pre_hunk_text():
    # Simulates a pre-hunk body (a commit message, or any header noise) the
    # old parser mistook for a context line before the first ``@@``.
    raw = (
        "    Some commit subject line\n"
        "\n"
        "diff --git a/f b/f\n"
        "index 111..222 100644\n"
        "--- a/f\n"
        "+++ b/f\n"
        "@@ -1,1 +1,1 @@\n"
        "-old\n"
        "+new\n"
    )
    lines, truncated = _parse_unified(raw)
    assert not truncated
    texts = [ln["text"] for ln in lines]
    assert "    Some commit subject line" not in texts
    assert lines[0]["type"] == "hunk"
    assert {"type": "del", "text": "old"} in lines
    assert {"type": "add", "text": "new"} in lines


# ── robustness: repo with no commits at all (HEAD absent) ───────────────


def test_no_commits_head_absent_falls_through_cleanly(tmp_path: Path):
    root = tmp_path / "repo"
    _init_repo(root)
    (root / "new.txt").write_text("x\n")

    # HEAD doesn't exist yet — every "git diff HEAD" / "git log" call fails;
    # the ladder must fall through to untracked rather than raising.
    result = _resolve_diff(str(root), "new.txt")
    assert result["diff_type"] == "untracked"


def test_no_commits_head_absent_staged_file_yields_content(tmp_path: Path):
    """A staged file in a 0-commit repo must NOT fall through to ``none``.

    ``git diff HEAD`` fails outright (no HEAD to diff against), and
    ``git ls-files --error-unmatch`` succeeds for the staged file (it is in
    the index), so the old ladder skipped the ``untracked`` step too and
    silently landed on ``none`` — the staged content vanished entirely.
    """
    root = tmp_path / "repo"
    _init_repo(root)
    f = root / "staged.txt"
    f.write_text("alpha\nbeta\n")
    _git(root, "add", "staged.txt")

    result = _resolve_diff(str(root), "staged.txt")
    assert result["diff_type"] != "none"
    assert [ln["text"] for ln in result["lines"] if ln["type"] == "add"] == [
        "alpha",
        "beta",
    ]


def test_no_repository_found_reports_honest_reason(tmp_path: Path):
    outside = tmp_path / "not_a_repo" / "f.txt"
    outside.parent.mkdir(parents=True)
    outside.write_text("x\n")

    root, reason = resolve_repo_root(str(outside))
    assert root is None
    assert reason == "no git repository found for this path"


# ── symlinked ancestor: rel must be computed against the canonicalized
#    path, not the raw one, or it silently walks back out to the original
#    absolute path and every downstream git call misses the tracked file
#    (macOS default layout: /tmp -> /private/tmp; this worktree itself is
#    reachable both ways) ────────────────────────────────────────────────


def test_diff_for_path_matches_through_symlinked_ancestor(tmp_path: Path):
    real_root = tmp_path / "real_repo"
    _init_repo(real_root)
    tracked = real_root / "tracked.py"
    tracked.write_text("x = 1\n")
    _git(real_root, "add", "tracked.py")
    _git(real_root, "commit", "-q", "-m", "add tracked.py")
    tracked.write_text("x = 2\n")  # uncommitted change

    symlinked_root = tmp_path / "link_repo"
    symlinked_root.symlink_to(real_root, target_is_directory=True)

    via_real = diff_for_path(str(real_root / "tracked.py"))
    via_symlink = diff_for_path(str(symlinked_root / "tracked.py"))

    assert via_real["diff_type"] == "uncommitted"
    assert via_symlink["diff_type"] == "uncommitted"
    assert via_symlink["lines"] == via_real["lines"]


def test_repo_root_and_relpath_matches_through_symlinked_ancestor(tmp_path: Path):
    # This is the single helper http_standalone_trace._file_git_root_rel
    # now delegates to — asserting it directly guards against the
    # duplication-reintroduces-the-bug failure mode the two callers
    # previously fell into independently.
    real_root = tmp_path / "real_repo2"
    _init_repo(real_root)
    (real_root / "f.py").write_text("x = 1\n")
    _git(real_root, "add", "f.py")
    _git(real_root, "commit", "-q", "-m", "add f.py")

    symlinked_root = tmp_path / "link_repo2"
    symlinked_root.symlink_to(real_root, target_is_directory=True)

    root, rel, reason = repo_root_and_relpath(str(symlinked_root / "f.py"))
    assert reason is None
    assert rel == "f.py"
    assert root == str(real_root.resolve())


# ── relative path / hash must never be silently absolutized against the
#    server's CWD — the absoluteness gate (contract A.1) has to run BEFORE
#    realpath, not after, or realpath's own CWD-relative resolution makes
#    every relative input pass the ``startswith('/')`` check it exists to
#    enforce ──────────────────────────────────────────────────────────────


def test_relative_path_is_rejected_as_not_absolute(tmp_path: Path, monkeypatch):
    # cwd is a real, git-managed directory so a bug that absolutizes against
    # CWD would otherwise resolve a real repo and mask the failure.
    root = tmp_path / "repo"
    _init_repo(root)
    (root / "main.py").write_text("x\n")
    _git(root, "add", "main.py")
    _git(root, "commit", "-q", "-m", "c1")
    monkeypatch.chdir(root)

    root_result, reason = resolve_repo_root("src/main.py")
    assert root_result is None
    assert reason == "path is not absolute"


def test_bare_hash_like_string_is_rejected_as_not_absolute(tmp_path: Path, monkeypatch):
    root = tmp_path / "repo"
    _init_repo(root)
    monkeypatch.chdir(root)

    root_result, reason = resolve_repo_root("abc123")
    assert root_result is None
    assert reason == "path is not absolute"


def test_tilde_expanded_absolute_path_still_resolves(tmp_path: Path, monkeypatch):
    # Guards against an over-broad fix: '~' must still expand to an
    # absolute path and pass the gate, not get caught by the relative-path
    # rejection.
    monkeypatch.setenv("HOME", str(tmp_path))
    root = tmp_path / "homerepo"
    _init_repo(root)
    f = root / "f.txt"
    f.write_text("one\n")
    _git(root, "add", "f.txt")
    _git(root, "commit", "-q", "-m", "c1")

    root_result, reason = resolve_repo_root("~/homerepo/f.txt")
    assert reason is None
    assert root_result is not None
    import os

    assert os.path.realpath(root_result) == os.path.realpath(str(root))


# ── (g)/(h) — /api/file-diff name resolution (contract A.3) ─────────────


def test_resolve_name_absolute_path_passthrough():
    from cortex_viz.server.http_file_diff import _resolve_name

    abs_path, reason = _resolve_name(None, "/abs/path/to/foo.py")
    assert abs_path == "/abs/path/to/foo.py"
    assert reason is None


def test_resolve_name_basename_resolves_via_activity_store(monkeypatch):
    import cortex_viz.infrastructure.activity_store as activity_store
    from cortex_viz.server.http_file_diff import _resolve_name

    monkeypatch.setattr(
        activity_store,
        "find_abs_path_by_label",
        lambda store, label: "/Users/dev/repo/foo.py" if label == "foo.py" else None,
    )
    abs_path, reason = _resolve_name(object(), "foo.py")
    assert abs_path == "/Users/dev/repo/foo.py"
    assert reason is None


def test_resolve_name_basename_unresolved_reports_reason(monkeypatch):
    import cortex_viz.infrastructure.activity_store as activity_store
    from cortex_viz.server.http_file_diff import _resolve_name

    monkeypatch.setattr(
        activity_store,
        "find_abs_path_by_label",
        lambda store, label: None,
    )
    abs_path, reason = _resolve_name(object(), "missing.py")
    assert abs_path is None
    assert reason is not None
    assert "unresolved basename" in reason


def test_resolve_name_never_falls_back_to_server_cwd(monkeypatch):
    # Store unavailable AND relative name has no known repo match: must
    # report a reason, never silently resolve against os.getcwd().
    from cortex_viz.server.http_file_diff import _resolve_name

    abs_path, reason = _resolve_name(None, "src/some/file.py")
    assert abs_path is None
    assert reason is not None
    assert "unresolved relative name" in reason


def test_resolve_name_relative_matches_known_repo_root(tmp_path: Path, monkeypatch):
    from cortex_viz.server.http_file_diff import _resolve_name

    repo_dir = tmp_path / "known_repo"
    (repo_dir / "src").mkdir(parents=True)
    (repo_dir / "src" / "file.py").write_text("x\n")

    class _FakeRepoInfo:
        def __init__(self, fs_path: str) -> None:
            self.fs_path = fs_path

    class _FakeRegistry:
        repos = [_FakeRepoInfo(str(repo_dir))]

    monkeypatch.setattr(
        "cortex_viz.shared.domain_mapping._build_registry",
        lambda: _FakeRegistry(),
    )
    abs_path, reason = _resolve_name(None, "src/file.py")
    assert abs_path == str(repo_dir / "src" / "file.py")
    assert reason is None


# ── (i) /api/trace/file without ?include=ast never computes AST ─────────


class _FakeHandler:
    def __init__(self, path: str) -> None:
        self.path = path
        self.sent: dict | None = None


def test_serve_trace_file_without_include_skips_ast(monkeypatch, tmp_path: Path):
    import cortex_viz.server.http_standalone_trace as trace_mod

    def _boom(_path):  # pragma: no cover - must never be called
        raise AssertionError("_ast_and_impact must not run without ?include=ast")

    monkeypatch.setattr(trace_mod, "_ast_and_impact", _boom)
    captured = {}
    monkeypatch.setattr(
        trace_mod,
        "send_json_ok",
        lambda handler, payload: captured.update(payload),
    )

    handler = _FakeHandler(f"/api/trace/file?path={tmp_path / 'nope.py'}")
    trace_mod.serve_trace_file(handler)

    assert "ast" not in captured
    assert "git" in captured
    assert "versions" in captured


def test_serve_trace_file_with_include_ast_computes_it(monkeypatch, tmp_path: Path):
    import cortex_viz.server.http_standalone_trace as trace_mod

    monkeypatch.setattr(trace_mod, "_ast_and_impact", lambda _path: {"ok": True})
    captured = {}
    monkeypatch.setattr(
        trace_mod,
        "send_json_ok",
        lambda handler, payload: captured.update(payload),
    )

    handler = _FakeHandler(f"/api/trace/file?path={tmp_path / 'nope.py'}&include=ast")
    trace_mod.serve_trace_file(handler)

    assert captured.get("ast") == {"ok": True}
