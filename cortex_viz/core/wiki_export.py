"""Static wiki export — the pure half (#112).

Produces a single HTML file that opens from ``file://`` with no server and no
network. Two rules shape everything here:

1. **The bundle renders through the wiki view's own code.** The page it emits
   loads the same ``ui/`` assets the server serves and installs an adapter on
   the transport port ``wiki.js`` already reads (``JUG._wikiTransport``), so the
   maturity badges and the 10-kind taxonomy cannot drift from the served view —
   there is no second renderer to keep in step.
2. **The payload is keyed by request URL.** The exporter asks the server's own
   ``_dispatch_get`` for each URL the view will request and stores the answers
   verbatim, so exported content is the served content by construction rather
   than by a parallel query someone has to keep aligned.

Deterministic by construction: every collection is sorted and the JSON is
emitted with sorted keys and no timestamp, so the same wiki yields a
byte-identical bundle (criterion 5).

No I/O: callers pass in a ``respond`` callable and the asset text. The
filesystem half lives in ``handlers.wiki_export_bundle``.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any

# The capabilities the served view lazy-loads from a CDN and the bundle
# deliberately does not carry: mermaid (~1 MB ESM) and KaTeX (~60 font files).
# Both already degrade to source text when absent — mermaid's import has a
# ``.catch(() => null)`` and the KaTeX pass is behind ``if
# (window.renderMathInElement)`` — but they degrade SILENTLY, which §F2
# forbids. The bundle states the trade instead of letting a reader wonder why a
# diagram is a code block.
OMITTED_CAPABILITIES = ("mermaid diagrams", "LaTeX math")

_PAYLOAD_MARKER = "/*__CORTEX_WIKI_PAYLOAD__*/"


def request_urls(pages: list[dict], bibliography: list[dict]) -> list[str]:
    """Every URL the wiki view will request, sorted.

    Derived from the index responses rather than guessed: the per-page and
    per-``.bib`` reads exist only because a page or file is listed.
    """
    urls = ["/api/wiki/list", "/api/wiki/projects", "/api/wiki/bibliography"]
    for page in pages:
        path = str(page.get("path") or "")
        if not path:
            continue
        quoted = _quote(path)
        urls.append(f"/api/wiki/page?path={quoted}")
        urls.append(f"/api/wiki/page_meta?path={quoted}")
    for entry in bibliography:
        path = str(entry.get("path") or "")
        if path:
            urls.append(f"/api/wiki/bibliography/read?path={_quote(path)}")
    return sorted(set(urls))


def _quote(value: str) -> str:
    """Percent-encode exactly as the browser's ``encodeURIComponent`` does.

    ``urllib.parse.quote`` keeps ``/`` unescaped by default, which
    ``encodeURIComponent`` does not — and the payload is keyed by the literal
    URL the client builds, so a single character of disagreement is a miss.
    """
    from urllib.parse import quote

    return quote(value, safe="!'()*-._~")


def build_payload(
    *,
    respond: Callable[[str, dict[str, str]], Any],
    pages: list[dict],
    bibliography: list[dict],
    graph_variants: list[dict[str, str]] | None = None,
    overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Answer every URL the view will request and return the keyed payload.

    Args:
        respond: ``(path_without_query, params) -> response body``. The server's
            own ``_dispatch_get`` is what callers pass, which is what makes the
            bundle's content identical to the served content.
        pages: the ``pages`` list from ``/api/wiki/list``.
        bibliography: the ``files`` list from ``/api/wiki/bibliography``.
        graph_variants: query-parameter sets for ``/api/wiki/graph`` to
            pre-render, so graph mode works offline for the toggle combinations
            a reader can actually reach.
        overrides: responses to use INSTEAD of asking ``respond``. A per-domain
            bundle needs this for ``/api/wiki/list``: the server answers with
            every page, but a scoped bundle only carries its own domain's
            bodies, so an unscoped index would list pages whose content is
            absent and the tree would fill with "unavailable" entries.
    """
    fixed = dict(overrides or {})
    responses: dict[str, Any] = {}
    for url in request_urls(pages, bibliography):
        if url in fixed:
            responses[url] = fixed[url]
            continue
        path, _, query = url.partition("?")
        responses[url] = respond(path, _parse_query(query))
    for variant in graph_variants or []:
        url = "/api/wiki/graph?" + "&".join(
            f"{key}={_quote(variant[key])}" for key in sorted(variant)
        )
        responses[url] = (
            fixed[url] if url in fixed else respond("/api/wiki/graph", dict(variant))
        )
    return {
        "schema": "wiki_export.v1",
        "omitted_capabilities": list(OMITTED_CAPABILITIES),
        "page_count": len(pages),
        "responses": responses,
    }


# Pages carrying no domain still have to land somewhere. Dropping them would
# lose 1064 of this wiki's 16254 pages without saying so.
UNASSIGNED_DOMAIN_LABEL = "unassigned"

# Everything a filename may not safely carry. Compiled once at module level —
# a lazily-initialised global would be mutable state for no gain (§7.2).
_SLUG_UNSAFE = re.compile(r"[^a-z0-9._-]+")


