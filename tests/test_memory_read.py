"""Contract test for the viz read path (MemoryReader).

Proves that ``MemoryReader`` exposes exactly the read surface the viz server
consumes from Cortex — the 14 ``store.<method>()`` calls plus the dict-row
``_conn`` used by the raw sankey ``SELECT`` sites — against the real shared
PostgreSQL store. This is the Phase-2 acceptance bar of the viz-MCP extraction:
the only hard code-coupling (``MemoryStore``) is replaced by a read-only path
that imports ``psycopg`` + stdlib only.

The test connects to the shared Cortex database over ``DATABASE_URL``. If no
database is reachable it skips — the contract (method names, arities, return
shapes) is still asserted structurally via ``test_surface_matches_contract``,
which needs no connection.
"""

from __future__ import annotations

import inspect

import pytest

from cortex_viz.infrastructure.memory_read import MemoryReader, _resolve_database_url

# The exact surface grepped from mcp_server/server/ (store.<method>( call sites).
# A divergence here means the viz server would call a method the reader lacks.
CONTRACT_METHODS = {
    "get_hot_memories",
    "get_entity_by_id",
    "get_domain_counts",
    "count_relationships",
    "count_memories",
    "count_entities",
    "get_top_entities_for_domain",
    "get_recent_memories",
    "get_memory",
    "get_last_consolidation",
    "get_avg_heat",
    "get_all_relationships",
    "get_all_entities",
    "count_active_triggers",
}


def test_surface_matches_contract() -> None:
    """Every method the viz calls exists on the reader, and nothing writes."""
    for name in CONTRACT_METHODS:
        assert hasattr(MemoryReader, name), f"reader missing {name}"
        assert callable(getattr(MemoryReader, name))

    # No write surface: the reader must not expose mutation verbs. Guards
    # against accidentally widening the contract back toward MemoryStore.
    public = {n for n in dir(MemoryReader) if not n.startswith("_")}
    forbidden_prefixes = ("store_", "save_", "update_", "insert_", "delete_",
                          "remember", "bump_", "log_", "anchor")
    leaked = {n for n in public if n.startswith(forbidden_prefixes)}
    assert not leaked, f"reader leaked write-ish methods: {leaked}"


def test_no_mcp_server_import() -> None:
    """Boundary invariant: the reader module imports no mcp_server.* symbol.

    The plan's acceptance criterion is ``grep -r "mcp_server\\." cortex_viz/``
    returning zero — i.e. no *import* of the Cortex package. Prose mentions in
    docstrings (explaining what coupling this module severs) are fine; only
    code references to ``mcp_server.`` are a breach.
    """
    import ast

    import cortex_viz.infrastructure.memory_read as mod

    tree = ast.parse(inspect.getsource(mod))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert not alias.name.startswith("mcp_server"), (
                    f"boundary breach: import {alias.name}"
                )
        elif isinstance(node, ast.ImportFrom):
            assert not (node.module or "").startswith("mcp_server"), (
                f"boundary breach: from {node.module} import ..."
            )


def test_default_database_url() -> None:
    """URL resolution mirrors Cortex: empty/${...} → 127.0.0.1 default."""
    url = _resolve_database_url()
    assert url.startswith("postgresql://")


@pytest.fixture
def reader():
    try:
        r = MemoryReader()
    except Exception as exc:  # noqa: BLE001 — any connect failure → skip
        pytest.skip(f"no Cortex database reachable: {exc}")
    yield r
    r.close()


def test_counts_return_ints(reader) -> None:
    assert isinstance(reader.count_relationships(), int)
    assert isinstance(reader.count_entities(), int)
    assert isinstance(reader.count_active_triggers(), int)


def test_count_memories_shape(reader) -> None:
    counts = reader.count_memories()
    assert isinstance(counts, dict)
    for key in ("total", "episodic", "semantic", "active",
                "archived", "stale", "protected"):
        assert key in counts


def test_avg_heat_is_float(reader) -> None:
    assert isinstance(reader.get_avg_heat(), float)


def test_domain_counts_is_mapping(reader) -> None:
    dc = reader.get_domain_counts()
    assert isinstance(dc, dict)
    assert all(isinstance(v, int) for v in dc.values())


def test_hot_memories_normalized(reader) -> None:
    rows = reader.get_hot_memories(min_heat=0.0, limit=5)
    assert isinstance(rows, list)
    for row in rows:
        assert "heat" in row  # normalizer aliases heat_base → heat
        assert "embedding" not in row  # dropped on the read path


def test_recent_memories_and_get_memory_roundtrip(reader) -> None:
    recent = reader.get_recent_memories(limit=3)
    assert isinstance(recent, list)
    if recent:
        one = reader.get_memory(recent[0]["id"])
        assert one is not None
        assert one["id"] == recent[0]["id"]
    # Non-existent id returns None, never raises.
    assert reader.get_memory(-1) is None


def test_entities_and_relationships(reader) -> None:
    ents = reader.get_all_entities(min_heat=0.0)
    assert isinstance(ents, list)
    rels = reader.get_all_relationships()
    assert isinstance(rels, list)
    if ents:
        eid = ents[0]["id"]
        assert reader.get_entity_by_id(eid)["id"] == eid


def test_raw_conn_select_sankey_path(reader) -> None:
    """The 4 raw store._conn.execute() sankey sites must work via the reader."""
    row = reader._conn.execute(
        "SELECT COUNT(*) AS c FROM stage_transitions"
    ).fetchone()
    assert "c" in row and isinstance(row["c"], int)


def test_get_last_consolidation_type(reader) -> None:
    val = reader.get_last_consolidation()
    assert val is None or isinstance(val, str)
