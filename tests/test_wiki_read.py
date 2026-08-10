"""Unit tests for ``infrastructure.wiki_read.read_page`` frontmatter
normalisation — the fix for the "Page not found" false-positive: list-typed
frontmatter keys (``tags``, ``curation_gaps``, ...) arrive from
``parse_yaml_frontmatter`` as raw strings (flat-KV parser contract), and the
client does ``tags.forEach`` / ``Array.isArray(meta.curation_gaps)`` on them.
"""

from __future__ import annotations

import contextlib
import locale
from pathlib import Path

import pytest

import cortex_viz.infrastructure.wiki_read as mod


def _write_page(tmp_path, rel_path: str, content: str):
    p = tmp_path / rel_path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return p


# ── UTF-8 pinning under a non-UTF-8 process locale ──────────────────────
# Every read_text/write_text call in this module pins encoding="utf-8"
# explicitly rather than trusting the platform default -- the CI matrix
# runs a Windows job specifically because that default is NOT UTF-8
# everywhere. Killing an "encoding=None" mutant needs an environment where
# the default genuinely differs from UTF-8.
#
# A subprocess with LC_ALL=C reproduces that divergence but is invisible to
# mutmut's coverage-based test-to-mutant association: mutmut decides which
# tests to run for a given mutant by tracing which lines a test's OWN
# process executes during the stats pass, and a spawned child process's
# execution is outside that trace -- a subprocess-based test is silently
# never selected to run against the mutant it targets (verified empirically:
# it passes locally in isolation, yet the mutant it exists to kill still
# reports "survived" in the real run). `locale.setlocale` changes the SAME
# process's C-library locale state at runtime, which `Path.read_text` /
# `Path.write_text` genuinely consult (also verified empirically --
# monkeypatching the higher-level `locale.getencoding` /
# `locale.getpreferredencoding` Python functions does NOT reach them; only
# the C-level `setlocale` call does) -- so this is both a real behavioural
# probe and one mutmut's coverage tracer can see.


@contextlib.contextmanager
def _c_locale():
    """Force the process's C-library locale to "C" (US-ASCII preferred
    encoding) for the duration of the block, restoring the prior locale
    afterward even on failure."""
    original = locale.setlocale(locale.LC_ALL)
    try:
        locale.setlocale(locale.LC_ALL, "C")
        yield
    finally:
        locale.setlocale(locale.LC_ALL, original)


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


# ── _parse_list ──────────────────────────────────────────────────────────
# Frontmatter list values arrive in three raw shapes from the flat-KV
# parser: an already-materialised list (only via direct dict construction,
# never from the parser itself, but the function must still honour it),
# a bracketed comma-separated string, and a bare comma-separated string.


def test_parse_list_passes_through_a_real_list():
    assert mod._parse_list(["a", " b ", ""]) == ["a", "b"]


def test_parse_list_falsy_value_is_empty():
    assert mod._parse_list(None) == []
    assert mod._parse_list("") == []


def test_parse_list_strips_brackets_and_splits_on_comma():
    assert mod._parse_list("[a, b, c]") == ["a", "b", "c"]


def test_parse_list_without_brackets_still_splits():
    assert mod._parse_list("a, b") == ["a", "b"]


def test_parse_list_strips_quotes_from_each_token():
    assert mod._parse_list("['a', \"b\"]") == ["a", "b"]


def test_parse_list_drops_whitespace_only_tokens():
    assert mod._parse_list("a, , b") == ["a", "b"]


# ── _title_from ──────────────────────────────────────────────────────────


def test_title_from_uses_frontmatter_title_when_present():
    assert mod._title_from({"title": "Explicit Title"}, Path("/x/ignored.md")) == (
        "Explicit Title"
    )


def test_title_from_falls_back_to_stem_with_separators_as_spaces():
    assert mod._title_from({}, Path("/x/my-cool_page.md")) == "my cool page"


def test_title_from_falls_back_to_filename_when_stem_is_all_separators():
    # stem.replace("-", " ").replace("_", " ").strip() == "" for a stem made
    # only of separators -- the final fallback is the literal file name.
    assert mod._title_from({}, Path("/x/--__.md")) == "--__.md"


