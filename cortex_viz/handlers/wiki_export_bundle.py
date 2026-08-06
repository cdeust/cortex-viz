"""Static wiki export — the filesystem half (#112).

Composition root: reads the served HTML and its local assets, asks the server's
own ``_dispatch_get`` for the wiki data, and writes one self-contained file.

Asset URLs are resolved through the same four prefixes
``server.http_standalone_routes`` serves (``/js/``, ``/css/``, ``/shared/``,
``/vendor/``), so a bundle cannot silently reference something the server would
have served from elsewhere. ``test_wiki_export.py`` asserts every local
reference in the HTML resolves, which is what catches the mapping drifting.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from cortex_viz.core.wiki_export import build_payload, render_bundle

# Remote references come in two shapes and only one is a tag. The tag pass
# below handles `<script src>` / `<link href>`; these two handle the rest, which
# live INSIDE the JavaScript being inlined:
#
#   * wiki.js          import('https://esm.sh/mermaid@10.9.0')
#   * wiki.js          six import('https://esm.sh/@codemirror/…') calls
#   * tilemap          DECKGL_FALLBACK / ARROW_FALLBACK unpkg URLs
#
# A first version of this audit only matched `src=`/`href=` and reported a clean
# bundle while nine live remote references sat in the inlined scripts. Matching
# quoted URL literals is what closes that: every one of those forms reaches the
# network through a string, so neutralising the string neutralises the load.
_QUOTED_REMOTE = re.compile(r"""(['"])(https?://[^'"\s]+)\1""")
_REMOTE_TAG_REF = re.compile(
    r"""<(?:script|link)\b[^>]*?(?:src|href)\s*=\s*["']https?://[^"']*["']""",
    re.IGNORECASE,
)
_SCRIPT_BLOCK = re.compile(r"<script\b[^>]*>(.*?)</script>", re.IGNORECASE | re.DOTALL)

# Replaces a remote URL literal. Not the empty string: every call site here
# feeds the value to a loader, and a scheme-less "" can resolve back against the
# document's own URL. `about:blank` cannot, fails immediately, and is legible in
# a stack trace as a deliberate substitution rather than a bug.
_NOT_BUNDLED = "about:blank#cortex-export-not-bundled"
_TAG = re.compile(
    r"""<(script|link)\b[^>]*?(?:src|href)\s*=\s*["']([^"']+)["'][^>]*?>"""
    r"""(?:\s*</script>)?""",
    re.IGNORECASE | re.DOTALL,
)
_GRAPH_VARIANTS = [
    {"cooccur": "0", "domain": "", "xlens": "1"},
    {"cooccur": "1", "domain": "", "xlens": "1"},
    {"cooccur": "0", "domain": "", "xlens": "0"},
]


def _asset_path(url: str, ui_root: Path) -> Path | None:
    """Map a served URL to its file, mirroring the route table's four prefixes."""
    clean = url.split("?")[0]
    prefixes = (
        ("/js/", ui_root / "unified" / "js"),
        ("/css/", ui_root / "unified"),
        ("/shared/", ui_root / "shared"),
        ("/vendor/", ui_root / "unified" / "vendor"),
    )
    for prefix, base in prefixes:
        if clean.startswith(prefix):
            return base / clean[len(prefix) :]
    return None


def _inline(tag: str, kind: str, url: str, ui_root: Path) -> str:
    """Replace one asset tag with its content inlined."""
    path = _asset_path(url, ui_root)
    if path is None or not path.is_file():
        # Left in place deliberately: a dropped tag would look like a working
        # bundle with a missing feature. The audit below turns a surviving
        # remote reference into a hard failure, and a missing LOCAL asset is
        # reported by the manifest the caller gets back.
        return tag
    body = path.read_text(encoding="utf-8", errors="replace")
    if kind.lower() == "link":
        return f"<style>\n{body}\n</style>"
    return f"<script>\n{neutralise_remote_urls(body)}\n</script>"


def neutralise_remote_urls(source: str) -> str:
    """Point every remote URL literal in JavaScript at a non-network scheme.

    Only quoted literals are rewritten, so a URL in a comment or in prose is
    untouched — it loads nothing. Each of the sites this hits already has a
    failure path: mermaid's import ends in ``.catch(() => null)``, the CodeMirror
    imports gate the inline editor (which a static export refuses anyway), and
    the tilemap's unpkg URLs are fallbacks behind a local ``/vendor/`` path.
    They degrade rather than break, which is why substitution is safe here and
    deleting the call sites would not be.
    """
    return _QUOTED_REMOTE.sub(
        lambda m: f"{m.group(1)}{_NOT_BUNDLED}{m.group(1)}", source
    )


def inline_assets(html: str, ui_root: Path) -> tuple[str, list[str]]:
    """Inline every local asset and drop every remote one.

    Returns the rewritten HTML and the sorted list of local URLs that could not
    be resolved, so the caller can report them instead of shipping a bundle
    whose gaps are invisible.
    """
    missing: list[str] = []

    def replace(match: re.Match) -> str:
        tag, kind, url = match.group(0), match.group(1), match.group(2)
        if url.startswith(("http://", "https://", "//")):
            return f"<!-- cortex-export: dropped remote {kind} {url} -->"
        path = _asset_path(url, ui_root)
        if path is not None and not path.is_file():
            missing.append(url)
        return _inline(tag, kind, url, ui_root)

    return _TAG.sub(replace, html), sorted(set(missing))


def audit_remote_references(html: str) -> list[str]:
    """Every surviving remote resource load. Empty is the requirement.

    Checks both shapes: a remote ``src``/``href`` on a tag, and a remote URL
    quoted inside any ``<script>`` block — which is how `import()`, `fetch()`
    and script-injecting loaders reach the network.

    Deliberately NOT flagged, because neither loads anything: a URL in a comment,
    and a link a reader clicks (``<a href>``). An audit that failed on those
    would be argued around rather than fixed, which is worse than one that is
    narrow and honest about what it covers.
    """
    found = {match.group(0) for match in _REMOTE_TAG_REF.finditer(html)}
    for block in _SCRIPT_BLOCK.finditer(html):
        found.update(
            match.group(2) for match in _QUOTED_REMOTE.finditer(block.group(1))
        )
    return sorted(found)


def _template(ui_root: Path) -> tuple[str, list[str]]:
    """The served page, with assets inlined and a marker for the payload."""
    html = (ui_root / "unified-viz.html").read_text(encoding="utf-8")
    inlined, missing = inline_assets(html, ui_root)
    adapter_tag = (
        "<script>/*__CORTEX_WIKI_PAYLOAD__*/</script>\n"
        f"<script>\n{_adapter_source(ui_root)}\n</script>\n"
    )
    # Before </body>: the adapter only has to exist before the first REQUEST,
    # and wiki.js reads the port per call rather than at load (see wikiFetch).
    if "</body>" not in inlined:
        raise ValueError("unified-viz.html has no </body> to anchor the export on")
    return inlined.replace("</body>", adapter_tag + "</body>"), missing


def _adapter_source(ui_root: Path) -> str:
    return (ui_root / "unified" / "js" / "wiki_export_adapter.js").read_text(
        encoding="utf-8"
    )


def export_wiki(*, out_dir: Path, ui_root: Path, respond) -> dict[str, Any]:
    """Write ``<out_dir>/index.html`` and return a manifest.

    Args:
        out_dir: created if absent.
        ui_root: the ``ui/`` directory whose assets the server serves.
        respond: ``(path, params) -> body``, normally
            ``http_standalone_wiki._dispatch_get`` bound to a store.

    Returns a manifest with the byte size, the page count, any unresolved local
    assets, and the remote references found in the artifact — which must be
    empty, and which the caller reports rather than discovering later.

    The audit runs on the TEMPLATE, not the finished file: the template holds
    every line of code the bundle will execute, while the payload it is missing
    is inert JSON. Auditing the finished file instead conflated the two — on the
    real wiki it reported 27 "remote references" that were all badge images and
    links inside page bodies, i.e. content a reader is meant to see, and it would
    have failed a perfectly good export on them.
    """
    listing = respond("/api/wiki/list", {})
    pages = sorted(
        (listing or {}).get("pages") or [], key=lambda page: str(page.get("path") or "")
    )
    bib = (respond("/api/wiki/bibliography", {}) or {}).get("files") or []
    payload = build_payload(
        respond=respond,
        pages=pages,
        bibliography=sorted(bib, key=lambda item: str(item.get("path") or "")),
        graph_variants=_GRAPH_VARIANTS,
    )
    template, missing = _template(ui_root)
    html = render_bundle(
        template=template, payload=payload, adapter_js=""
    )  # adapter ships as its own tag
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / "index.html"
    target.write_text(html, encoding="utf-8")
    return {
        "path": str(target),
        "bytes": len(html.encode("utf-8")),
        "page_count": payload["page_count"],
        "request_count": len(payload["responses"]),
        "omitted_capabilities": payload["omitted_capabilities"],
        "missing_assets": missing,
        "remote_references": audit_remote_references(template),
    }


__all__ = [
    "audit_remote_references",
    "export_wiki",
    "inline_assets",
]
