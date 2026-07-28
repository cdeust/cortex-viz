"""Unit tests for ``infrastructure.wiki_read.read_page`` frontmatter
normalisation — the fix for the "Page not found" false-positive: list-typed
frontmatter keys (``tags``, ``curation_gaps``, ...) arrive from
``parse_yaml_frontmatter`` as raw strings (flat-KV parser contract), and the
client does ``tags.forEach`` / ``Array.isArray(meta.curation_gaps)`` on them.
"""

from __future__ import annotations

import pytest

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


# ── containment (CWE-22) ───────────────────────────────────────────────
# ``_safe_path`` is the only thing between a request-supplied ``path=`` /
# ``rel_path`` and a filesystem read or WRITE (``save_page``). Its refusal
# arms are tested like happy paths: each asserts the observable effect (the
# error shape returned, and for writes that nothing landed on disk).


@pytest.mark.parametrize(
    "escape",
    [
        "../outside.md",
        "sub/../../outside.md",
        "../../../../etc/passwd.md",
        "/etc/passwd.md",
    ],
)
def test_read_page_refuses_a_path_escaping_the_wiki_root(
    monkeypatch, tmp_path, escape: str
):
    root = tmp_path / "wiki"
    root.mkdir()
    (tmp_path / "outside.md").write_text("SECRET\n", encoding="utf-8")
    monkeypatch.setattr(mod, "WIKI_ROOT", root)
    assert mod.read_page(escape) == {"error": "invalid path"}


def test_read_page_refuses_a_symlink_escaping_the_wiki_root(monkeypatch, tmp_path):
    """The escape a textual check cannot see: an innocent name inside the
    root whose target is outside it."""
    root = tmp_path / "wiki"
    root.mkdir()
    secret = tmp_path / "outside.md"
    secret.write_text("SECRET\n", encoding="utf-8")
    (root / "innocent.md").symlink_to(secret)
    monkeypatch.setattr(mod, "WIKI_ROOT", root)
    assert mod.read_page("innocent.md") == {"error": "invalid path"}


def test_read_page_refuses_a_sibling_directory_sharing_the_root_prefix(
    monkeypatch, tmp_path
):
    """``<root>-backup`` is not inside ``<root>``."""
    root = tmp_path / "wiki"
    root.mkdir()
    sibling = tmp_path / "wiki-backup"
    sibling.mkdir()
    (sibling / "page.md").write_text("SECRET\n", encoding="utf-8")
    monkeypatch.setattr(mod, "WIKI_ROOT", root)
    assert mod.read_page("../wiki-backup/page.md") == {"error": "invalid path"}


def test_read_page_refuses_a_non_markdown_suffix(monkeypatch, tmp_path):
    root = tmp_path / "wiki"
    root.mkdir()
    (root / "notes.txt").write_text("x\n", encoding="utf-8")
    monkeypatch.setattr(mod, "WIKI_ROOT", root)
    assert mod.read_page("notes.txt") == {"error": "invalid path"}


def test_read_page_refuses_an_empty_path(monkeypatch, tmp_path):
    monkeypatch.setattr(mod, "WIKI_ROOT", tmp_path)
    assert mod.read_page("") == {"error": "invalid path"}


def test_save_page_writes_inside_the_wiki_root(monkeypatch, tmp_path):
    """Paired positive for the refusals below — a legitimate save must still
    create parent directories and land the bytes."""
    root = tmp_path / "wiki"
    root.mkdir()
    monkeypatch.setattr(mod, "WIKI_ROOT", root)
    got = mod.save_page("new/nested/page.md", "hello\n")
    assert got["ok"] is True
    assert (root / "new" / "nested" / "page.md").read_text(
        encoding="utf-8"
    ) == "hello\n"


@pytest.mark.parametrize(
    "escape",
    [
        "../pwned.md",
        "sub/../../pwned.md",
        "/tmp/pwned.md",
    ],
)
def test_save_page_refuses_to_write_outside_the_wiki_root(
    monkeypatch, tmp_path, escape: str
):
    root = tmp_path / "wiki"
    root.mkdir()
    monkeypatch.setattr(mod, "WIKI_ROOT", root)
    assert mod.save_page(escape, "pwned\n") == {"error": "invalid path"}
    # Negative assertion: absence of the write IS the behaviour under test.
    assert not (tmp_path / "pwned.md").exists()


def test_save_page_refuses_to_create_directories_outside_the_wiki_root(
    monkeypatch, tmp_path
):
    """``save_page`` mkdirs the parent before writing — a refused path must
    not leave that directory behind either."""
    root = tmp_path / "wiki"
    root.mkdir()
    monkeypatch.setattr(mod, "WIKI_ROOT", root)
    assert mod.save_page("../evil/nested/page.md", "x\n") == {"error": "invalid path"}
    assert not (tmp_path / "evil").exists()


def test_read_bibliography_refuses_a_path_outside_the_wiki_root(monkeypatch, tmp_path):
    root = tmp_path / "wiki"
    root.mkdir()
    (tmp_path / "outside.bib").write_text("@book{x}\n", encoding="utf-8")
    monkeypatch.setattr(mod, "WIKI_ROOT", root)
    assert mod.read_bibliography("../outside.bib") == {"error": "invalid path"}
