"""Tests for domain_mapping, wiki_pg, wiki_graph, ap_graph_root and friends."""

from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime
from pathlib import Path

import pytest

from cortex_viz.infrastructure import ap_graph_root as apr
from cortex_viz.infrastructure import wiki_graph as wg
from cortex_viz.infrastructure import wiki_page_actions_pg as wpa
from cortex_viz.infrastructure import wiki_pg
from cortex_viz.shared import domain_mapping as dm

# ── domain_mapping ───────────────────────────────────────────────────────────


def test_extract_repo_name():
    assert dm._extract_repo_name("") == ""
    assert dm._extract_repo_name("git@github.com:cdeust/Cortex.git") == "cortex"
    assert dm._extract_repo_name("https://github.com/x/Foo-Bar/") == "foo-bar"


def test_shared_prefix():
    assert (
        dm._shared_prefix("ai-architect-pipeline", "ai-architect-prd") == "ai-architect"
    )
    assert dm._shared_prefix("cortex", "cortex-cowork") == "cortex"
    assert dm._shared_prefix("career-ops", "memory-monitor") == ""
    assert dm._shared_prefix("ai-x", "ai-y") == ""


def test_group_repos_and_indexes():
    repos = [
        dm.RepoInfo("/d/cortex", "cortex", "cortex"),
        dm.RepoInfo("/d/cortex-viz", "cortex-viz", "cortex-viz"),
        dm.RepoInfo("/d/mine", "mine", "other-name"),
        dm.RepoInfo("/d/lone", "lone", "lonely"),
    ]
    n2c = dm._group_repos(repos)
    assert n2c["cortex-viz"] == "cortex" and n2c["cortex"] == "cortex"
    assert repos[2].canonical == "other-name" and n2c["mine"] == "other-name"
    assert n2c["lonely"] == "lonely"
    slugs = dm._build_slug_index(repos)
    assert "d-cortex-viz" in slugs
    assert (
        dm._match_slug("-d-cortex-viz--claude-worktrees-x", slugs).dir_name
        == "cortex-viz"
    )
    assert dm._match_slug("-d-cortex-viz-worktrees-abc", slugs).dir_name == "cortex-viz"
    assert dm._match_slug("-d-cortex-vizzz", slugs).dir_name == "cortex-viz"
    assert dm._match_slug("-nothing", slugs) is None
    frags = dm._build_fragment_index(repos, n2c)
    assert frags["cortex-viz"] == "cortex" and frags["lonely"] == "lonely"
    assert "viz" not in frags  # < 4 chars


def test_subprocess_wrappers(monkeypatch):
    def ok(cmd, **kw):
        return b"  git@host:me/Repo.git\n" if "get-url" in cmd else b"/root\n"

    monkeypatch.setattr(dm.subprocess, "check_output", ok)
    assert dm._get_remote_url(Path("/x")) == "git@host:me/Repo.git"
    assert dm._git_root("/x/y") == "/root"

    def bad(cmd, **kw):
        raise subprocess.CalledProcessError(1, cmd)

    monkeypatch.setattr(dm.subprocess, "check_output", bad)
    assert dm._get_remote_url(Path("/x")) == ""
    assert dm._git_root("/x") is None


