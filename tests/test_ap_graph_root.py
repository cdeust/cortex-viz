"""Unit tests for ``infrastructure.ap_graph_root`` — resolving the absolute
source root an automatised-pipeline graph was indexed from, so L6 can key AST
FILE nodes by the same absolute scheme the rest of the graph uses (VOLET ①,
mem 4262203).
"""

from __future__ import annotations

import json

import cortex_viz.infrastructure.ap_graph_root as mod
from cortex_viz.infrastructure.ap_graph_root import absolutize, graph_source_root


# ── absolutize ──────────────────────────────────────────────────────


def test_absolutize_none_root_returns_relative_unchanged():
    assert absolutize(None, "cortex_viz/core/x.py") == "cortex_viz/core/x.py"


def test_absolutize_joins_posix():
    assert absolutize("/repo", "a/b.py") == "/repo/a/b.py"


def test_absolutize_strips_trailing_slash():
    assert absolutize("/repo/", "a/b.py") == "/repo/a/b.py"


def test_absolutize_matches_resolve_file_node_id(monkeypatch):
    """The join here MUST be byte-identical to what
    wiki_source_resolve.resolve_file_node_id computes, or the file ids never
    match. Cross-check against that function on the same (root, rel)."""
    import cortex_viz.core.wiki_source_resolve as resolve_mod
    from cortex_viz.core.workflow_graph_schema import NodeIdFactory

    root = "/Users/x/repo"
    rel = "pkg/mod/file.py"
    monkeypatch.setattr(
        resolve_mod, "source_roots_for_domain", lambda canonical: [root]
    )
    via_resolver = resolve_mod.resolve_file_node_id("domain:cortex", rel)
    via_absolutize = NodeIdFactory.file_id(absolutize(root, rel))
    assert via_resolver == via_absolutize


# ── sidecar ─────────────────────────────────────────────────────────


def test_sidecar_root_read(tmp_path):
    graph_dir = tmp_path / "code-graphs" / "cortex-viz"
    graph_dir.mkdir(parents=True)
    (graph_dir / "meta.json").write_text(json.dumps({"root": str(tmp_path)}))
    graph_path = str(graph_dir / "graph")
    assert graph_source_root(graph_path, "cortex-viz") == str(tmp_path)


def test_sidecar_missing_falls_through(tmp_path, monkeypatch):
    graph_dir = tmp_path / "code-graphs" / "noproj"
    graph_dir.mkdir(parents=True)
    graph_path = str(graph_dir / "graph")
    monkeypatch.setattr(mod, "_root_from_registry", lambda proj_name: None)
    assert graph_source_root(graph_path, "noproj") is None


def test_sidecar_stale_root_not_on_disk_ignored(tmp_path, monkeypatch):
    graph_dir = tmp_path / "code-graphs" / "cortex-viz"
    graph_dir.mkdir(parents=True)
    (graph_dir / "meta.json").write_text(
        json.dumps({"root": "/does/not/exist/anywhere"})
    )
    graph_path = str(graph_dir / "graph")
    monkeypatch.setattr(mod, "_root_from_registry", lambda proj_name: None)
    assert graph_source_root(graph_path, "cortex-viz") is None


def test_sidecar_malformed_json_ignored(tmp_path, monkeypatch):
    graph_dir = tmp_path / "code-graphs" / "cortex-viz"
    graph_dir.mkdir(parents=True)
    (graph_dir / "meta.json").write_text("{ this is not json")
    graph_path = str(graph_dir / "graph")
    monkeypatch.setattr(mod, "_root_from_registry", lambda proj_name: None)
    assert graph_source_root(graph_path, "cortex-viz") is None


# ── registry fallback ───────────────────────────────────────────────


class _FakeRepo:
    def __init__(self, fs_path):
        self.fs_path = fs_path


class _FakeRegistry:
    def __init__(self, repos):
        self.repos = repos


def test_registry_fallback_matches_basename(tmp_path, monkeypatch):
    # No sidecar → falls through to the registry, matched by dir basename.
    graph_dir = tmp_path / "code-graphs" / "cortex-viz"
    graph_dir.mkdir(parents=True)
    graph_path = str(graph_dir / "graph")
    repo_root = "/Users/x/Developments/anthropic-partnership/cortex-viz"
    # _root_from_registry imports _build_registry lazily from domain_mapping.
    import cortex_viz.shared.domain_mapping as dm

    monkeypatch.setattr(
        dm, "_build_registry", lambda: _FakeRegistry([_FakeRepo(repo_root)])
    )
    assert graph_source_root(graph_path, "cortex-viz") == repo_root


def test_registry_no_match_returns_none(tmp_path, monkeypatch):
    graph_dir = tmp_path / "code-graphs" / "unknown-proj"
    graph_dir.mkdir(parents=True)
    graph_path = str(graph_dir / "graph")
    import cortex_viz.shared.domain_mapping as dm

    monkeypatch.setattr(
        dm,
        "_build_registry",
        lambda: _FakeRegistry([_FakeRepo("/Users/x/cortex-viz")]),
    )
    assert graph_source_root(graph_path, "unknown-proj") is None


def test_sidecar_precedence_over_registry(tmp_path, monkeypatch):
    graph_dir = tmp_path / "code-graphs" / "cortex-viz"
    graph_dir.mkdir(parents=True)
    (graph_dir / "meta.json").write_text(json.dumps({"root": str(tmp_path)}))
    graph_path = str(graph_dir / "graph")
    import cortex_viz.shared.domain_mapping as dm

    monkeypatch.setattr(
        dm,
        "_build_registry",
        lambda: _FakeRegistry([_FakeRepo("/some/other/cortex-viz")]),
    )
    # Sidecar wins.
    assert graph_source_root(graph_path, "cortex-viz") == str(tmp_path)
