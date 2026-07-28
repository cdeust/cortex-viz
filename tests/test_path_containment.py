"""Contract tests for the filesystem containment primitive.

``shared.path_containment`` is the single guard every request-supplied path
crosses before it reaches a filesystem call, so its failure modes are tested
like happy paths: each escape shape asserts the OBSERVABLE refusal (``None``
returned), and each legitimate shape asserts the exact resolved value the
caller is then required to use.

The sibling-prefix and symlink cases are the two that a naive containment
check gets wrong, and both are the reason this module exists rather than a
one-line ``startswith`` at each call site.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from cortex_viz.shared.path_containment import (
    real_path,
    resolve_under,
    resolve_under_any,
)


# ── real_path ──────────────────────────────────────────────────────────


def test_real_path_expands_tilde(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    assert real_path("~/x.txt") == os.path.realpath(str(tmp_path / "x.txt"))


def test_real_path_resolves_a_symlinked_ancestor(tmp_path: Path) -> None:
    target = tmp_path / "real"
    target.mkdir()
    link = tmp_path / "link"
    link.symlink_to(target)
    assert real_path(str(link / "f.txt")) == os.path.realpath(str(target / "f.txt"))


def test_real_path_of_empty_input_is_the_cwd() -> None:
    """Pinned because callers MUST reject empty/relative input before calling.

    ``realpath('')`` absolutizes against the process CWD. This test exists so
    that behaviour is a stated contract rather than a surprise: it is the
    exact reason ``git_diff_engine._sandboxed_abs_path`` checks absoluteness
    on the un-canonicalized string first.
    """
    assert real_path("") == os.path.realpath(os.getcwd())


# ── resolve_under: containment ─────────────────────────────────────────


def test_resolve_under_accepts_a_path_inside_the_root(tmp_path: Path) -> None:
    root = tmp_path / "sandbox"
    (root / "sub").mkdir(parents=True)
    inside = root / "sub" / "f.txt"
    inside.write_text("x", encoding="utf-8")
    assert resolve_under(str(root), str(inside)) == os.path.realpath(str(inside))


def test_resolve_under_accepts_a_path_that_does_not_exist_yet(tmp_path: Path) -> None:
    """``save_page`` writes new files — containment must not require existence."""
    root = tmp_path / "sandbox"
    root.mkdir()
    new = root / "not" / "created" / "yet.md"
    assert resolve_under(str(root), str(new)) == os.path.realpath(str(new))


@pytest.mark.parametrize(
    "escape",
    [
        "../secret.txt",
        "sub/../../secret.txt",
        "../../../../etc/passwd",
        "..",
    ],
)
def test_resolve_under_refuses_dot_dot_escapes(tmp_path: Path, escape: str) -> None:
    root = tmp_path / "sandbox"
    (root / "sub").mkdir(parents=True)
    (tmp_path / "secret.txt").write_text("TOP-SECRET", encoding="utf-8")
    assert resolve_under(str(root), os.path.join(str(root), escape)) is None


def test_resolve_under_refuses_an_absolute_path_outside(tmp_path: Path) -> None:
    root = tmp_path / "sandbox"
    root.mkdir()
    assert resolve_under(str(root), "/etc/passwd") is None


def test_resolve_under_refuses_a_symlink_pointing_out_of_the_root(
    tmp_path: Path,
) -> None:
    """The case a textual check cannot catch: the name is innocent, the
    dereference is not. Containment must compare the RESOLVED path."""
    root = tmp_path / "sandbox"
    root.mkdir()
    secret = tmp_path / "secret.txt"
    secret.write_text("TOP-SECRET", encoding="utf-8")
    (root / "innocent.txt").symlink_to(secret)
    assert resolve_under(str(root), str(root / "innocent.txt")) is None


def test_resolve_under_refuses_a_sibling_sharing_the_root_prefix(
    tmp_path: Path,
) -> None:
    """``/x/wiki-backup`` is not inside ``/x/wiki``.

    A prefix comparison without the separator accepts it; this is the
    boundary bug the separator-terminated prefix exists to prevent.
    """
    root = tmp_path / "wiki"
    root.mkdir()
    sibling = tmp_path / "wiki-backup"
    sibling.mkdir()
    leak = sibling / "f.md"
    leak.write_text("x", encoding="utf-8")
    assert resolve_under(str(root), str(leak)) is None


def test_resolve_under_refuses_the_root_itself(tmp_path: Path) -> None:
    root = tmp_path / "sandbox"
    root.mkdir()
    assert resolve_under(str(root), str(root)) is None


def test_resolve_under_refuses_a_root_of_slash(tmp_path: Path) -> None:
    """A sandbox rooted at ``/`` contains everything and is not a sandbox."""
    assert resolve_under("/", str(tmp_path / "anything.txt")) is None


def test_resolve_under_refuses_a_root_that_resolves_to_slash(tmp_path: Path) -> None:
    """The refusal is on the RESOLVED root, so a symlink to ``/`` is caught
    too — otherwise the whole filesystem is reachable through one link."""
    link = tmp_path / "everything"
    link.symlink_to("/")
    assert resolve_under(str(link), str(tmp_path / "anything.txt")) is None


def test_resolve_under_accepts_a_root_one_level_below_slash(tmp_path: Path) -> None:
    """Paired control for the two refusals above: the root check must reject
    filesystem roots ONLY, not every short path."""
    assert resolve_under(
        os.sep + "tmp", os.sep + "tmp" + os.sep + "f.txt"
    ) == real_path(os.sep + "tmp" + os.sep + "f.txt")


def test_resolve_under_tolerates_a_trailing_separator_on_the_root(
    tmp_path: Path,
) -> None:
    root = tmp_path / "sandbox"
    root.mkdir()
    inside = root / "f.txt"
    inside.write_text("x", encoding="utf-8")
    assert resolve_under(str(root) + os.sep, str(inside)) == os.path.realpath(
        str(inside)
    )


def test_resolve_under_resolves_a_symlinked_root(tmp_path: Path) -> None:
    """macOS ``/tmp`` -> ``/private/tmp``: root and candidate must be
    canonicalized the same way or a legitimate read is refused."""
    real_root = tmp_path / "real_root"
    (real_root / "sub").mkdir(parents=True)
    inside = real_root / "sub" / "f.txt"
    inside.write_text("x", encoding="utf-8")
    linked_root = tmp_path / "linked_root"
    linked_root.symlink_to(real_root)
    assert resolve_under(str(linked_root), str(inside)) == os.path.realpath(str(inside))


# ── resolve_under_any ──────────────────────────────────────────────────


def test_resolve_under_any_returns_the_first_containing_root(tmp_path: Path) -> None:
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()
    inside = b / "f.txt"
    inside.write_text("x", encoding="utf-8")
    assert resolve_under_any([str(a), str(b)], str(inside)) == os.path.realpath(
        str(inside)
    )


def test_resolve_under_any_refuses_when_no_root_contains_it(tmp_path: Path) -> None:
    a = tmp_path / "a"
    a.mkdir()
    outside = tmp_path / "elsewhere" / "f.txt"
    assert resolve_under_any([str(a)], str(outside)) is None


def test_resolve_under_any_with_no_roots_refuses_everything(tmp_path: Path) -> None:
    """Negative assertion: an empty root list must be closed, not open."""
    assert resolve_under_any([], str(tmp_path / "f.txt")) is None
