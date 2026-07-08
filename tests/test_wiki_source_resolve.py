"""Unit tests for ``core.wiki_source_resolve.resolve_file_node_id`` — the
riskiest join in the wiki->file edge feature (source_path -> FILE node
id). Monkeypatches ``source_roots_for_domain`` so these tests never touch
``git``; the family-disambiguation tests use a real ``tmp_path`` tree so
the ``os.path.exists`` probe has something concrete to resolve against.
"""

from __future__ import annotations

import os

import cortex_viz.core.wiki_source_resolve as mod
from cortex_viz.core.workflow_graph_schema import NodeIdFactory


# ── Single-root fast path (historic behaviour, no disk read) ──────────


def test_resolves_to_the_same_id_the_graph_would_mint_for_the_abs_path(
    monkeypatch,
):
    monkeypatch.setattr(
        mod, "source_roots_for_domain", lambda canonical: ["/repo/cortex"]
    )
    got = mod.resolve_file_node_id("domain:cortex", "mcp_server/core/foo.py")
    expected = NodeIdFactory.file_id("/repo/cortex/mcp_server/core/foo.py")
    assert got == expected


def test_root_with_trailing_slash_does_not_double_slash(monkeypatch):
    # A source_path WITH '/' takes the direct-join branch; a trailing slash on
    # the root must not produce a double slash in the reconstructed abs path.
    monkeypatch.setattr(
        mod, "source_roots_for_domain", lambda canonical: ["/repo/cortex/"]
    )
    got = mod.resolve_file_node_id("domain:cortex", "sub/foo.py")
    expected = NodeIdFactory.file_id("/repo/cortex/sub/foo.py")
    assert got == expected


def test_unknown_domain_source_root_returns_none(monkeypatch):
    monkeypatch.setattr(mod, "source_roots_for_domain", lambda canonical: [])
    assert mod.resolve_file_node_id("domain:no-such-repo", "foo.py") is None


def test_non_domain_prefixed_id_returns_none(monkeypatch):
    monkeypatch.setattr(
        mod, "source_roots_for_domain", lambda canonical: ["/repo/cortex"]
    )
    assert mod.resolve_file_node_id("__global__", "foo.py") is None
    assert mod.resolve_file_node_id(None, "foo.py") is None


def test_blank_source_path_returns_none(monkeypatch):
    monkeypatch.setattr(
        mod, "source_roots_for_domain", lambda canonical: ["/repo/cortex"]
    )
    assert mod.resolve_file_node_id("domain:cortex", "") is None
    assert mod.resolve_file_node_id("domain:cortex", None) is None


# ── Family disambiguation (the collision fix) ─────────────────────────


def _make_family(tmp_path):
    """A 'cortex' family: cortex (main) + cortex-viz siblings on disk."""
    main = tmp_path / "cortex"
    viz = tmp_path / "cortex-viz"
    (main / "mcp_server").mkdir(parents=True)
    (viz / "cortex_viz" / "core").mkdir(parents=True)
    return str(main), str(viz)


def test_family_resolves_to_the_sibling_that_holds_the_file(
    monkeypatch, tmp_path
):
    main, viz = _make_family(tmp_path)
    # File exists only in the viz sibling, not the family's main repo.
    target = tmp_path / "cortex-viz" / "cortex_viz" / "core" / "graph.py"
    target.write_text("x = 1\n")
    monkeypatch.setattr(
        mod, "source_roots_for_domain", lambda canonical: [main, viz]
    )
    got = mod.resolve_file_node_id(
        "domain:cortex", "cortex_viz/core/graph.py"
    )
    expected = NodeIdFactory.file_id(f"{viz}/cortex_viz/core/graph.py")
    assert got == expected  # NOT the main-repo first-match


def test_family_with_no_on_disk_match_falls_back_to_primary(
    monkeypatch, tmp_path
):
    main, viz = _make_family(tmp_path)
    monkeypatch.setattr(
        mod, "source_roots_for_domain", lambda canonical: [main, viz]
    )
    # Neither sibling holds the path — keep the candidate-id contract
    # (caller confirms the FILE node exists), fall back to primary root.
    got = mod.resolve_file_node_id("domain:cortex", "nowhere/ghost.py")
    expected = NodeIdFactory.file_id(f"{main}/nowhere/ghost.py")
    assert got == expected