# ── _page_item / list_pages / list_projects / _iter_md ─────────────────


def test_page_item_reads_frontmatter_fields(tmp_path):
    root = tmp_path / "wiki"
    root.mkdir()
    _write_page(
        root,
        "a/b.md",
        "---\n"
        "title: B Page\n"
        "kind: adr\n"
        "domain: cortex\n"
        "tags: [x, y]\n"
        "status: draft\n"
        "date: 2026-01-01\n"
        "tended: 2026-02-02\n"
        "---\n"
        "body\n",
    )
    item = mod._page_item(root / "a" / "b.md", root)
    assert item == {
        "path": "a/b.md",
        "title": "B Page",
        "kind": "adr",
        "domain": "cortex",
        "tags": ["x", "y"],
        "maturity": "draft",
        "created": "2026-01-01",
        "updated": "2026-02-02",
    }


def test_page_item_defaults_kind_to_page_when_absent(tmp_path):
    root = tmp_path / "wiki"
    root.mkdir()
    _write_page(root, "c.md", "no frontmatter\n")
    item = mod._page_item(root / "c.md", root)
    assert item["kind"] == "page"
    assert item["domain"] == ""
    assert item["tags"] == []
    assert item["maturity"] == ""
    assert item["created"] == ""
    assert item["updated"] == ""


def test_page_item_prefers_maturity_over_status_fallback(tmp_path):
    root = tmp_path / "wiki"
    root.mkdir()
    _write_page(root, "m.md", "---\nmaturity: stable\nstatus: draft\n---\nbody\n")
    item = mod._page_item(root / "m.md", root)
    assert item["maturity"] == "stable"


def test_page_item_prefers_created_over_date_fallback(tmp_path):
    root = tmp_path / "wiki"
    root.mkdir()
    _write_page(root, "d.md", "---\ncreated: 2026-01-01\ndate: 2020-01-01\n---\nbody\n")
    item = mod._page_item(root / "d.md", root)
    assert item["created"] == "2026-01-01"


def test_page_item_unreadable_file_yields_empty_meta(monkeypatch, tmp_path):
    root = tmp_path / "wiki"
    root.mkdir()
    p = _write_page(root, "u.md", "---\ntitle: X\n---\nbody\n")

    def _boom(*a, **k):
        raise OSError("permission denied")

    monkeypatch.setattr(Path, "read_text", _boom)
    item = mod._page_item(p, root)
    assert item["title"] == "u"  # falls back to the filename stem
    assert item["kind"] == "page"


def test_iter_md_skips_the_bibliography_subtree(tmp_path):
    root = tmp_path / "wiki"
    root.mkdir()
    _write_page(root, "keep.md", "keep\n")
    _write_page(root, "_bibliography/refs.md", "skip me\n")
    found = [str(p.relative_to(root)) for p in mod._iter_md(root)]
    assert found == ["keep.md"]


def test_list_pages_returns_empty_when_root_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(mod, "WIKI_ROOT", tmp_path / "does-not-exist")
    assert mod.list_pages() == {"pages": []}


def test_list_pages_returns_every_page(monkeypatch, tmp_path):
    root = tmp_path / "wiki"
    root.mkdir()
    _write_page(root, "one.md", "---\ntitle: One\n---\nbody\n")
    _write_page(root, "two.md", "---\ntitle: Two\n---\nbody\n")
    monkeypatch.setattr(mod, "WIKI_ROOT", root)
    got = mod.list_pages()
    titles = sorted(p["title"] for p in got["pages"])
    assert titles == ["One", "Two"]


def test_list_projects_returns_empty_when_root_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(mod, "WIKI_ROOT", tmp_path / "does-not-exist")
    assert mod.list_projects() == {"projects": []}


