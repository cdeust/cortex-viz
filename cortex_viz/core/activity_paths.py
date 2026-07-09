"""Path canonicalization for the live session-activity spine (P4 fix).

Root cause this module addresses: the live activity spine
(``core.activity_graph``) minted ``file:<raw literal path>`` node ids while
the galaxy workflow graph mints ``file:<sha256(abs_path)[:10]>``
(``NodeIdFactory.file_id`` in ``core.workflow_graph_schema``). Two disjoint
id spaces for the same file meant the live spine's FILE nodes could never
dedup-merge with the galaxy's FILE nodes on the client
(``appendGraphDelta`` dedups by id string equality —
``ui/unified/js/activity_stream.js`` documents that the live spine is meant
to merge into the SAME galaxy graph).

Fix: mint the SAME id. ``file_target_id`` canonicalizes the raw path (``~``
expansion, ``cwd``-relative resolution, ``.``/``..`` collapse — pure string
ops, no filesystem I/O, no symlink resolution) and hashes it through
``NodeIdFactory.file_id``, exactly like the galaxy's ingest path
(``workflow_graph_builder_ingest._ingest_tool_event`` hashes
``ev["file_path"]`` verbatim). For the common case — Read/Edit/Write tool
calls, whose ``file_path`` argument Claude Code's own tool contract requires
to already be absolute — canonicalization is a no-op on an already-clean
path, so the hash equals what the galaxy would mint for the identical tool
event. This is the SAME best-effort precision limit already documented in
``core.wiki_source_resolve`` (a symlink, a different casing, or a stray
trailing slash on either side of the join still won't hash-equal) — not a
new limitation introduced here.

Historical rows in ``session_activity`` written before this fix carry the
OLD ``file:<raw path>`` scheme. Nothing here mutates those rows (the table
is an append-only durable log per ``infrastructure.activity_store``) —
instead, ``canonical_file_id_for_legacy`` recomputes the same canonical id
from the raw path a legacy row still embeds after its ``"file:"`` prefix,
so callers (SSE replay, ``core.wiki_page_actions``) can self-heal old rows
at read time without a migration. See the decision recorded in the
Task-A change report (cortex-viz feat/activity-path-unification).

Pure: stdlib ``posixpath`` only (``expanduser`` reads ``$HOME``/``$USER``
from the environment, not the filesystem — the same "accepted" boundary
``wiki_coverage._project_source_root`` documents for git-root discovery).
No filesystem I/O.
"""

from __future__ import annotations

import posixpath
import re

from cortex_viz.core.workflow_graph_schema import NodeIdFactory

# NodeIdFactory.file_id hashes to a 10-char lowercase hex digest
# (``hashlib.sha256(...).hexdigest()[:10]``, see ``_short_hash``'s default
# width in ``workflow_graph_schema.py``). A legacy raw-path target_id always
# contains a path separator or a "~"; a post-unification hash never does —
# the two shapes are disjoint by construction, so this regex reliably tells
# them apart without a schema-version column.
_CANONICAL_HASH_RE = re.compile(r"^[0-9a-f]{10}$")


def canonicalize_path(raw_path: str, cwd: str | None) -> str:
    """Raw path (possibly ``~``-prefixed or ``cwd``-relative) -> absolute
    path string. Pure string manipulation: ``~``/``~user`` expansion via
    ``$HOME``, join against ``cwd`` when not already absolute, then
    ``normpath`` to collapse ``.``/``..``/redundant slashes. Never touches
    the filesystem (no symlink resolution — see module docstring).

    An already-clean absolute path (the common case — Claude Code's own
    tool contract requires ``file_path`` arguments to Read/Edit/Write to be
    absolute) round-trips unchanged, so the hash minted from it equals the
    galaxy's hash for the identical tool event.
    """
    p = posixpath.expanduser(raw_path)
    if not posixpath.isabs(p) and cwd:
        p = posixpath.join(cwd, p)
    return posixpath.normpath(p)


def file_target_id(raw_path: str, cwd: str | None) -> str:
    """``file:<hash>`` id for a raw path — the SAME id the galaxy workflow
    graph would mint for the identical file via ``NodeIdFactory.file_id``.
    This is the join key that lets the live activity spine's FILE nodes
    dedup-merge with the galaxy's FILE nodes on the client.
    """
    return NodeIdFactory.file_id(canonicalize_path(raw_path, cwd))


def is_canonical_file_target_id(target_id: str | None) -> bool:
    """True when ``target_id`` already carries the post-unification
    ``file:<10-hex-hash>`` shape (as opposed to a pre-unification
    ``file:<raw literal path>`` legacy row)."""
    if not target_id or not target_id.startswith("file:"):
        return False
    return bool(_CANONICAL_HASH_RE.match(target_id[len("file:") :]))


def canonical_file_id_for_legacy(target_id: str, cwd: str | None) -> str:
    """Recompute the canonical ``file:<hash>`` id for a LEGACY
    ``session_activity`` row whose ``target_id`` still embeds the raw path
    (``file:<raw path>``, written before this fix shipped). Read-time
    self-healing — no DB migration, no rewrite of the append-only log.

    Callers MUST check ``target_id`` is non-canonical first (or simply
    apply this unconditionally to rows known to be pre-unification); passing
    an already-canonical id here would hash the literal 10-char digest
    string, which is harmless (it just produces a non-matching id) but not
    meaningful — always gate with ``is_canonical_file_target_id`` first.
    """
    raw_path = target_id[len("file:") :]
    return file_target_id(raw_path, cwd)


__all__ = [
    "canonicalize_path",
    "file_target_id",
    "is_canonical_file_target_id",
    "canonical_file_id_for_legacy",
]