def group_by_domain(pages: list[dict]) -> list[tuple[str, list[dict]]]:
    """``[(domain, pages)]``, domain-sorted, pages path-sorted within each.

    A page with no domain is grouped under ``UNASSIGNED_DOMAIN_LABEL`` rather
    than discarded — an export that silently omits a sixth of the wiki is worse
    than one with an awkward group name.
    """
    grouped: dict[str, list[dict]] = {}
    for page in pages:
        label = str(page.get("domain") or "").strip() or UNASSIGNED_DOMAIN_LABEL
        grouped.setdefault(label, []).append(page)
    return [
        (domain, sorted(items, key=lambda page: str(page.get("path") or "")))
        for domain, items in sorted(grouped.items())
    ]


def domain_filenames(domains: list[str]) -> dict[str, str]:
    """Map each domain to a distinct, filesystem-safe ``.html`` filename.

    Two domains can slug to the same name (``a/b`` and ``a-b``), and one
    silently overwriting the other would delete a whole domain's pages from the
    export. Collisions are therefore resolved by appending an index, assigned in
    sorted order so the mapping is the same on every run.
    """
    used: set[str] = set()
    mapping: dict[str, str] = {}
    for domain in sorted(domains):
        base = _SLUG_UNSAFE.sub("-", domain.lower()).strip("-.") or "domain"
        candidate, suffix = base, 2
        while candidate in used:
            candidate = f"{base}-{suffix}"
            suffix += 1
        used.add(candidate)
        mapping[domain] = f"{candidate}.html"
    return mapping


def _parse_query(query: str) -> dict[str, str]:
    from urllib.parse import parse_qs

    return {key: values[0] for key, values in parse_qs(query).items()}


def serialize_payload(payload: dict[str, Any]) -> str:
    """JSON for embedding, with sorted keys and no timestamp.

    ``</`` is split so the literal ``</script>`` can never appear inside the
    inline script that carries this, which would end the element early. This is
    the standard escape and it survives ``JSON.parse`` unchanged.
    """
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return text.replace("</", "<\\/")


def render_bundle(*, template: str, payload: dict[str, Any], adapter_js: str) -> str:
    """Return the finished single-file bundle.

    ``template`` is the served HTML with its local assets already inlined and
    its remote references removed (``handlers.wiki_export_bundle`` does that);
    this function only injects the payload and the adapter, at the marker.
    """
    if _PAYLOAD_MARKER not in template:
        raise ValueError("export template is missing the payload marker")
    injected = (
        f"window.__CORTEX_WIKI_EXPORT__ = {serialize_payload(payload)};\n{adapter_js}"
    )
    return template.replace(_PAYLOAD_MARKER, injected)


_INDEX_CSS = """
:root { color-scheme: light dark; }
body { font: 15px/1.55 ui-sans-serif, system-ui, sans-serif; margin: 0;
       padding: 3rem 1.5rem; display: flex; justify-content: center; }
main { width: min(46rem, 100%); }
h1 { font-size: 1.35rem; margin: 0 0 .35rem; }
p.sub { margin: 0 0 2rem; opacity: .7; }
ul { list-style: none; margin: 0; padding: 0; }
li { border-top: 1px solid rgba(128,128,128,.28); }
li:last-child { border-bottom: 1px solid rgba(128,128,128,.28); }
a { display: flex; justify-content: space-between; gap: 1rem; padding: .7rem .2rem;
    text-decoration: none; color: inherit; }
a:hover { background: rgba(128,128,128,.1); }
.count { opacity: .6; font-variant-numeric: tabular-nums; }
footer { margin-top: 2rem; font-size: .85rem; opacity: .65; }
"""


def _escape(text: str) -> str:
    """HTML-escape. Domain names come from page frontmatter — untrusted input."""
    from html import escape

    return escape(str(text), quote=True)


def render_domain_index(
    entries: list[tuple[str, str, int]], *, omitted: list[str] | None = None
) -> str:
    """The chooser page for a per-domain export.

    ``entries`` is ``[(domain, filename, page_count)]``. Written out plainly with
    inline CSS and no script at all: it is a list of links, and the one thing it
    must never do is need the network to render.
    """
    rows = "\n".join(
        f'      <li><a href="{_escape(filename)}">'
        f"<span>{_escape(domain)}</span>"
        f'<span class="count">{count} pages</span></a></li>'
        for domain, filename, count in entries
    )
    total = sum(count for _domain, _filename, count in entries)
    note = (
        f"      <footer>Renders as source (not bundled): {_escape(', '.join(omitted))}."
        "</footer>\n"
        if omitted
        else ""
    )
    return (
        "<!doctype html>\n"
        '<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        "<title>Wiki export</title>\n"
        f"<style>{_INDEX_CSS}</style>\n"
        "</head>\n<body>\n  <main>\n"
        "    <h1>Wiki export</h1>\n"
        f'    <p class="sub">{len(entries)} domains, {total} pages. '
        "Each link opens a self-contained file; no server required.</p>\n"
        f"    <ul>\n{rows}\n    </ul>\n{note}"
        "  </main>\n</body>\n</html>\n"
    )


__all__ = [
    "OMITTED_CAPABILITIES",
    "UNASSIGNED_DOMAIN_LABEL",
    "build_payload",
    "domain_filenames",
    "group_by_domain",
    "render_bundle",
    "render_domain_index",
    "request_urls",
    "serialize_payload",
]