def test_list_projects_groups_by_domain_with_kind_counts(monkeypatch, tmp_path):
    root = tmp_path / "wiki"
    root.mkdir()
    _write_page(root, "a.md", "---\ndomain: cortex\nkind: adr\n---\nbody\n")
    _write_page(root, "b.md", "---\ndomain: cortex\nkind: adr\n---\nbody\n")
    _write_page(root, "c.md", "---\ndomain: cortex\nkind: note\n---\nbody\n")
    _write_page(root, "d.md", "no frontmatter\n")  # domain absent -> _general
    monkeypatch.setattr(mod, "WIKI_ROOT", root)
    got = mod.list_projects()
    by_domain = {p["domain"]: p for p in got["projects"]}
    assert by_domain["cortex"]["page_total"] == 3
    assert by_domain["cortex"]["page_counts_by_kind"] == {"adr": 2, "note": 1}
    assert by_domain["_general"]["page_total"] == 1


def test_list_projects_sorts_by_descending_page_total(monkeypatch, tmp_path):
    root = tmp_path / "wiki"
    root.mkdir()
    _write_page(root, "big1.md", "---\ndomain: big\n---\nb\n")
    _write_page(root, "big2.md", "---\ndomain: big\n---\nb\n")
    _write_page(root, "small.md", "---\ndomain: small\n---\nb\n")
    monkeypatch.setattr(mod, "WIKI_ROOT", root)
    got = mod.list_projects()
    domains_in_order = [p["domain"] for p in got["projects"]]
    assert domains_in_order == ["big", "small"]


# ── list_bibliography / read_bibliography ───────────────────────────────


def test_list_bibliography_empty_when_dir_missing(monkeypatch, tmp_path):
    root = tmp_path / "wiki"
    root.mkdir()
    monkeypatch.setattr(mod, "WIKI_ROOT", root)
    assert mod.list_bibliography() == {"files": []}


def test_list_bibliography_counts_entries_and_reports_size(monkeypatch, tmp_path):
    root = tmp_path / "wiki"
    bib_dir = root / "_bibliography"
    bib_dir.mkdir(parents=True)
    content = "@book{a,\n  title={A}\n}\n@article{b,\n  title={B}\n}\n"
    (bib_dir / "refs.bib").write_text(content, encoding="utf-8")
    monkeypatch.setattr(mod, "WIKI_ROOT", root)
    got = mod.list_bibliography()
    assert len(got["files"]) == 1
    entry = got["files"][0]
    assert entry["path"] == "_bibliography/refs.bib"
    assert entry["entries"] == 2
    assert entry["size"] == len(content.encode("utf-8"))


def test_list_bibliography_counts_a_leading_entry_with_no_preceding_newline(
    monkeypatch, tmp_path
):
    root = tmp_path / "wiki"
    bib_dir = root / "_bibliography"
    bib_dir.mkdir(parents=True)
    # The file's very first byte is '@' -- no "\n@" occurs, so the leading
    # entry would be undercounted without the lstrip().startswith("@") arm.
    (bib_dir / "solo.bib").write_text("@book{a,\n  title={A}\n}\n", encoding="utf-8")
    monkeypatch.setattr(mod, "WIKI_ROOT", root)
    got = mod.list_bibliography()
    assert got["files"][0]["entries"] == 1


def test_read_bibliography_returns_content_and_byte_size(monkeypatch, tmp_path):
    root = tmp_path / "wiki"
    bib_dir = root / "_bibliography"
    bib_dir.mkdir(parents=True)
    content = "@book{a}\n"
    (bib_dir / "refs.bib").write_text(content, encoding="utf-8")
    monkeypatch.setattr(mod, "WIKI_ROOT", root)
    got = mod.read_bibliography("_bibliography/refs.bib")
    assert got == {
        "path": "_bibliography/refs.bib",
        "content": content,
        "size": len(content),
    }


def test_read_bibliography_refuses_a_bib_outside_the_bibliography_subtree(
    monkeypatch, tmp_path
):
    # _safe_path alone would accept this (it's under WIKI_ROOT and ends in
    # .bib) -- the "_bibliography" in p.parts guard is a second, independent
    # gate that must reject it too.
    root = tmp_path / "wiki"
    root.mkdir()
    (root / "stray.bib").write_text("@book{a}\n", encoding="utf-8")
    monkeypatch.setattr(mod, "WIKI_ROOT", root)
    assert mod.read_bibliography("stray.bib") == {"error": "invalid path"}


