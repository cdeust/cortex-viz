"""Round-trip tests for the incremental snapshot splitter.

Contract under test (json_stream_split.iter_snapshot_segments): for ANY
chunking of the snapshot writer's output, the yielded segment bytes —
re-joined with ``,`` inside ``[...]`` — parse back to exactly the arrays
``json.dumps`` produced. Chunk sizes of 1 and 7 force every token and
string-escape sequence across a chunk boundary at some point, which is
where phantom-structure bugs live.
"""

from __future__ import annotations

import json

import pytest

from cortex_viz.shared.json_stream_split import (
    SnapshotSplitError,
    iter_snapshot_segments,
)

# Labels chosen to defeat naive splitters: structural bytes inside strings,
# escaped quotes, escaped backslash before a closing quote, unicode.
_HOSTILE_LABELS = [
    'plain',
    'braces {inside} [brackets]',
    'quote \\" then }',
    'trailing backslash \\\\',
    'json-in-string: {"k":[1,2,{"x":"]"}]}',
    'unicode 🧠 héat',
    '',
    'comma, and ]},{" fake boundary',
]


def _make_snapshot(n_nodes: int, n_edges: int) -> tuple[bytes, dict]:
    nodes = [
        {
            "id": f"node:{i}",
            "kind": "memory" if i % 2 else "entity",
            "label": _HOSTILE_LABELS[i % len(_HOSTILE_LABELS)],
            "heat": i / max(n_nodes, 1),
            "flags": {"protected": i % 3 == 0, "note": None},
        }
        for i in range(n_nodes)
    ]
    edges = [
        {
            "source": f"node:{i}",
            "target": f"node:{(i * 7 + 1) % max(n_nodes, 1)}",
            "kind": "about_entity",
            "weight": i,
        }
        for i in range(n_edges)
    ]
    graph = {"nodes": nodes, "edges": edges, "meta": {"n": n_nodes, "e": n_edges}}
    # Same serialisation as snapshot_pg_store.write_snapshot.
    payload = json.dumps(graph, separators=(",", ":"), default=str).encode()
    return payload, graph


def _chunk(payload: bytes, size: int):
    for off in range(0, len(payload), size):
        yield payload[off : off + size]


def _reassemble(pairs) -> dict:
    parts: dict[str, list[bytes]] = {"nodes": [], "edges": []}
    meta = None
    for section, raw in pairs:
        if section == "meta":
            meta = json.loads(raw)
        else:
            parts[section].append(raw)
    return {
        "nodes": json.loads(b"[" + b",".join(parts["nodes"]) + b"]"),
        "edges": json.loads(b"[" + b",".join(parts["edges"]) + b"]"),
        "meta": meta,
    }


@pytest.mark.parametrize("chunk_size", [1, 7, 64, 4096, 1 << 20])
def test_round_trip_hostile_strings(chunk_size):
    payload, graph = _make_snapshot(n_nodes=41, n_edges=97)
    out = _reassemble(iter_snapshot_segments(_chunk(payload, chunk_size)))
    assert out == graph


@pytest.mark.parametrize("chunk_size", [1, 13])
def test_empty_arrays(chunk_size):
    payload, graph = _make_snapshot(n_nodes=0, n_edges=0)
    out = _reassemble(iter_snapshot_segments(_chunk(payload, chunk_size)))
    assert out == graph


def test_single_chunk_whole_document():
    payload, graph = _make_snapshot(n_nodes=5, n_edges=3)
    out = _reassemble(iter_snapshot_segments([payload]))
    assert out == graph


def test_truncated_stream_raises():
    payload, _ = _make_snapshot(n_nodes=5, n_edges=3)
    with pytest.raises(SnapshotSplitError):
        list(iter_snapshot_segments([payload[: len(payload) // 2]]))


def test_wrong_prefix_raises():
    bad = b'{"edges":[],"nodes":[],"meta":{}}'
    with pytest.raises(SnapshotSplitError):
        list(iter_snapshot_segments([bad]))
