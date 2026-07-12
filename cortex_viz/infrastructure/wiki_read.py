"""Filesystem reader for the wiki view (``~/.claude/methodology/wiki``).

Serves the page tree, single pages, project grouping, and bibliography files
straight off disk — the hand-curated half of the wiki. The PG-backed half
(thermodynamic page state, backlinks, memos) lives in ``wiki_pg``.

Pure I/O over ``WIKI_ROOT``. Response shapes match the parent Cortex viz
server so the extracted ``wiki.js`` consumes them unchanged.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from cortex_viz.infrastructure.config import WIKI_ROOT
from cortex_viz.shared.yaml_parser import parse_yaml_frontmatter


def _safe_path(rel_path: str, *, suffix: str | None = None) -> Path | None:
    """Resolve ``rel_path`` under WIKI_ROOT, defeating path traversal.

    Uses realpath + commonpath (not pathlib.is_relative_to, which CodeQL does
    not recognise as a containment check — see codeql_pathinj_weakhash_fix).
    Returns None if the resolved path escapes the wiki root or fails an
    optional suffix gate.
    """
    if not rel_path:
        return None
    root = os.path.realpath(str(WIKI_ROOT))
    cand = os.path.realpath(os.path.join(root, rel_path))
    if os.path.commonpath([root, cand]) != root:
        return None
    if suffix is not None and not cand.endswith(suffix):
        return None
    return Path(cand)


def _parse_list(value: Any) -> list[str]:
    """Frontmatter list value (``[a, b]`` / ``a, b`` / already a list) → list."""
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    if not value:
        return []
    s = str(value).strip().strip("[]")
    return [tok.strip().strip("'\"") for tok in s.split(",") if tok.strip()]


# Frontmatter keys whose value is a list, not a scalar string. The parser
# (yaml_parser.parse_yaml_frontmatter) has a flat-KV contract — every value
# comes back as a string — so these keys need explicit normalisation via
# _parse_list before reaching the client. Measured against the live wiki
# corpus (2026-07): tags 2086 pages, audience 32, required_sections 5,
# optional_sections 5, source_memory_ids 1. curation_gaps is included
# because ui/unified/js/wiki.js reads it with Array.isArray(meta.curation_gaps).
_LIST_KEYS = (
    "tags",
    "curation_gaps",
    "audience",
    "required_sections",
    "optional_sections",
    "source_memory_ids",
)


def _title_from(meta: dict, path: Path) -> str:
    return (
        meta.get("title")
        or path.stem.replace("-", " ").replace("_", " ").strip()
        or path.name
    )


def _page_item(md: Path, root: Path) -> dict[str, Any]:
    """Lightweight tree item for one .md file (frontmatter only, no body)."""
    rel = str(md.relative_to(root))
    try:
        meta, _ = parse_yaml_frontmatter(
            md.read_text(encoding="utf-8", errors="replace")
        )
    except OSError:
        meta = {}
    return {
        "path": rel,
        "title": _title_from(meta, md),
        "kind": meta.get("kind") or "page",
        "domain": meta.get("domain") or "",
        "tags": _parse_list(meta.get("tags")),
        "maturity": meta.get("maturity") or meta.get("status") or "",
        "created": meta.get("created") or meta.get("date") or "",
        "updated": meta.get("updated") or meta.get("tended") or "",
    }


def _iter_md(root: Path):
    """All .md files under the wiki root except the bibliography subtree."""
    for md in sorted(root.rglob("*.md")):
        if "_bibliography" in md.parts:
            continue
        yield md


def list_pages() -> dict[str, Any]:
    """``{pages: [...]}`` — the whole wiki tree (frontmatter only)."""
    root = Path(os.path.realpath(str(WIKI_ROOT)))
    if not root.is_dir():
        return {"pages": []}
    return {"pages": [_page_item(md, root) for md in _iter_md(root)]}


def read_page(rel_path: str) -> dict[str, Any]:
    """``{path, meta, body}`` for one page, or ``{error}`` if missing/unsafe."""
    p = _safe_path(rel_path, suffix=".md")
    if p is None:
        return {"error": "invalid path"}
    if not p.is_file():
        return {"error": "not found"}
    try:
        meta, body = parse_yaml_frontmatter(
            p.read_text(encoding="utf-8", errors="replace")
        )
    except OSError as e:
        return {"error": str(e)}
    # meta values are always strings per the parser's flat-KV contract (see
    # _LIST_KEYS docstring) — normalise the keys the client expects as
    # arrays, same as _page_item does for the list endpoint. Only keys
    # actually present are touched: injecting an absent key would flip a
    # client-side falsy check (e.g. `meta.curation_gaps &&`) to truthy.
    for key in _LIST_KEYS:
        if key in meta:
            meta[key] = _parse_list(meta[key])
    return {"path": rel_path, "meta": meta, "body": body}


def list_projects() -> dict[str, Any]:
    """``{projects: [...]}`` — pages grouped by domain with per-kind counts."""
    root = Path(os.path.realpath(str(WIKI_ROOT)))
    if not root.is_dir():
        return {"projects": []}
    by_domain: dict[str, dict[str, Any]] = {}
    for md in _iter_md(root):
        item = _page_item(md, root)
        dom = item["domain"] or "_general"
        proj = by_domain.setdefault(
            dom, {"domain": dom, "page_total": 0, "page_counts_by_kind": {}}
        )
        proj["page_total"] += 1
        kind = item["kind"]
        proj["page_counts_by_kind"][kind] = proj["page_counts_by_kind"].get(kind, 0) + 1
    projects = sorted(by_domain.values(), key=lambda p: -p["page_total"])
    return {"projects": projects}


def list_bibliography() -> dict[str, Any]:
    """``{files: [...]}`` — .bib files under ``_bibliography/`` with entry counts."""
    root = Path(os.path.realpath(str(WIKI_ROOT)))
    bib_dir = root / "_bibliography"
    if not bib_dir.is_dir():
        return {"files": []}
    files: list[dict[str, Any]] = []
    for bib in sorted(bib_dir.rglob("*.bib")):
        try:
            text = bib.read_text(encoding="utf-8", errors="replace")
            entries = text.count("\n@") + (1 if text.lstrip().startswith("@") else 0)
            files.append(
                {
                    "path": str(bib.relative_to(root)),
                    "size": bib.stat().st_size,
                    "entries": entries,
                }
            )
        except OSError:
            continue
    return {"files": files}


def read_bibliography(rel_path: str) -> dict[str, Any]:
    """``{path, content, size}`` for one .bib file (must be under _bibliography)."""
    p = _safe_path(rel_path, suffix=".bib")
    if p is None or "_bibliography" not in p.parts or not p.is_file():
        return {"error": "invalid path"}
    try:
        content = p.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return {"error": str(e)}
    return {"path": rel_path, "content": content, "size": len(content)}


def save_page(rel_path: str, content: str) -> dict[str, Any]:
    """Overwrite one wiki .md page with ``content`` (full markdown source).

    Path-contained to WIKI_ROOT and gated to ``.md`` (same guard as reads), so
    a malicious ``rel_path`` can't escape the wiki tree. Creates parent dirs
    for a brand-new page. User-initiated (the editor's Save button); returns
    ``{ok, path}`` or ``{error}``.
    """
    p = _safe_path(rel_path, suffix=".md")
    if p is None:
        return {"error": "invalid path"}
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    except OSError as e:
        return {"error": str(e)}
    return {"ok": True, "path": rel_path, "bytes": len(content.encode("utf-8"))}


__all__ = [
    "list_pages",
    "read_page",
    "list_projects",
    "list_bibliography",
    "read_bibliography",
    "save_page",
]