# ── save_page byte accounting ───────────────────────────────────────────


def test_save_page_reports_utf8_byte_length_not_character_length(monkeypatch, tmp_path):
    root = tmp_path / "wiki"
    root.mkdir()
    monkeypatch.setattr(mod, "WIKI_ROOT", root)
    content = "café"  # 4 chars, 5 UTF-8 bytes
    got = mod.save_page("page.md", content)
    assert got["bytes"] == 5
    assert len(content) == 4


def test_save_page_success_returns_the_exact_response_shape(monkeypatch, tmp_path):
    root = tmp_path / "wiki"
    root.mkdir()
    monkeypatch.setattr(mod, "WIKI_ROOT", root)
    got = mod.save_page("page.md", "hi\n")
    assert got == {"ok": True, "path": "page.md", "bytes": 3}


def test_save_page_refuses_a_non_markdown_suffix(monkeypatch, tmp_path):
    # _safe_path's suffix gate applies to writes exactly as it does to reads
    # -- a non-".md" target must never be created, regardless of containment.
    root = tmp_path / "wiki"
    root.mkdir()
    monkeypatch.setattr(mod, "WIKI_ROOT", root)
    assert mod.save_page("notes.txt", "x") == {"error": "invalid path"}
    assert not (root / "notes.txt").exists()


def test_save_page_oserror_on_write_reports_the_exact_message(monkeypatch, tmp_path):
    root = tmp_path / "wiki"
    root.mkdir()
    monkeypatch.setattr(mod, "WIKI_ROOT", root)

    def _boom(self, content, encoding=None):
        raise OSError("disk full")

    monkeypatch.setattr(Path, "write_text", _boom)
    assert mod.save_page("page.md", "x") == {"error": "disk full"}


def test_save_page_round_trips_non_ascii_content_under_a_non_utf8_locale(
    monkeypatch, tmp_path
):
    root = tmp_path / "wiki"
    root.mkdir()
    monkeypatch.setattr(mod, "WIKI_ROOT", root)
    with _c_locale():
        got = mod.save_page("page.md", "café\n")
    assert got["ok"] is True, got
    assert (root / "page.md").read_bytes().decode("utf-8") == "café\n"


# ── _parse_list: the exact strip-charsets, not any charset that happens
#    to also remove brackets/quotes ──────────────────────────────────────


def test_parse_list_only_strips_the_bracket_characters_not_arbitrary_chars():
    # A value bracketed with 'X' rather than '[' / ']' must survive: proves
    # the strip charset is exactly "[]", not a wider set that also matches
    # mutmut's "XX[]XX" marker wrapping.
    assert mod._parse_list("Xone, twoX") == ["Xone", "twoX"]


def test_parse_list_only_strips_quote_characters_not_arbitrary_chars():
    # A token bounded by 'X' (no quotes) must survive per-token stripping
    # unchanged -- proves the per-token strip charset is exactly "'\"".
    assert mod._parse_list("Xa, Xb") == ["Xa", "Xb"]


# ── _page_item: "updated" precedence, and encoding/errors robustness ────


def test_page_item_prefers_updated_over_tended_fallback(tmp_path):
    root = tmp_path / "wiki"
    root.mkdir()
    _write_page(
        root, "u.md", "---\nupdated: 2026-03-03\ntended: 2020-01-01\n---\nbody\n"
    )
    item = mod._page_item(root / "u.md", root)
    assert item["updated"] == "2026-03-03"


def test_page_item_replaces_invalid_utf8_bytes_instead_of_raising(tmp_path):
    root = tmp_path / "wiki"
    root.mkdir()
    p = root / "bad.md"
    p.write_bytes(b"---\ntitle: ok\n---\nbody \xff bytes\n")
    item = mod._page_item(p, root)
    assert item["title"] == "ok"  # must not raise, invalid byte gets replaced


