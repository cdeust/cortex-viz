"""Transport-metadata contract of the real ``GraphEventStream``.

Every other test of the activity path substitutes a fake stream whose ``emit``
merely records its arguments, so nothing asserted that the production stream
actually attaches ``event_meta`` to what a subscriber receives. The durable
PostgreSQL cursor the SSE handler depends on rides entirely on that merge.
"""

from __future__ import annotations

import pytest

from cortex_viz.server.graph_event_stream import GraphEventStream


@pytest.fixture
def stream():
    live = GraphEventStream()
    yield live
    live.reset()


def test_event_meta_reaches_the_subscriber(stream):
    stream.emit(
        "activity", [{"id": "n1"}], [{"id": "e1"}], event_meta={"activity_id": 7}
    )

    (_idx, event) = next(iter(stream.subscribe(since=0, timeout=0.01)))
    assert event["activity_id"] == 7
    assert event["label"] == "activity"


def test_every_chunk_of_a_split_batch_carries_the_cursor(stream):
    """A subscriber may be cut off mid-batch, so the cursor cannot live on
    only the first chunk — it must be resumable from any delivered event."""
    nodes = [{"id": f"n{i}"} for i in range(5)]
    emitted = stream.emit("activity", nodes, [], chunk=2, event_meta={"activity_id": 9})

    events = [event for _idx, event in stream.subscribe(since=0, timeout=0.01)]
    assert emitted == len(events) == 3
    assert [event["activity_id"] for event in events] == [9, 9, 9]
    assert [len(event["nodes"]) for event in events] == [2, 2, 1]


def test_no_event_meta_leaves_the_wire_shape_untouched(stream):
    """Absence is the behaviour: the build stream emits without metadata and
    must not gain a null key the client would have to special-case."""
    stream.emit("memories", [{"id": "n1"}], [])

    (_idx, event) = next(iter(stream.subscribe(since=0, timeout=0.01)))
    assert set(event) == {"label", "off", "n_total", "e_total", "nodes", "edges"}


@pytest.mark.parametrize("meta", [None, {}])
def test_an_empty_meta_is_the_same_as_none(stream, meta):
    stream.emit("memories", [{"id": "n1"}], [], event_meta=meta)

    (_idx, event) = next(iter(stream.subscribe(since=0, timeout=0.01)))
    assert "activity_id" not in event


def test_event_meta_cannot_overwrite_the_batch_envelope(stream):
    """`event.update(event_meta)` applies metadata last. A caller-supplied key
    colliding with the envelope would corrupt chunk accounting, so pin which
    side wins and make a future collision visible here rather than on the wire.
    """
    stream.emit("activity", [{"id": "n1"}], [], event_meta={"label": "spoofed"})

    (_idx, event) = next(iter(stream.subscribe(since=0, timeout=0.01)))
    assert event["label"] == "spoofed"
    assert event["n_total"] == 1


def test_an_empty_batch_emits_nothing_even_with_metadata(stream):
    assert stream.emit("activity", [], [], event_meta={"activity_id": 3}) == 0
    assert list(stream.subscribe(since=0, timeout=0.01)) == []
