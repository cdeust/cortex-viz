"""Static wiki export — the exporter's own contracts (#112).

The artifact-level criteria (opens from ``file://``, no remote references, the
taxonomy, determinism, the empty wiki) are asserted against the BUILT bundle in
``tests/js/wiki_export_artifact.test.mjs``, per criterion 1's "not the sources".
What is asserted here is everything upstream of that: which URLs get captured,
how they are keyed, and whether the audit can actually see a remote reference.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cortex_viz.core import wiki_export
from cortex_viz.handlers import wiki_export_bundle as bundle

ROOT = Path(__file__).resolve().parent.parent
UI_ROOT = ROOT / "ui"


def _page(path: str, **extra):
    page = {"path": path, "title": path, "kind": "note", "domain": "d", "tags": []}
    page.update(extra)
    return page


def _respond(path, params):
    return {"path": path, "params": dict(params)}


def test_every_listed_page_contributes_its_body_and_its_metadata():
    urls = wiki_export.request_urls([_page("a.md"), _page("b/c.md")], [])

    assert "/api/wiki/page?path=a.md" in urls
    assert "/api/wiki/page_meta?path=a.md" in urls
    assert "/api/wiki/page?path=b%2Fc.md" in urls


def test_a_path_is_encoded_the_way_the_browser_encodes_it():
    """The payload is keyed by the literal URL the client builds, so the
    exporter must match ``encodeURIComponent`` — which escapes ``/`` where
    ``urllib.parse.quote`` does not, and leaves ``!'()*-._~`` alone."""
    urls = wiki_export.request_urls([_page("a b/ç!'()*-._~.md")], [])
    page_url = next(u for u in urls if u.startswith("/api/wiki/page?"))

    assert page_url == "/api/wiki/page?path=a%20b%2F%C3%A7!'()*-._~.md"


def test_the_three_index_endpoints_are_always_captured():
    urls = wiki_export.request_urls([], [])

    assert urls == [
        "/api/wiki/bibliography",
        "/api/wiki/list",
        "/api/wiki/projects",
    ]


def test_a_page_without_a_path_is_skipped_not_captured_as_an_empty_url():
    urls = wiki_export.request_urls([_page(""), _page("a.md")], [])

    assert [u for u in urls if u.startswith("/api/wiki/page?")] == [
        "/api/wiki/page?path=a.md"
    ]


def test_each_bibliography_file_contributes_a_read():
    urls = wiki_export.request_urls([], [{"path": "refs/main.bib"}])

    assert "/api/wiki/bibliography/read?path=refs%2Fmain.bib" in urls


def test_the_urls_are_deduplicated_and_sorted():
    urls = wiki_export.request_urls([_page("a.md"), _page("a.md")], [])

    assert urls == sorted(set(urls))


def test_the_payload_answers_every_url_it_captured():
    payload = wiki_export.build_payload(
        respond=_respond, pages=[_page("a.md")], bibliography=[]
    )

    assert set(payload["responses"]) == set(
        wiki_export.request_urls([_page("a.md")], [])
    )
    assert payload["responses"]["/api/wiki/page?path=a.md"]["params"] == {
        "path": "a.md"
    }
    assert payload["page_count"] == 1
    assert payload["schema"] == "wiki_export.v1"


def test_graph_variants_are_captured_under_a_sorted_query_key():
    """The client builds its graph URL in a fixed parameter order; the key has
    to match it exactly or graph mode misses the payload and reads as
    unavailable."""
    payload = wiki_export.build_payload(
        respond=_respond,
        pages=[],
        bibliography=[],
        graph_variants=[{"xlens": "1", "cooccur": "0", "domain": ""}],
    )

    body = payload["responses"]["/api/wiki/graph?cooccur=0&domain=&xlens=1"]
    assert body["path"] == "/api/wiki/graph"
    assert body["params"] == {"xlens": "1", "cooccur": "0", "domain": ""}


def test_the_payload_names_what_it_does_not_bundle():
    payload = wiki_export.build_payload(respond=_respond, pages=[], bibliography=[])

    assert payload["omitted_capabilities"] == list(wiki_export.OMITTED_CAPABILITIES)


def test_the_serialized_payload_cannot_close_its_own_script_element():
    """A page body containing ``</script>`` would end the inline script early and
    truncate the bundle — a corruption that only shows up in a browser."""
    payload = {"responses": {"/x": {"body": "</script><script>alert(1)</script>"}}}

    text = wiki_export.serialize_payload(payload)

    assert "</script" not in text
    assert "<\\/script" in text


def test_the_serialized_payload_is_stable_across_key_order():
    left = wiki_export.serialize_payload({"a": 1, "b": 2})
    right = wiki_export.serialize_payload({"b": 2, "a": 1})

    assert left == right


