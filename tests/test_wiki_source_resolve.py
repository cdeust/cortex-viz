"""Unit tests for ``core.wiki_source_resolve.resolve_file_node_id`` — the
riskiest join in the wiki->file edge feature (source_path -> FILE node
id). Monkeypatches ``source_roots_for_domain`` so these tests never touch
``git``; the family-disambiguation tests use a real ``tmp_path`` tree so
the ``os.path.exists`` probe has something concrete to resolve against.
"""

from __future__ import annotations

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
    monkeypatch.setattr(
        mod, "source_roots_for_domain", lambda canonical: ["/repo/cortex/"]
    )
    got = mod.resolve_file_node_id("domain:cortex", "foo.py")
    expected = NodeIdFactory.file_id("/repo/cortex/foo.py")
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
    # Same relative path present in BOTH repos — the domain tag cannot
    # disambiguate, so skip rather than draw a possibly-wrong edge.
    (tmp_path / "cortex" / "README.md").write_text("main\n")
    (tmp_path / "cortex-viz" / "README.md").write_text("viz\n")
    monkeypatch.setattr(
        mod, "source_roots_for_domain", lambda canonical: [main, viz]
    )
    assert mod.resolve_file_node_id("domain:cortex", "README.md") is None
