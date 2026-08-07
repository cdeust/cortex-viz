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
    """
    responses: dict[str, Any] = {}
    for url in request_urls(pages, bibliography):
        path, _, query = url.partition("?")
        responses[url] = respond(path, _parse_query(query))
    for variant in graph_variants or []:
        url = "/api/wiki/graph?" + "&".join(
            f"{key}={_quote(variant[key])}" for key in sorted(variant)
        )
        responses[url] = respond("/api/wiki/graph", dict(variant))
    return {
        "schema": "wiki_export.v1",
        "omitted_capabilities": list(OMITTED_CAPABILITIES),
        "page_count": len(pages),
        "responses": responses,
    }


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


__all__ = [
    "OMITTED_CAPABILITIES",
    "build_payload",
    "render_bundle",
    "request_urls",
    "serialize_payload",
]