def test_rendering_without_the_marker_is_an_error_not_a_silent_no_op():
    with pytest.raises(
        ValueError, match=r"^export template is missing the payload marker$"
    ):
        wiki_export.render_bundle(template="<html></html>", payload={}, adapter_js="")


def test_the_payload_lands_where_the_marker_was():
    html = wiki_export.render_bundle(
        template="<b>/*__CORTEX_WIKI_PAYLOAD__*/</b>",
        payload={"page_count": 3},
        adapter_js="/*adapter*/",
    )

    assert "window.__CORTEX_WIKI_EXPORT__ =" in html
    assert '"page_count":3' in html
    assert "/*adapter*/" in html


# ── The asset layer ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("url", "expected_suffix"),
    [
        ("/js/wiki.js", "ui/unified/js/wiki.js"),
        ("/js/wiki.js?v=abc123", "ui/unified/js/wiki.js"),
        ("/css/theme.css", "ui/unified/theme.css"),
        ("/shared/ds.css", "ui/shared/ds.css"),
        ("/vendor/force-graph.min.js", "ui/unified/vendor/force-graph.min.js"),
    ],
)
def test_asset_urls_resolve_the_way_the_server_resolves_them(url, expected_suffix):
    resolved = bundle._asset_path(url, UI_ROOT)

    assert resolved is not None
    assert str(resolved).endswith(expected_suffix)


def test_an_unknown_prefix_resolves_to_nothing_rather_than_guessing():
    assert bundle._asset_path("/api/wiki/list", UI_ROOT) is None


def test_every_local_asset_the_served_page_references_actually_resolves():
    """The guard against the route table and this resolver drifting apart: if
    the server starts serving ``/js/`` from elsewhere, this fails instead of the
    bundle quietly shipping without that script."""
    html = (UI_ROOT / "unified-viz.html").read_text(encoding="utf-8")

    _inlined, missing = bundle.inline_assets(html, UI_ROOT)

    assert missing == []


def test_a_remote_tag_is_dropped_and_a_local_one_is_inlined(tmp_path):
    (tmp_path / "unified" / "js").mkdir(parents=True)
    (tmp_path / "unified" / "js" / "x.js").write_text("var x = 1;")
    html = (
        '<script src="https://cdn.example/a.js"></script>'
        '<script src="/js/x.js"></script>'
    )

    inlined, missing = bundle.inline_assets(html, tmp_path)

    assert missing == []
    assert "cdn.example" not in inlined.split("-->")[1]
    assert "dropped remote script" in inlined
    assert "var x = 1;" in inlined


def test_a_missing_local_asset_is_reported_rather_than_swallowed(tmp_path):
    (tmp_path / "unified" / "js").mkdir(parents=True)

    _inlined, missing = bundle.inline_assets(
        '<script src="/js/gone.js"></script>', tmp_path
    )

    assert missing == ["/js/gone.js"]


@pytest.mark.parametrize(
    "source",
    [
        "import('https://esm.sh/mermaid@10.9.0')",
        "var FALLBACK = 'https://unpkg.com/deck.gl@9.0.27/dist.min.js';",
        'var u = "http://example.com/x.js";',
    ],
)
def test_a_remote_url_inside_javascript_is_neutralised(source):
    """The hole the first version of this audit had: these reach the network
    through a string, not through a tag."""
    out = bundle.neutralise_remote_urls(source)

    assert "https://" not in out
    assert "http://example.com" not in out
    assert "about:blank#cortex-export-not-bundled" in out


def test_a_url_in_a_comment_is_left_alone():
    """It loads nothing, and rewriting it would corrupt a source citation."""
    source = "// source: https://example.com/paper\nvar x = 1;"

    assert bundle.neutralise_remote_urls(source) == source


def test_the_audit_sees_a_remote_url_hidden_inside_a_script_block():
    html = "<script>\nimport('https://esm.sh/mermaid@10.9.0');\n</script>"

    assert bundle.audit_remote_references(html) == ["https://esm.sh/mermaid@10.9.0"]


def test_the_audit_sees_a_remote_tag():
    html = '<link rel="stylesheet" href="https://cdn.example/a.css">'

    assert bundle.audit_remote_references(html) != []


def test_the_audit_ignores_a_link_a_reader_clicks_and_an_xml_namespace():
    html = (
        '<a href="https://github.com/cdeust/Cortex">repo</a>'
        '<script>var NS = "http://www.w3.org/2000/svg";</script>'
    )

    # The <a> loads nothing. The namespace is an identifier — but it IS a quoted
    # URL in a script, so it is reported; the artifact test filters it by name
    # rather than the audit guessing intent.
    assert bundle.audit_remote_references(html) == ["http://www.w3.org/2000/svg"]


