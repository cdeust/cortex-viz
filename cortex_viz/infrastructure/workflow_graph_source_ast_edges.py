"""Per-graph async edge-batch loader for the AST workflow-graph source.

Split out of ``workflow_graph_source_ast.py`` (was 917 lines) to respect
the 500-line file limit. Holds ``_edge_batches_async`` — the async
generator that queries AP's typed rel tables (Calls_*, Imports_*,
HasMethod_*, Uses_*, Defines_File_Import) and yields one batch per
rel-table query. Pure infrastructure — no core imports.

``workflow_graph_source_ast`` re-exports this so existing import paths
keep resolving.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from cortex_viz.infrastructure.workflow_graph_source_ast_async import (
    _NON_QUALIFIED_LABELS,
    _as_list,
)

if TYPE_CHECKING:
    from cortex_viz.infrastructure.ap_bridge import APBridge


# The exact rel tables AP's schema creates, transcribed from
# ``automatised-pipeline`` ``src/graph_store.rs`` ``REL_TABLES`` — the
# authoritative, static list of every relationship table AP's LadybugDB graph
# can contain. Each entry is (semantic_kind, table, src_label, dst_label,
# has_provenance).
#
# Why an explicit list instead of a Cartesian product over label kinds:
#   * AP's query_graph guard FORBIDS the ``CALL`` keyword
#     (automatised-pipeline src/main.rs:FORBIDDEN_CYPHER_KEYWORDS), so the
#     engine's catalog (``CALL show_tables()``) is unreachable — we cannot
#     discover tables at runtime and must mirror the schema statically.
#   * The prior code enumerated the full Cartesian product of label kinds
#     (~89 queries). Only 24 of those pairs correspond to a table that AP's
#     schema actually creates; the other 65 were queries against tables that
#     can NEVER exist (e.g. ``HasMethod_Class_Field``, ``Uses_Method_Interface``,
#     ``Calls_Macro_Macro`` — AP has no Class/Interface/Protocol node labels and
#     no Macro-call table). AP returns empty for a missing table, so the extra
#     65 round-trips were pure latency on the cold-L6 path, not a data change.
#   * This list is EXACTLY the 24 pairs the prior enumeration returned rows for,
#     so trimming to it is behaviour-preserving for loaded edges while cutting
#     the query count by ~73%.
#
# ``has_provenance`` gates fetching ``r.confidence`` + ``r.resolution_method``.
# In AP both is_resolution_rel (Calls/Imports/Uses/References/Implements/
# Extends) AND is_structural_provenance_rel (Defines/HasMethod/HasField/
# HasVariant) carry those columns (graph_store.rs rel_table_ddl), so requesting
# them never binds against a missing column. We keep HasMethod_*/Defines_* at
# False (confidence defaults to 1.0 for ground-truth AST facts) to preserve the
# prior behaviour exactly.
#
# NOTE (follow-up, tracked in the galaxy-lag audit): AP's schema also defines
# ~29 more real edge tables the viz does not yet load — Imports_File_File,
# References_File_File, HasField_*, HasVariant_*, Defines_File_<symbol>,
# Imports_Module_*, Uses_Struct_*/Uses_Field_*, Calls_*_StdlibSymbol,
# Calls_CallSite_*. Adding them widens the graph but each has an endpoint/column
# subtlety (File/Module carry ``id`` not ``qualified_name``; Variant/StdlibSymbol
# aren't in _SYMBOL_LABELS yet), so they need their own load-path handling
# rather than being folded into this trim. source: galaxy-lag audit Finding E.
_AP_REL_TABLES: tuple[tuple[str, str, str, str, bool], ...] = (
    # Calls — Function/Method call edges (resolution rels: provenance).
    ("calls", "Calls_Function_Function", "Function", "Function", True),
    ("calls", "Calls_Function_Method", "Function", "Method", True),
    ("calls", "Calls_Method_Function", "Method", "Function", True),
    ("calls", "Calls_Method_Method", "Method", "Method", True),
    # Imports — File → imported in-graph symbol (resolution rels: provenance).
    ("imports", "Imports_File_Function", "File", "Function", True),
    ("imports", "Imports_File_Method", "File", "Method", True),
    ("imports", "Imports_File_Struct", "File", "Struct", True),
    ("imports", "Imports_File_Enum", "File", "Enum", True),
    ("imports", "Imports_File_Trait", "File", "Trait", True),
    ("imports", "Imports_File_Constant", "File", "Constant", True),
    ("imports", "Imports_File_TypeAlias", "File", "TypeAlias", True),
    ("imports", "Imports_File_Module", "File", "Module", True),
    # HasMethod — container → method (structural: confidence defaults to 1.0).
    ("member_of", "HasMethod_Struct_Method", "Struct", "Method", False),
    ("member_of", "HasMethod_Enum_Method", "Enum", "Method", False),
    ("member_of", "HasMethod_Trait_Method", "Trait", "Method", False),
    # Defines_File_Import — File → Import node. AP wires every ``import``
    # statement to its file here; counts in the tens of thousands per project.
    # This is the edge set that lifted the viz from ~5k to ~36k imports.
    ("imports", "Defines_File_Import", "File", "Import", False),
    # Uses — Function/Method uses a type (resolution rels: provenance).
    ("uses", "Uses_Function_Struct", "Function", "Struct", True),
    ("uses", "Uses_Function_Enum", "Function", "Enum", True),
    ("uses", "Uses_Function_Trait", "Function", "Trait", True),
    ("uses", "Uses_Function_TypeAlias", "Function", "TypeAlias", True),
    ("uses", "Uses_Method_Struct", "Method", "Struct", True),
    ("uses", "Uses_Method_Enum", "Method", "Enum", True),
    ("uses", "Uses_Method_Trait", "Method", "Trait", True),
    ("uses", "Uses_Method_TypeAlias", "Method", "TypeAlias", True),
)


async def _edge_batches_async(
    bridge: "APBridge",
    graph_path: str,
    paths: list[str],
):
    """Yield one batch of edge rows per AP rel-table query (async gen).

    AP uses per-label-pair typed rel tables (LadybugDB convention):
      * Calls_<Src>_<Dst>   for Function↔Method call edges
      * Imports_File_<Lbl>  for File → imported symbol
      * HasMethod_<Parent>_Method for struct/enum/trait → method

    We query exactly the rel tables AP's schema defines (``_AP_REL_TABLES``,
    transcribed from AP's ``REL_TABLES``) and collapse them to the semantic
    kinds the builder understands. Each rel-table's rows are yielded as its own
    batch the moment its query returns, so peak rows retained inside the source
    is one rel-table's result — not the union across all the queries.
    """
    # Same path-matching strategy as ``_symbol_batches_async``.
    path_tails: set[str] = set()
    for p in paths:
        if not p:
            continue
        path_tails.add(p)
        parts = p.split("/")
        for i in range(1, len(parts)):
            path_tails.add("/".join(parts[i:]))

    def _match(file_part: str) -> bool:
        if not path_tails:
            return True
        return any(
            p == file_part or p.endswith(file_part) or file_part.endswith(p)
            for p in path_tails
        )

    async def _run_edge(
        kind: str,
        table: str,
        src_lbl: str,
        dst_lbl: str,
        has_provenance: bool,
    ) -> list[dict[str, Any]]:
        """Query AP for edges of ``kind`` in ``table`` and RETURN this
        rel-table's rows as one batch (the caller yields it, then drops
        it before the next rel-table query — bounding peak to one batch).

        ``has_provenance`` gates whether to fetch ``r.confidence`` +
        ``r.resolution_method``: Kuzu raises a Binder exception on
        missing-property access, so we only request those columns
        for rel tables the AP resolver actually annotates (Calls_*
        / Imports_* / Implements_* / Extends_* / Uses_*). Structural
        tables (HasMethod_* / Defines_*) have no such columns —
        callers default confidence to 1.0 for those kinds instead.
        """
        if src_lbl == "File":
            select_src = "src.id AS src_name"
        elif src_lbl in _NON_QUALIFIED_LABELS:
            select_src = "src.id AS src_name"
        else:
            select_src = "src.qualified_name AS src_name"
        # Import nodes (and any other ``_NON_QUALIFIED_LABELS`` kind)
        # carry ``id`` instead of ``qualified_name``. Selecting the
        # missing property would raise a Kuzu Binder exception.
        dst_qn = (
            "dst.id AS dst_name"
            if dst_lbl in _NON_QUALIFIED_LABELS
            else "dst.qualified_name AS dst_name"
        )
        if has_provenance:
            return_tail = (
                f"       {dst_qn}, "
                "       r.confidence       AS confidence, "
                "       r.resolution_method AS reason"
            )
        else:
            return_tail = f"       {dst_qn}"
        query = (
            f"MATCH (src:{src_lbl})-[r:{table}]->(dst:{dst_lbl}) "
            f"RETURN {select_src}, {return_tail}"
        )
        rows = await bridge.call(
            "query_graph",
            {"graph_path": graph_path, "query": query},
        )
        batch: list[dict[str, Any]] = []
        for r in _as_list(rows):
            src = str(r.get("src_name") or "")
            dst = str(r.get("dst_name") or "")
            if not dst:
                continue
            # src may be a File.id (relative path) or a symbol qn.
            if src_lbl == "File":
                src_file = src
                src_qn = ""
            else:
                src_file, _, _ = src.partition("::")
                src_qn = src
            dst_file, _, _ = dst.partition("::")
            if kind == "imports":
                if not _match(src_file):
                    continue
            else:
                if not (_match(src_file) and _match(dst_file)):
                    continue
            # AP stores ``resolution_method`` wrapped in literal
            # single quotes (see ``automatised-pipeline``
            # resolver.rs:183 — ``format!("'{method}'")``), so the
            # value comes back INCLUDING quotes. Strip them here at
            # the infrastructure boundary. Remove this strip once
            # AP fixes the upstream quoting.
            conf_raw = r.get("confidence") if has_provenance else None
            try:
                confidence = float(conf_raw) if conf_raw is not None else None
            except (TypeError, ValueError):
                confidence = None
            reason_raw = r.get("reason") if has_provenance else None
            reason_str = (
                str(reason_raw).strip("'\"") or None if reason_raw else None
            )
            batch.append(
                {
                    "kind": kind,
                    "src_file": src_file,
                    "src_name": src_qn,
                    "dst_file": dst_file,
                    "dst_name": dst,
                    "confidence": confidence,
                    "reason": reason_str,
                }
            )
        return batch

    for kind, table, src_lbl, dst_lbl, has_prov in _AP_REL_TABLES:
        yield await _run_edge(kind, table, src_lbl, dst_lbl, has_provenance=has_prov)