@pytest.fixture
def dev_tree(tmp_path, monkeypatch):
    """A fake dev root with real directories and a faked `git` boundary."""
    root = tmp_path / "dev"
    remotes = {}
    for rel, remote in [
        ("cortex", "https://github.com/c/Cortex.git"),
        ("cortex-viz", "https://github.com/c/cortex-viz.git"),
        ("org/ai-architect-pipeline", "https://github.com/c/ai-architect-pipeline.git"),
        ("org/ai-architect-prd-builder", ""),
        ("plain", ""),
    ]:
        p = root / rel
        (p / ".git").mkdir(parents=True)
        remotes[str(p)] = remote
    (root / ".hidden" / "x" / ".git").mkdir(parents=True)
    (root / "afile").write_text("x")
    (root / "empty").mkdir()
    monkeypatch.setenv("CORTEX_DEV_ROOT", str(root))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "nohome"))

    def fake(cmd, **kw):
        path = cmd[2]
        if "get-url" in cmd:
            if not remotes.get(path):
                raise subprocess.CalledProcessError(1, cmd)
            return remotes[path].encode()
        # rev-parse: walk up to a known repo
        cur = Path(path)
        for cand in [cur, *cur.parents]:
            if str(cand) in remotes:
                return str(cand).encode()
        raise subprocess.CalledProcessError(128, cmd)

    monkeypatch.setattr(dm.subprocess, "check_output", fake)
    dm._build_registry.cache_clear()
    yield root
    dm._build_registry.cache_clear()


def test_candidate_dev_roots(tmp_path, monkeypatch):
    (tmp_path / "Documents" / "Developments").mkdir(parents=True)
    (tmp_path / "code").mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.delenv("CORTEX_DEV_ROOT", raising=False)
    roots = dm.candidate_dev_roots()
    assert roots == [tmp_path / "Documents" / "Developments", tmp_path / "code"]
    monkeypatch.setenv("CORTEX_DEV_ROOT", str(tmp_path))
    assert dm.candidate_dev_roots()[0] == tmp_path


def test_discover_and_registry(dev_tree, monkeypatch):
    assert dm._discover_repos(dev_tree / "nope") == []
    repos = dm._discover_repos(dev_tree)
    assert {r.dir_name for r in repos} == {
        "cortex",
        "cortex-viz",
        "ai-architect-pipeline",
        "ai-architect-prd-builder",
        "plain",
    }
    reg = dm._build_registry()
    assert reg.path_to_repo[str(dev_tree / "plain")].canonical == "plain"
    # a duplicate dev root does not double-register repos
    monkeypatch.setattr(dm, "candidate_dev_roots", lambda: [dev_tree, dev_tree])
    dm._build_registry.cache_clear()
    assert len(dm._build_registry().repos) == 5


def test_resolve_domain_all_steps(dev_tree):
    assert dm.resolve_domain("") == "" and dm.resolve_domain("   ") == ""
    # path -> git root
    assert dm.resolve_domain(str(dev_tree / "cortex-viz" / "pkg")) == "cortex"
    assert (
        dm.resolve_domain(str(dev_tree / "org" / "ai-architect-pipeline"))
        == "ai-architect"
    )
    # path inside no repo but with a registry prefix -> prefix match
    assert dm._match_by_path("/nowhere/at/all", dm._build_registry()) is None
    reg = dm._build_registry()
    assert dm._match_by_path("-dash/path", reg) is None
    assert dm._match_by_path("noslash", reg) is None
    # slug
    slug = str(dev_tree / "plain").replace("/", "-")
    assert dm.resolve_domain(slug) == "plain"
    assert dm._match_by_slug("-short", reg) is None
    assert dm._match_by_slug("-zzzzzzzzzzzzzzzz", reg) is None
    # name hint
    assert dm.resolve_domain("Cortex-Viz") == "cortex"
    # fragment (exact + substring)
    assert dm.resolve_domain("ai-architect-prd") == "ai-architect"
    assert dm.resolve_domain("my plain notes") == "plain"
    # fallbacks
    assert dm.resolve_domain("zzz") == "zzz"
    assert dm.resolve_domain("-Users-cdeust-Developments-jarvis-extra") == "jarvis"
    assert dm.resolve_domain("-Users-cdeust-Documents-foo-worktrees-x") == "foo"
    assert dm.resolve_domain("-Users-cdeust-bar") == "bar"
    assert dm.resolve_domain("-other-thing-here") == ""
    assert (
        dm._slug_tail_fallback("-Users-cdeust-", "-users-cdeust-") == "-users-cdeust-"
    )


