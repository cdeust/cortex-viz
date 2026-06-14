"""Process-wide live activity event stream (single persistent instance).

Reuses ``GraphEventStream`` (the build's SSE queue) but, unlike the build
stream, is NEVER reset — it is the continuous channel for session activity:
the ingest endpoint ``emit()``s each captured action's nodes/edges, and every
``/api/activity/stream`` subscriber replays from its cursor and then blocks for
new events. One producer (the POST ingest handler) + many subscribers.
"""

from __future__ import annotations

from cortex_viz.server.graph_event_stream import GraphEventStream

_STREAM = GraphEventStream()


def stream() -> GraphEventStream:
    """The singleton activity stream."""
    return _STREAM
