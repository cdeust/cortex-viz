"""The Wiki view lists every root Cortex publishes into, not only the global one.

Cortex's ``project_root`` mode (ADR-0056 / ADR-0593) writes a page under
``<repo>/wiki/`` with ``wiki/manifest.json`` and nothing else: no
``wiki.pages`` row, nothing under ``~/.claude/methodology/wiki``. The ADRs
1060 to 1062 of the Cortex repository were published that way on 2026-09-09
and the view, which walked the global root only, never listed them. These
tests pin the discovery (``infrastructure.wiki_roots``) and the reader
(``infrastructure.wiki_read``) over a fixture store shaped like that
publication: a global root with one page, a repository whose project wiki
holds one ADR, and a repository without a manifest.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import cortex_viz.infrastructure.wiki_read as wiki_read
import cortex_viz.infrastructure.wiki_roots as wiki_roots

ADR_REL = "adr/cortex/1060-refuse-a-decision-written-into-code.md"
ADR_PAGE = (
    "---\n"
    "created: 2026-09-09T11:00:52Z\n"
    "kind: adr\n"
    "number: 1060\n"
    "status: accepted\n"
    "tags: [hooks, governance]\n"
    "title: Refuse a decision written into code at edit time\n"
    "---\n"
    "# ADR-1060: Refuse a decision written into code at edit time\n"
)


def _write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _manifest(repo: Path, project: str = "cortex", version: int = 1) -> None:
    _write(
        repo / "wiki" / "manifest.json",
        json.dumps({"version": version, "project": project, "pages": {}}),
    )


@pytest.fixture
def store(monkeypatch, tmp_path):
    """A global root, a repo with a project wiki, a repo without one."""
    global_root = tmp_path / "methodology" / "wiki"
    _write(
        global_root / "reference" / "cortex" / "overview.md",
        "---\ntitle: Overview\nkind: reference\ndomain: cortex\n---\nbody\n",
    )
    repo = tmp_path / "dev" / "Cortex"
    _manifest(repo)
    _write(repo / "wiki" / ADR_REL, ADR_PAGE)
    bare = tmp_path / "dev" / "no-wiki"
    _write(bare / "README.md", "not a wiki\n")
    monkeypatch.setattr(wiki_read, "WIKI_ROOT", global_root)
    monkeypatch.setattr(
        wiki_roots, "candidate_repo_paths", lambda: [str(repo), str(bare)]
    )
    return {"global": global_root, "repo": repo, "bare": bare}


# ── discovery ───────────────────────────────────────────────────────────


def test_project_roots_come_from_repos_carrying_a_manifest(store):
    roots = wiki_roots.project_roots()
    assert [(r.name, r.directory) for r in roots] == [
        ("cortex", store["repo"] / "wiki")
    ]


def test_project_root_prefix_namespaces_its_pages(store):
    (root,) = wiki_roots.project_roots()
    assert root.prefix == "@cortex/"
    assert wiki_roots.WikiRoot("", store["global"]).prefix == ""


@pytest.mark.parametrize(
    "manifest",
    [
        '{"version": 2, "project": "cortex", "pages": {}}',
        '{"version": 1, "project": "", "pages": {}}',
        '{"version": 1, "project": "a/b", "pages": {}}',
        '{"version": 1, "pages": {}}',
        "[]",
        "{not json",
    ],
)
def test_an_invalid_manifest_is_not_a_project_root(store, manifest):
    _write(store["bare"] / "wiki" / "manifest.json", manifest)
    assert [r.name for r in wiki_roots.project_roots()] == ["cortex"]


def test_two_checkouts_of_one_project_keep_the_first(monkeypatch, store):
    twin = store["repo"].parent / "Cortex-twin"
    _manifest(twin)
    monkeypatch.setattr(
        wiki_roots,
        "candidate_repo_paths",
        lambda: [str(store["repo"]), str(twin)],
    )
    assert [r.directory for r in wiki_roots.project_roots()] == [store["repo"] / "wiki"]


def test_candidate_repo_paths_reads_the_domain_registry(monkeypatch):
    from types import SimpleNamespace

    import cortex_viz.shared.domain_mapping as domain_mapping

    registry = SimpleNamespace(
        repos=[SimpleNamespace(fs_path="/r/one"), SimpleNamespace(fs_path="/r/two")]
    )
    monkeypatch.setattr(domain_mapping, "_build_registry", lambda: registry)
    assert wiki_roots.candidate_repo_paths() == ["/r/one", "/r/two"]


# ── locate ──────────────────────────────────────────────────────────────


def test_locate_keeps_a_plain_path_in_the_global_root(store):
    root, inside = wiki_roots.locate("reference/x.md", store["global"])
    assert (root.name, root.directory, inside) == (
        "",
        store["global"],
        "reference/x.md",
    )


def test_locate_maps_a_prefixed_path_to_its_project_root(store):
    root, inside = wiki_roots.locate("@cortex/" + ADR_REL, store["global"])
    assert (root.name, root.directory, inside) == (
        "cortex",
        store["repo"] / "wiki",
        ADR_REL,
    )


@pytest.mark.parametrize("path", ["@unknown/adr/x.md", "@cortex", "@cortex/", "@/x.md"])
def test_locate_refuses_an_unknown_or_empty_project(store, path):
    assert wiki_roots.locate(path, store["global"]) is None


# ── the list endpoint's reader ──────────────────────────────────────────


def test_list_pages_includes_the_project_wiki_adr(store):
    pages = wiki_read.list_pages()["pages"]
    adrs = [p for p in pages if p["kind"] == "adr"]
    assert len(adrs) == 1, pages
    assert adrs[0] == {
        "path": "@cortex/" + ADR_REL,
        "title": "Refuse a decision written into code at edit time",
        "kind": "adr",
        "domain": "cortex",
        "tags": ["hooks", "governance"],
        "maturity": "accepted",
        "created": "2026-09-09T11:00:52Z",
        "updated": "",
    }


def test_list_pages_keeps_the_global_root_first_and_unprefixed(store):
    paths = [p["path"] for p in wiki_read.list_pages()["pages"]]
    assert paths == ["reference/cortex/overview.md", "@cortex/" + ADR_REL]


def test_list_pages_survives_a_missing_global_root(monkeypatch, store):
    monkeypatch.setattr(wiki_read, "WIKI_ROOT", store["global"] / "absent")
    paths = [p["path"] for p in wiki_read.list_pages()["pages"]]
    assert paths == ["@cortex/" + ADR_REL]


def test_frontmatter_domain_wins_over_the_project_name(store):
    _write(
        store["repo"] / "wiki" / "notes" / "shared.md",
        "---\ntitle: Shared\nkind: note\ndomain: cortex-viz\n---\nbody\n",
    )
    by_path = {p["path"]: p for p in wiki_read.list_pages()["pages"]}
    assert by_path["@cortex/notes/shared.md"]["domain"] == "cortex-viz"


def test_list_projects_counts_the_project_wiki_under_its_project(store):
    projects = {p["domain"]: p for p in wiki_read.list_projects()["projects"]}
    assert projects["cortex"]["page_total"] == 2
    assert projects["cortex"]["page_counts_by_kind"] == {"reference": 1, "adr": 1}


# ── reading and writing a project page ──────────────────────────────────


def test_read_page_serves_a_project_wiki_page_with_its_domain(store):
    got = wiki_read.read_page("@cortex/" + ADR_REL)
    assert got["path"] == "@cortex/" + ADR_REL
    assert got["meta"]["title"] == "Refuse a decision written into code at edit time"
    assert got["meta"]["kind"] == "adr"
    assert got["meta"]["domain"] == "cortex"
    assert got["meta"]["tags"] == ["hooks", "governance"]
    assert got["body"].startswith("# ADR-1060")


def test_read_page_keeps_an_explicit_frontmatter_domain(store):
    _write(
        store["repo"] / "wiki" / "notes" / "shared.md",
        "---\ntitle: Shared\ndomain: cortex-viz\n---\nbody\n",
    )
    assert wiki_read.read_page("@cortex/notes/shared.md")["meta"]["domain"] == (
        "cortex-viz"
    )


def test_read_page_refuses_an_unknown_project(store):
    assert wiki_read.read_page("@elsewhere/" + ADR_REL) == {"error": "invalid path"}


def test_read_page_refuses_escaping_a_project_root(store):
    _write(store["repo"] / "secret.md", "not a wiki page\n")
    assert wiki_read.read_page("@cortex/../secret.md") == {"error": "invalid path"}


def test_read_page_reports_a_missing_project_page(store):
    assert wiki_read.read_page("@cortex/adr/cortex/9999-none.md") == {
        "error": "not found"
    }


def test_save_page_refuses_a_project_wiki_page(store):
    before = (store["repo"] / "wiki" / ADR_REL).read_text(encoding="utf-8")
    got = wiki_read.save_page("@cortex/" + ADR_REL, "overwritten\n")
    assert "error" in got and "Cortex" in got["error"]
    assert (store["repo"] / "wiki" / ADR_REL).read_text(encoding="utf-8") == before


def test_save_page_still_writes_a_global_page(store):
    got = wiki_read.save_page("notes/new.md", "fresh\n")
    assert got["ok"] is True
    assert (store["global"] / "notes" / "new.md").read_text(encoding="utf-8") == (
        "fresh\n"
    )


def test_bibliography_stays_global_root_only(store):
    _write(store["repo"] / "wiki" / "_bibliography" / "refs.bib", "@book{x}\n")
    assert wiki_read.read_bibliography("@cortex/_bibliography/refs.bib") == {
        "error": "invalid path"
    }