def test_page_item_decodes_utf8_regardless_of_process_locale(tmp_path):
    root = tmp_path / "wiki"
    root.mkdir()
    p = root / "accent.md"
    p.write_bytes("---\ntitle: café\n---\nbody\n".encode())
    with _c_locale():
        item = mod._page_item(p, root)
    assert item["title"] == "café"


# ── read_page: exact response shapes, and the OSError arm ────────────────


def test_read_page_not_found_returns_the_exact_error_shape(monkeypatch, tmp_path):
    root = tmp_path / "wiki"
    root.mkdir()
    monkeypatch.setattr(mod, "WIKI_ROOT", root)
    assert mod.read_page("missing.md") == {"error": "not found"}


def test_read_page_success_returns_the_exact_response_shape(monkeypatch, tmp_path):
    root = tmp_path / "wiki"
    root.mkdir()
    monkeypatch.setattr(mod, "WIKI_ROOT", root)
    _write_page(root, "page.md", "---\ntitle: X\n---\nhello\n")
    assert mod.read_page("page.md") == {
        "path": "page.md",
        "meta": {"title": "X"},
        "body": "hello",
    }


def test_read_page_oserror_on_read_reports_the_exact_message(monkeypatch, tmp_path):
    root = tmp_path / "wiki"
    root.mkdir()
    monkeypatch.setattr(mod, "WIKI_ROOT", root)
    _write_page(root, "page.md", "body\n")

    def _boom(self, encoding=None, errors=None):
        raise OSError("permission denied")

    monkeypatch.setattr(Path, "read_text", _boom)
    assert mod.read_page("page.md") == {"error": "permission denied"}


def test_read_page_replaces_invalid_utf8_bytes_instead_of_raising(
    monkeypatch, tmp_path
):
    root = tmp_path / "wiki"
    root.mkdir()
    monkeypatch.setattr(mod, "WIKI_ROOT", root)
    (root / "bad.md").write_bytes(b"---\ntitle: ok\n---\nbody \xff bytes\n")
    got = mod.read_page("bad.md")
    assert "error" not in got
    assert got["meta"]["title"] == "ok"


def test_read_page_decodes_utf8_regardless_of_process_locale(monkeypatch, tmp_path):
    root = tmp_path / "wiki"
    root.mkdir()
    monkeypatch.setattr(mod, "WIKI_ROOT", root)
    _write_page(root, "accent.md", "---\ntitle: X\n---\ncafé\n")
    with _c_locale():
        got = mod.read_page("accent.md")
    assert got["body"] == "café"


# ── list_bibliography: leading-entry boundary, the else-arm, OSError
#    continue-vs-break, and invalid-byte robustness ──────────────────────


def test_list_bibliography_counts_a_leading_entry_preceded_only_by_whitespace(
    monkeypatch, tmp_path
):
    # Leading SPACES (not a preceding "\n@") before the first "@" -- distinguishes
    # lstrip() (strips them, sees "@...", counts it) from rstrip() (leaves them,
    # the text does not start with "@", would NOT count it).
    root = tmp_path / "wiki"
    bib_dir = root / "_bibliography"
    bib_dir.mkdir(parents=True)
    (bib_dir / "lead.bib").write_text("   @book{a,\n  title={A}\n}\n", encoding="utf-8")
    monkeypatch.setattr(mod, "WIKI_ROOT", root)
    got = mod.list_bibliography()
    assert got["files"][0]["entries"] == 1


def test_list_bibliography_reports_zero_entries_for_a_file_with_no_at_sign(
    monkeypatch, tmp_path
):
    root = tmp_path / "wiki"
    bib_dir = root / "_bibliography"
    bib_dir.mkdir(parents=True)
    (bib_dir / "empty.bib").write_text("no entries in this file\n", encoding="utf-8")
    monkeypatch.setattr(mod, "WIKI_ROOT", root)
    got = mod.list_bibliography()
    assert got["files"][0]["entries"] == 0


