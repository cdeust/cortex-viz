"""Unit tests for ``core.wiki_source_resolve.resolve_file_node_id`` — the
riskiest join in the wiki->file edge feature (source_path -> FILE node
id). Monkeypatches ``_project_source_root`` so these tests never touch
the real filesystem or ``git``.
"""

from __future__ import annotations

import cortex_viz.core.wiki_source_resolve as mod
from cortex_viz.core.workflow_graph_schema import NodeIdFactory


def test_resolves_to_the_same_id_the_graph_would_mint_for_the_abs_path(
    monkeypatch,
):
    monkeypatch.setattr(
        mod, "_project_source_root", lambda canonical: "/repo/cortex"
    )
    got = mod.resolve_file_node_id("domain:cortex", "mcp_server/core/foo.py")
    expected = NodeIdFactory.file_id("/repo/cortex/mcp_server/core/foo.py")
    assert got == expected


def test_root_with_trailing_slash_does_not_double_slash(monkeypatch):
    monkeypatch.setattr(
        mod, "_project_source_root", lambda canonical: "/repo/cortex/"
    )
    got = mod.resolve_file_node_id("domain:cortex", "foo.py")
    expected = NodeIdFactory.file_id("/repo/cortex/foo.py")
    assert got == expected


def test_unknown_domain_source_root_returns_none(monkeypatch):
    monkeypatch.setattr(mod, "_project_source_root", lambda canonical: None)
    assert mod.resolve_file_node_id("domain:no-such-repo", "foo.py") is None


def test_non_domain_prefixed_id_returns_none(monkeypatch):
    monkeypatch.setattr(
        mod, "_project_source_root", lambda canonical: "/repo/cortex"
    )
    assert mod.resolve_file_node_id("__global__", "foo.py") is None
    assert mod.resolve_file_node_id(None, "foo.py") is None


def test_blank_source_path_returns_none(monkeypatch):
    monkeypatch.setattr(
        mod, "_project_source_root", lambda canonical: "/repo/cortex"
    )
    assert mod.resolve_file_node_id("domain:cortex", "") is None
    assert mod.resolve_file_node_id("domain:cortex", None) is None