def test_the_export_writes_one_file_and_reports_on_it(tmp_path):
    manifest = bundle.export_wiki(
        out_dir=tmp_path / "out",
        ui_root=UI_ROOT,
        respond=lambda path, params: (
            {"pages": [_page("a.md")]}
            if path == "/api/wiki/list"
            else {"files": []}
            if path == "/api/wiki/bibliography"
            else {"ok": True}
        ),
    )

    written = Path(manifest["path"])
    assert written.name == "index.html"
    assert written.is_file()
    assert manifest["bytes"] == len(written.read_text(encoding="utf-8").encode("utf-8"))
    assert manifest["page_count"] == 1
    assert manifest["missing_assets"] == []
    assert manifest["remote_references"] == []


def test_the_export_creates_its_output_directory(tmp_path):
    target = tmp_path / "deep" / "nested"

    bundle.export_wiki(
        out_dir=target,
        ui_root=UI_ROOT,
        respond=lambda path, params: (
            {"pages": []} if path == "/api/wiki/list" else {"files": []}
        ),
    )

    assert (target / "index.html").is_file()


# ── #50: every CDN script carries Subresource Integrity ────────────────
# In scope here per #112 criterion 3: the export must not reintroduce the
# pattern, and the shared source of it gets fixed in the same change.


@pytest.mark.parametrize(
    "page", ["atom-viz.html", "brain-viz.html", "methodology-viz.html"]
)
def test_every_remote_script_declares_an_integrity_hash(page):
    """A CDN script without `integrity` executes whatever the CDN returns. The
    export bundles nothing remote at all, but these three views still load
    three.js and 3d-force-graph over the network."""
    import re

    html = (UI_ROOT / page).read_text(encoding="utf-8")
    remote = re.findall(r"<script[^>]*src=\"https://[^\"]+\"[^>]*>", html)

    assert remote, f"{page} is expected to load at least one remote script"
    for tag in remote:
        assert 'integrity="sha384-' in tag, tag
        assert "crossorigin=" in tag, tag


def test_the_unified_view_loads_no_remote_script_at_all():
    """The page the wiki ships from. Its three KaTeX tags were the only remote
    references, and they are stylesheet + script; if one comes back it needs
    integrity too, and the export needs to keep dropping it."""
    import re

    html = (UI_ROOT / "unified-viz.html").read_text(encoding="utf-8")
    remote = re.findall(r"<(?:script|link)[^>]*(?:src|href)=\"https://[^\"]+\"", html)

    for tag in remote:
        assert "integrity=" in tag or "katex" in tag, tag


def test_a_url_in_page_content_is_not_mistaken_for_a_resource_load(tmp_path):
    """Found by running the exporter against the real 16k-page wiki: it reported
    27 "remote references" that were all badge images and links inside page
    bodies. Those are content a reader is meant to see, and failing the export
    on them would be a gate that cries wolf until someone disables it."""
    body = "See ![badge](https://img.shields.io/badge/x.svg) and <https://example.com>"
    manifest = bundle.export_wiki(
        out_dir=tmp_path / "out",
        ui_root=UI_ROOT,
        respond=lambda path, params: (
            {"pages": [_page("a.md")]}
            if path == "/api/wiki/list"
            else {"files": []}
            if path == "/api/wiki/bibliography"
            else {"path": "a.md", "meta": {}, "body": body}
            if path == "/api/wiki/page"
            else {"ok": True}
        ),
    )

    assert manifest["remote_references"] == []
    # And the content really is in the artifact — the audit passing must not be
    # because the URL was stripped out of the reader's page.
    assert "https://img.shields.io/badge/x.svg" in Path(manifest["path"]).read_text(
        encoding="utf-8"
    )


def test_narrowing_the_audit_did_not_make_it_blind_to_code(tmp_path):
    """The other half of the pair. The audit must still see a remote load in
    code, and inlining must be what removes it — asserted in that order, so a
    clean template cannot be mistaken for an audit that stopped looking."""
    ui = tmp_path / "ui"
    (ui / "unified" / "js").mkdir(parents=True)
    (ui / "unified" / "js" / "wiki_export_adapter.js").write_text("/*adapter*/")
    (ui / "unified" / "js" / "loader.js").write_text(
        "import('https://esm.sh/mermaid@10.9.0');"
    )
    (ui / "unified-viz.html").write_text(
        '<html><body><script src="/js/loader.js"></script></body></html>'
    )

    raw = (ui / "unified" / "js" / "loader.js").read_text()
    assert bundle.audit_remote_references(f"<script>{raw}</script>") == [
        "https://esm.sh/mermaid@10.9.0"
    ]

    template, _missing = bundle._template(ui)

    assert bundle.audit_remote_references(template) == []
    assert "esm.sh" not in template


# ── The --export command ───────────────────────────────────────────────