def test_list_bibliography_skips_an_unreadable_file_and_still_lists_the_rest(
    monkeypatch, tmp_path
):
    root = tmp_path / "wiki"
    bib_dir = root / "_bibliography"
    bib_dir.mkdir(parents=True)
    (bib_dir / "a_broken.bib").write_text("@book{a}\n", encoding="utf-8")
    (bib_dir / "b_ok.bib").write_text("@book{b}\n", encoding="utf-8")
    monkeypatch.setattr(mod, "WIKI_ROOT", root)

    real_read_text = Path.read_text

    def _flaky(self, *a, **k):
        if self.name == "a_broken.bib":
            raise OSError("unreadable")
        return real_read_text(self, *a, **k)

    monkeypatch.setattr(Path, "read_text", _flaky)
    got = mod.list_bibliography()
    names = {f["path"] for f in got["files"]}
    # "continue" (not "break") must let the loop reach b_ok.bib despite
    # a_broken.bib raising first (sorted order puts a_broken.bib first).
    assert names == {"_bibliography/b_ok.bib"}


def test_list_bibliography_replaces_invalid_utf8_bytes_instead_of_raising(
    monkeypatch, tmp_path
):
    root = tmp_path / "wiki"
    bib_dir = root / "_bibliography"
    bib_dir.mkdir(parents=True)
    (bib_dir / "bad.bib").write_bytes(b"@book{a,\n  title={A \xff B}\n}\n")
    monkeypatch.setattr(mod, "WIKI_ROOT", root)
    got = mod.list_bibliography()
    assert got["files"][0]["entries"] == 1  # did not raise; counted normally


# ── read_bibliography: suffix gate, exact OSError shape, invalid bytes,
#    and encoding robustness (content IS surfaced here, unlike list) ─────


def test_read_bibliography_refuses_a_non_bib_suffix(monkeypatch, tmp_path):
    root = tmp_path / "wiki"
    bib_dir = root / "_bibliography"
    bib_dir.mkdir(parents=True)
    (bib_dir / "notes.txt").write_text("not a bib file", encoding="utf-8")
    monkeypatch.setattr(mod, "WIKI_ROOT", root)
    assert mod.read_bibliography("_bibliography/notes.txt") == {"error": "invalid path"}


def test_read_bibliography_oserror_on_read_reports_the_exact_message(
    monkeypatch, tmp_path
):
    root = tmp_path / "wiki"
    bib_dir = root / "_bibliography"
    bib_dir.mkdir(parents=True)
    (bib_dir / "refs.bib").write_text("@book{a}\n", encoding="utf-8")
    monkeypatch.setattr(mod, "WIKI_ROOT", root)

    def _boom(self, encoding=None, errors=None):
        raise OSError("permission denied")

    monkeypatch.setattr(Path, "read_text", _boom)
    assert mod.read_bibliography("_bibliography/refs.bib") == {
        "error": "permission denied"
    }


def test_read_bibliography_replaces_invalid_utf8_bytes_instead_of_raising(
    monkeypatch, tmp_path
):
    root = tmp_path / "wiki"
    bib_dir = root / "_bibliography"
    bib_dir.mkdir(parents=True)
    (bib_dir / "bad.bib").write_bytes(b"@book{a,\n  title={A \xff B}\n}\n")
    monkeypatch.setattr(mod, "WIKI_ROOT", root)
    got = mod.read_bibliography("_bibliography/bad.bib")
    assert "error" not in got
    assert "�" in got["content"]


def test_read_bibliography_decodes_utf8_regardless_of_process_locale(
    monkeypatch, tmp_path
):
    root = tmp_path / "wiki"
    bib_dir = root / "_bibliography"
    bib_dir.mkdir(parents=True)
    (bib_dir / "accent.bib").write_bytes("@book{a, title={café}}\n".encode())
    monkeypatch.setattr(mod, "WIKI_ROOT", root)
    with _c_locale():
        got = mod.read_bibliography("_bibliography/accent.bib")
    assert got["content"] == "@book{a, title={café}}\n"
