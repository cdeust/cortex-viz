"""Tests for the read sandbox and the vulnerability it closes.

The regression test at the bottom is the one that matters: before the
sandbox existed, ``/api/trace/file?path=`` and ``/api/file-diff?name=``
returned the full contents of any file inside any git repository anywhere on
the machine, because ``diff_type: "untracked"`` renders a whole file as
``add`` lines. It is written so that it FAILS on the pre-fix code — the
paired positive case in the same test proves the refusal comes from the
sandbox and not from the repo simply being unreadable.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

from cortex_viz.infrastructure import file_sandbox
from cortex_viz.infrastructure.config import CLAUDE_DIR
from cortex_viz.infrastructure.file_sandbox import (
    OUTSIDE_SANDBOX,
    readable_roots,
    resolve_readable_path,
)
from cortex_viz.server.git_diff_engine import diff_for_path, repo_root_and_relpath


def _init_repo(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    for args in (
        ["init", "-q"],
        ["config", "user.email", "t@t.t"],
        ["config", "user.name", "t"],
        ["checkout", "-q", "-B", "main"],
    ):
        subprocess.run(
            ["git", "-C", str(root), *args], capture_output=True, text=True, check=True
        )


# ── readable_roots: the policy ─────────────────────────────────────────


def test_readable_roots_includes_the_claude_tree() -> None:
    assert str(CLAUDE_DIR) in readable_roots()


def test_readable_roots_includes_both_temp_roots() -> None:
    """$TMPDIR and the platform temp dir are different trees on macOS, and
    graphed scratchpad paths live under the second one. Listing only
    ``gettempdir()`` refused 524 of 1069 measured live paths."""
    roots = readable_roots()
    assert tempfile.gettempdir() in roots
    assert os.sep + "tmp" in roots


def test_readable_roots_follows_a_changed_dev_root_without_a_restart(
    monkeypatch, tmp_path: Path
) -> None:
    """Recomputed per call, not cached — a cached list would pin whichever
    layout existed at first import."""
    elsewhere = tmp_path / "some" / "dev"
    elsewhere.mkdir(parents=True)
    monkeypatch.setenv("CORTEX_DEV_ROOT", str(elsewhere))
    assert str(elsewhere) in readable_roots()


# ── resolve_readable_path ──────────────────────────────────────────────


def test_resolve_readable_path_accepts_a_path_under_a_root(tmp_path: Path) -> None:
    """tmp_path is under $TMPDIR, which is a readable root."""
    f = tmp_path / "f.txt"
    f.write_text("x", encoding="utf-8")
    resolved, reason = resolve_readable_path(str(f))
    assert reason is None
    assert resolved == os.path.realpath(str(f))


def test_resolve_readable_path_refuses_a_path_outside_every_root(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(file_sandbox, "readable_roots", lambda: [str(tmp_path / "in")])
    resolved, reason = resolve_readable_path(str(tmp_path / "out" / "f.txt"))
    assert resolved is None
    assert reason == OUTSIDE_SANDBOX


def test_refusal_reason_does_not_distinguish_missing_from_forbidden(
    monkeypatch, tmp_path: Path
) -> None:
    """Negative assertion: identical reasons, or the endpoint becomes a
    filesystem-existence oracle for paths the caller may not read."""
    allowed = tmp_path / "in"
    allowed.mkdir()
    monkeypatch.setattr(file_sandbox, "readable_roots", lambda: [str(allowed)])
    exists = tmp_path / "out" / "exists.txt"
    exists.parent.mkdir()
    exists.write_text("x", encoding="utf-8")
    missing = tmp_path / "out" / "missing.txt"
    assert (
        resolve_readable_path(str(exists))[1] == resolve_readable_path(str(missing))[1]
    )


# ── the vulnerability ──────────────────────────────────────────────────


def test_diff_engine_refuses_a_repo_outside_the_sandbox(
    monkeypatch, tmp_path: Path
) -> None:
    """Regression for the arbitrary-file-read this sandbox closes.

    Paired arms on the SAME repo and the SAME file: with the repo's parent
    listed as a readable root the file's contents come back in full; with it
    unlisted the request is refused. Without the pairing a refusal could just
    mean the repo was unreadable for some unrelated reason.
    """
    repo = tmp_path / "outside" / "repo"
    _init_repo(repo)
    secret = repo / "notes.txt"
    secret.write_text("TOKEN_A=hunter2\n", encoding="utf-8")

    monkeypatch.setattr(file_sandbox, "readable_roots", lambda: [str(tmp_path)])
    allowed = diff_for_path(str(secret))
    assert allowed["available"] is True
    assert "TOKEN_A=hunter2" in [ln["text"] for ln in allowed["lines"]]

    monkeypatch.setattr(
        file_sandbox, "readable_roots", lambda: [str(tmp_path / "elsewhere")]
    )
    refused = diff_for_path(str(secret))
    assert refused["available"] is False
    assert refused["reason"] == OUTSIDE_SANDBOX
    assert refused["lines"] == []


def test_repo_root_and_relpath_refuses_outside_the_sandbox(
    monkeypatch, tmp_path: Path
) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "f.txt").write_text("x", encoding="utf-8")

    monkeypatch.setattr(
        file_sandbox, "readable_roots", lambda: [str(tmp_path / "nope")]
    )
    root, rel, reason = repo_root_and_relpath(str(repo / "f.txt"))
    assert (root, rel) == (None, None)
    assert reason == OUTSIDE_SANDBOX


def test_sandbox_gate_runs_after_the_absoluteness_gate(monkeypatch) -> None:
    """Ordering is load-bearing: a relative path must be reported as such,
    never canonicalized against the server CWD and then judged."""
    monkeypatch.setattr(file_sandbox, "readable_roots", lambda: [os.getcwd()])
    result = diff_for_path("src/main.py")
    assert result["available"] is False
    assert result["reason"] == "path is not absolute"


def test_symlink_out_of_the_sandbox_is_refused(monkeypatch, tmp_path: Path) -> None:
    """A symlink INSIDE an allowed root pointing outside it must not carry
    the read out — containment compares the resolved path."""
    allowed = tmp_path / "allowed"
    _init_repo(allowed)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("TOP-SECRET\n", encoding="utf-8")
    (allowed / "innocent.txt").symlink_to(outside / "secret.txt")

    monkeypatch.setattr(file_sandbox, "readable_roots", lambda: [str(allowed)])
    result = diff_for_path(str(allowed / "innocent.txt"))
    assert result["available"] is False
    assert result["reason"] == OUTSIDE_SANDBOX
    assert "TOP-SECRET" not in str(result["lines"])