def test_family_ambiguous_same_relpath_in_two_siblings_skips(
    monkeypatch, tmp_path
):
    main, viz = _make_family(tmp_path)
    # Same relative PATH present in BOTH repos — the domain tag cannot
    # disambiguate, so skip rather than draw a possibly-wrong edge. (Uses a
    # '/'-bearing path to exercise the repo-exact _select_root branch, not the
    # basename branch.)
    for base in (main, viz):
        shared = tmp_path / os.path.basename(base) / "shared"
        shared.mkdir(parents=True)
        (shared / "common.py").write_text("x = 1\n")
    monkeypatch.setattr(
        mod, "source_roots_for_domain", lambda canonical: [main, viz]
    )
    assert mod.resolve_file_node_id("domain:cortex", "shared/common.py") is None


# ── Bare-basename recovery (VOLET ②) ──────────────────────────────────


def test_basename_resolves_to_unique_file(monkeypatch, tmp_path):
    """A source_path with NO '/' is a bare basename (upstream stored a
    filename, not a relpath). It resolves iff exactly one source file with
    that name exists in the family tree."""
    mod._basename_index.cache_clear()
    repo = tmp_path / "cortex"
    (repo / "pkg" / "core").mkdir(parents=True)
    (repo / "pkg" / "core" / "predictive_coding.py").write_text("x = 1\n")
    monkeypatch.setattr(
        mod, "source_roots_for_domain", lambda canonical: [str(repo)]
    )
    got = mod.resolve_file_node_id("domain:cortex", "predictive_coding.py")
    expected = NodeIdFactory.file_id(f"{repo}/pkg/core/predictive_coding.py")
    assert got == expected


def test_basename_ambiguous_two_files_skips(monkeypatch, tmp_path):
    """Two files with the same basename in the tree → cannot disambiguate →
    skip (precision over recall), never a possibly-wrong edge."""
    mod._basename_index.cache_clear()
    repo = tmp_path / "cortex"
    (repo / "a").mkdir(parents=True)
    (repo / "b").mkdir(parents=True)
    (repo / "a" / "utils.py").write_text("x = 1\n")
    (repo / "b" / "utils.py").write_text("y = 2\n")
    monkeypatch.setattr(
        mod, "source_roots_for_domain", lambda canonical: [str(repo)]
    )
    assert mod.resolve_file_node_id("domain:cortex", "utils.py") is None


def test_basename_ambiguous_across_family_siblings_skips(monkeypatch, tmp_path):
    """Same basename present in two family repos → ambiguous → skip."""
    mod._basename_index.cache_clear()
    main = tmp_path / "cortex"
    viz = tmp_path / "cortex-viz"
    (main / "pkg").mkdir(parents=True)
    (viz / "pkg").mkdir(parents=True)
    (main / "pkg" / "schema.py").write_text("x = 1\n")
    (viz / "pkg" / "schema.py").write_text("y = 2\n")
    monkeypatch.setattr(
        mod, "source_roots_for_domain", lambda canonical: [str(main), str(viz)]
    )
    assert mod.resolve_file_node_id("domain:cortex", "schema.py") is None


def test_basename_absent_returns_none(monkeypatch, tmp_path):
    mod._basename_index.cache_clear()
    repo = tmp_path / "cortex"
    (repo / "pkg").mkdir(parents=True)
    (repo / "pkg" / "present.py").write_text("x = 1\n")
    monkeypatch.setattr(
        mod, "source_roots_for_domain", lambda canonical: [str(repo)]
    )
    assert mod.resolve_file_node_id("domain:cortex", "missing.py") is None


def test_basename_unique_across_family_resolves_to_holder(monkeypatch, tmp_path):
    """The basename lives in exactly ONE sibling of the family → that one."""
    mod._basename_index.cache_clear()
    main = tmp_path / "cortex"
    viz = tmp_path / "cortex-viz"
    (main / "pkg").mkdir(parents=True)
    (viz / "cortex_viz" / "server").mkdir(parents=True)
    (viz / "cortex_viz" / "server" / "graph_build_l6.py").write_text("x = 1\n")
    monkeypatch.setattr(
        mod, "source_roots_for_domain", lambda canonical: [str(main), str(viz)]
    )
    got = mod.resolve_file_node_id("domain:cortex", "graph_build_l6.py")
    expected = NodeIdFactory.file_id(
        f"{viz}/cortex_viz/server/graph_build_l6.py"
    )
    assert got == expected