def test_match_by_path_prefix_when_git_fails(dev_tree, monkeypatch):
    reg = dm._build_registry()

    def bad(cmd, **kw):
        raise OSError

    monkeypatch.setattr(dm.subprocess, "check_output", bad)
    assert dm._match_by_path(str(dev_tree / "plain" / "sub"), reg) == "plain"


def test_resolve_cwd(dev_tree, tmp_path):
    assert dm.resolve_cwd("") == ""
    assert dm.resolve_cwd(str(dev_tree / "cortex" / "src")) == "cortex"
    assert dm.resolve_cwd(str(tmp_path / "elsewhere")) == ""


def test_resolve_cwd_root_not_in_registry(dev_tree, monkeypatch):
    monkeypatch.setattr(dm, "_git_root", lambda p: "/unregistered")
    assert dm.resolve_cwd("/anything") == ""


# ── ap_graph_root ────────────────────────────────────────────────────────────


def test_root_from_sidecar(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    out = tmp_path / "out"
    out.mkdir()
    graph = str(out / "graph")
    assert apr._root_from_sidecar(graph) is None  # no meta
    meta = out / "meta.json"
    meta.write_text(json.dumps({"root": str(src)}))
    assert apr._root_from_sidecar(graph) == str(src)
    assert apr._root_from_sidecar(graph + "/") == str(src)
    meta.write_text(json.dumps({"root": str(tmp_path / "gone")}))
    assert apr._root_from_sidecar(graph) is None
    meta.write_text(json.dumps({"root": ""}))
    assert apr._root_from_sidecar(graph) is None
    meta.write_text(json.dumps(["not", "dict"]))
    assert apr._root_from_sidecar(graph) is None
    meta.write_text("{broken")
    assert apr._root_from_sidecar(graph) is None


def test_root_from_registry(dev_tree):
    assert apr._root_from_registry("") is None
    assert apr._root_from_registry("plain") == str(dev_tree / "plain")
    assert apr._root_from_registry("unknown-repo") is None


def test_root_from_registry_failures(monkeypatch):
    def boom():
        raise RuntimeError

    monkeypatch.setattr(dm, "_build_registry", boom)
    assert apr._root_from_registry("x") is None
    monkeypatch.delattr(dm, "_build_registry")
    assert apr._root_from_registry("x") is None


def test_graph_source_root_and_absolutize(tmp_path, dev_tree):
    src = tmp_path / "s"
    src.mkdir()
    out = tmp_path / "o"
    out.mkdir()
    (out / "meta.json").write_text(json.dumps({"root": str(src)}))
    assert apr.graph_source_root(str(out / "graph"), "plain") == str(src)
    assert apr.graph_source_root(str(tmp_path / "zz" / "graph"), "plain") == str(
        dev_tree / "plain"
    )
    assert apr.absolutize(None, "a/b.py") == "a/b.py"
    assert apr.absolutize("/r/", "a/b.py") == os.path.join("/r", "a/b.py")


# ── wiki_pg / wiki_page_actions_pg / wiki_graph ──────────────────────────────


class Store:
    def __init__(self, handler):
        self.handler = handler
        self.calls = []

    def query(self, sql, params=None, **kw):
        self.calls.append((sql, params, kw))
        out = self.handler(sql, params)
        if isinstance(out, Exception):
            raise out
        return out


def test_wiki_pg_iso():
    ts = datetime(2026, 1, 1)
    assert wiki_pg._isodict({"a": ts, "b": 1}) == {"a": ts.isoformat(), "b": 1}


def test_page_meta_full():
    ts = datetime(2026, 2, 2)

    def h(sql, params):
        if "FROM wiki.pages WHERE rel_path" in sql:
            return [{"id": 4, "title": "T", "planted": ts}]
        if "FROM wiki.links l" in sql:
            return [{"src_page_id": 9, "dst_slug": "t"}]
        if "src_page_id = %s" in sql:
            return [{"dst_slug": "o"}]
        return [{"id": 1, "cited_at": ts}]

    meta = wiki_pg.page_meta(Store(h), "a/b.md")
    assert meta["db_row"] == {"id": 4, "title": "T", "planted": ts.isoformat()}
    assert meta["backlinks"][0]["src_page_id"] == 9
    assert meta["outbound_links"] == [{"dst_slug": "o"}]
    assert meta["recent_citations"][0]["cited_at"] == ts.isoformat()


def test_page_meta_degrades():
    empty = {
        "rel_path": "p",
        "db_row": None,
        "backlinks": [],
        "outbound_links": [],
        "recent_citations": [],
    }
    assert wiki_pg.page_meta(Store(lambda s, p: RuntimeError()), "p") == empty
    assert wiki_pg.page_meta(Store(lambda s, p: []), "p") == empty

    def h(sql, params):
        if "rel_path = %s" in sql:
            return [{"id": 1}]
        return RuntimeError("no links table")

    meta = wiki_pg.page_meta(Store(h), "p")
    assert meta["db_row"] == {"id": 1} and meta["backlinks"] == []


def test_list_memos():
    ts = datetime(2026, 3, 3)
    st = Store(lambda s, p: [{"id": 1, "created_at": ts}])
    out = wiki_pg.list_memos(st, "page", "5", limit=1000)
    assert out == {"memos": [{"id": 1, "created_at": ts.isoformat()}], "count": 1}
    assert st.calls[0][1] == ("page", 5, 200)
    assert wiki_pg.list_memos(st, "page", "x") == {"memos": [], "count": 0}
    assert wiki_pg.list_memos(st, "page", None) == {"memos": [], "count": 0}
    st.handler = lambda s, p: RuntimeError()
    assert wiki_pg.list_memos(st, "page", 1) == {"memos": [], "count": 0}
    st.handler = lambda s, p: []
    wiki_pg.list_memos(st, "page", 1, limit=0)
    assert st.calls[-1][1][2] == 1


def test_wiki_page_actions_pg():
    st = Store(lambda s, p: [{"id": 3, "domain": "d", "rel_path": "r", "title": "t"}])
    assert wpa.load_page_by_id(st, 3)["domain"] == "d"
    assert wpa.load_page_by_id(Store(lambda s, p: []), 3) is None
    assert wpa.load_page_by_id(Store(lambda s, p: RuntimeError()), 3) is None
    st = Store(
        lambda s, p: [
            {"source_path": "a.py", "link_kind": "documents", "confidence": 1.0}
        ]
    )
    assert wpa.load_page_sources(st, 3) == [
        {"source_path": "a.py", "link_kind": "documents", "confidence": 1.0}
    ]
    assert wpa.load_page_sources(Store(lambda s, p: RuntimeError()), 3) == []


@pytest.mark.parametrize(
    "fn",
    [
        wg.load_wiki_pages,
        wg.load_wiki_links,
        wg.load_wiki_memory_links,
        wg.load_wiki_page_sources,
        wg.load_wiki_session_links,
    ],
)
def test_wiki_graph_loaders(fn):
    st = Store(lambda s, p: [{"id": 1}])
    assert fn(st) == [{"id": 1}]
    assert st.calls[0][2] == {"batch": True}
    assert fn(Store(lambda s, p: RuntimeError())) == []


def test_load_memories_by_ids():
    st = Store(lambda s, p: [{"id": 2}])
    assert wg.load_memories_by_ids(st, [3, None, "2", 3]) == [{"id": 2}]
    assert st.calls[0][1] == ([2, 3],)
    assert wg.load_memories_by_ids(st, [None]) == []
    assert len(st.calls) == 1
    assert wg.load_memories_by_ids(Store(lambda s, p: RuntimeError()), [1]) == []
