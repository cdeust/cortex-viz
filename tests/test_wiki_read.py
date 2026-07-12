"""Unit tests for ``infrastructure.wiki_read.read_page`` frontmatter
normalisation — the fix for the "Page not found" false-positive: list-typed
frontmatter keys (``tags``, ``curation_gaps``, ...) arrive from
``parse_yaml_frontmatter`` as raw strings (flat-KV parser contract), and the
client does ``tags.forEach`` / ``Array.isArray(meta.curation_gaps)`` on them.
"""

from __future__ import annotations

import cortex_viz.infrastructure.wiki_read as mod


def _write_page(tmp_path, rel_path: str, content: str):
    p = tmp_path / rel_path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return p


def test_read_page_normalises_tags_to_a_list(monkeypatch, tmp_path):
    monkeypatch.setattr(mod, "WIKI_ROOT", tmp_path)
    _write_page(
        tmp_path,
        "page.md",
        "---\ntags: [a, b]\n---\nbody\n",
    )
    got = mod.read_page("page.md")
    assert got["meta"]["tags"] == ["a", "b"]


def test_read_page_normalises_curation_gaps_to_a_list(monkeypatch, tmp_path):
    monkeypatch.setattr(mod, "WIKI_ROOT", tmp_path)
    _write_page(
        tmp_path,
        "page.md",
        "---\ncuration_gaps: [purpose, tests]\n---\nbody\n",
    )
    got = mod.read_page("page.md")
    assert got["meta"]["curation_gaps"] == ["purpose", "tests"]


def test_read_page_leaves_non_list_keys_with_brackets_as_a_string(
    monkeypatch, tmp_path
):
    # title is not in _LIST_KEYS -- a bracketed value there is content,
    # not a list, and must survive untouched.
    monkeypatch.setattr(mod, "WIKI_ROOT", tmp_path)
    _write_page(
        tmp_path,
        "page.md",
        "---\ntitle: [WIP] foo\n---\nbody\n",
    )
    got = mod.read_page("page.md")
    assert got["meta"]["title"] == "[WIP] foo"


def test_read_page_no_frontmatter_yields_empty_meta_and_no_error(monkeypatch, tmp_path):
    monkeypatch.setattr(mod, "WIKI_ROOT", tmp_path)
    _write_page(tmp_path, "page.md", "just a body, no frontmatter\n")
    got = mod.read_page("page.md")
    assert "error" not in got
    assert got["meta"] == {}


def test_read_page_does_not_inject_absent_list_keys(monkeypatch, tmp_path):
    # tags is absent from the frontmatter -- it must stay absent from meta,
    # not be injected as [], since the client relies on falsy/absence checks.
    monkeypatch.setattr(mod, "WIKI_ROOT", tmp_path)
    _write_page(
        tmp_path,
        "page.md",
        "---\ntitle: hello\n---\nbody\n",
    )
    got = mod.read_page("page.md")
    assert "tags" not in got["meta"]
    assert "curation_gaps" not in got["meta"]