def _fake_manifest(**over):
    manifest = {
        "path": "/tmp/out/index.html",
        "bytes": 2048,
        "page_count": 3,
        "request_count": 9,
        "omitted_capabilities": ["mermaid diagrams", "LaTeX math"],
        "missing_assets": [],
        "remote_references": [],
    }
    manifest.update(over)
    return manifest


def _run_export(monkeypatch, manifest, tmp_path):
    from cortex_viz import __main__ as entry
    from cortex_viz.handlers import wiki_export_bundle as wb
    from cortex_viz.infrastructure import db_probe

    monkeypatch.setattr(db_probe, "open_store_or_none", lambda: None)
    monkeypatch.setattr(wb, "export_wiki", lambda **_kwargs: manifest)
    return entry._export(str(tmp_path))


def test_the_export_command_reports_what_it_wrote(monkeypatch, tmp_path, capsys):
    code = _run_export(monkeypatch, _fake_manifest(), tmp_path)

    err = capsys.readouterr().err
    assert code == 0
    assert "3 pages" in err
    assert "9 responses" in err
    # F2: the capabilities that render as source are NAMED, not left to be
    # discovered by a reader wondering why a diagram is a code block.
    assert "mermaid diagrams, LaTeX math" in err


def test_a_remote_reference_makes_the_command_fail(monkeypatch, tmp_path, capsys):
    """The one thing #112 forbids is a bundle that needs the network. Reporting
    that as success would hide exactly the failure that matters."""
    code = _run_export(
        monkeypatch, _fake_manifest(remote_references=["https://cdn.x/a.js"]), tmp_path
    )

    assert code == 1
    assert "ERROR remote references: https://cdn.x/a.js" in capsys.readouterr().err


def test_a_missing_local_asset_makes_the_command_fail(monkeypatch, tmp_path, capsys):
    code = _run_export(
        monkeypatch, _fake_manifest(missing_assets=["/js/gone.js"]), tmp_path
    )

    assert code == 1
    assert "ERROR missing local assets: /js/gone.js" in capsys.readouterr().err


def test_both_failure_kinds_are_reported_not_just_the_first(
    monkeypatch, tmp_path, capsys
):
    code = _run_export(
        monkeypatch,
        _fake_manifest(
            remote_references=["https://cdn.x/a.js"], missing_assets=["/js/g.js"]
        ),
        tmp_path,
    )

    err = capsys.readouterr().err
    assert code == 1
    assert "ERROR remote references" in err
    assert "ERROR missing local assets" in err


def test_a_template_without_a_body_is_an_error_not_a_broken_bundle(tmp_path):
    ui = tmp_path / "ui"
    (ui / "unified" / "js").mkdir(parents=True)
    (ui / "unified" / "js" / "wiki_export_adapter.js").write_text("/*a*/")
    (ui / "unified-viz.html").write_text("<html><p>no body close</p></html>")

    with pytest.raises(ValueError, match="</body>"):
        bundle._template(ui)


def test_a_value_json_cannot_encode_is_stringified_rather_than_raising():
    """`page_meta` returns datetimes straight from PostgreSQL, so the serializer
    has to coerce them. Without `default=str` the whole export dies on the first
    timestamped page — and a bundle that never gets written is a worse failure
    than a stringified date."""
    from datetime import datetime

    text = wiki_export.serialize_payload(
        {"responses": {"/x": {"updated": datetime(2026, 8, 6, 12, 0)}}}
    )

    assert "2026-08-06 12:00:00" in text


def test_the_serialized_payload_is_compact():
    """Whitespace between tokens would inflate a 66 MB artifact for nothing."""
    text = wiki_export.serialize_payload({"a": 1, "b": {"c": 2}})

    assert text == '{"a":1,"b":{"c":2}}'


def test_a_bibliography_entry_without_a_path_adds_no_url():
    """Symmetric to the pathless-page case. Found by mutation: the page guard was
    asserted and the .bib guard next to it was not."""
    urls = wiki_export.request_urls([], [{"path": ""}, {"path": "refs/main.bib"}])

    assert [u for u in urls if u.startswith("/api/wiki/bibliography/read")] == [
        "/api/wiki/bibliography/read?path=refs%2Fmain.bib"
    ]


@pytest.mark.parametrize(
    "closer",
    [
        "</script>",
        "</script >",
        "</SCRIPT\n>",
        # The spelling CodeQL named in #237: browsers ignore attributes on an
        # end tag and close the element anyway.
        "</script\t\n bar>",
    ],
)
def test_the_audit_sees_into_a_block_however_its_tag_is_closed(closer):
    """CodeQL py/bad-tag-filter (#236): HTML allows whitespace before the closing
    bracket, and the first version of this scan required `</script>` exactly — so
    a block closed any other way took its remote URLs past the gate."""
    html = f"<script>\nimport('https://esm.sh/mermaid@10.9.0');\n{closer}"

    assert bundle.audit_remote_references(html) == ["https://esm.sh/mermaid@10.9.0"]
