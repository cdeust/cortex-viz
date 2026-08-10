"""The background build must reach the event stream through ``get_stream()``.

`graph_event_stream` deliberately carries no module-level ``emit``/``close``/
``reset`` forwarders: one of them silently dropped ``event_meta`` once ``emit``
grew it, so the singleton has exactly one door. When those forwarders were
removed, `graph_build_run` was still calling all three **on the module**, and
nothing here noticed:

- ``_events.reset()`` raised ``AttributeError`` on the first line of every
  build, so the graph never built at all — ``/api/graph/progress`` sat at
  ``phase: "starting", pct: 0.0`` forever;
- ``_ev.close()`` in the ``finally`` block sat inside ``except Exception: pass``,
  so it failed **silently** — subscribers never received ``done`` and never
  disconnected.

The unit tests around the merge closure all pass a fake stream, so they could
not catch this: the defect was in how the production caller *obtains* the
stream, not in what it does with it. These tests assert that binding, which is
why they are static — reproducing the crash behaviourally would mean standing up
a build, and a build needs PostgreSQL.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from cortex_viz.server import graph_event_stream as module_under_contract
from cortex_viz.server.graph_event_stream import GraphEventStream

STREAM_ONLY_METHODS = ("emit", "close", "reset")

_BUILD_RUN = Path(module_under_contract.__file__).parent / "graph_build_run.py"


def _module_alias_names(tree: ast.AST) -> set[str]:
    """Names bound to the `graph_event_stream` MODULE, however imported."""
    aliases: set[str] = set()
    for node in ast.walk(tree):
        # `from cortex_viz.server import graph_event_stream as _events`
        if isinstance(node, ast.ImportFrom) and node.module in {
            "cortex_viz.server",
            "cortex_viz",
        }:
            for name in node.names:
                if name.name.endswith("graph_event_stream"):
                    aliases.add(name.asname or name.name)
        # `import cortex_viz.server.graph_event_stream as _events`
        elif isinstance(node, ast.Import):
            for name in node.names:
                if name.name.endswith("graph_event_stream") and name.asname:
                    aliases.add(name.asname)
    return aliases


def _attributes_called_on(tree: ast.AST, names: set[str]) -> set[str]:
    return {
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id in names
    }


@pytest.fixture(scope="module")
def build_run_tree() -> ast.AST:
    return ast.parse(_BUILD_RUN.read_text(encoding="utf-8"))


def test_the_singleton_keeps_exactly_one_door():
    """Pins the deliberate removal: re-adding a forwarder re-opens the weaker
    path that dropped `event_meta`."""
    for name in STREAM_ONLY_METHODS:
        assert not hasattr(module_under_contract, name), (
            f"graph_event_stream regained a module-level {name}() forwarder; "
            "callers must use get_stream()"
        )
        assert hasattr(GraphEventStream, name), (
            f"GraphEventStream lost {name}() — the build calls it"
        )


def test_the_build_never_calls_stream_methods_on_the_module(build_run_tree):
    """The regression itself. Fails on the pre-fix source, where
    `_events` was the module and `_events.reset()` was an AttributeError."""
    aliases = _module_alias_names(build_run_tree)
    offending = _attributes_called_on(build_run_tree, aliases) & set(
        STREAM_ONLY_METHODS
    )
    assert not offending, (
        f"graph_build_run calls {sorted(offending)} on the graph_event_stream "
        f"module (bound as {sorted(aliases)}); those live on GraphEventStream. "
        "Obtain the stream with get_stream() instead."
    )


def test_make_merge_is_annotated_against_the_stream_not_the_module():
    """A caller that passes the module must fail at type-check, not at the
    first emit halfway through a build.

    Resolved with ``get_type_hints`` rather than read off ``__annotations__``:
    the module uses ``from __future__ import annotations``, so the raw value is
    the *string* ``"GraphEventStream"`` and an identity check against the class
    would pass vacuously for any annotation at all.
    """
    from typing import get_type_hints

    from cortex_viz.server.graph_build_merge import make_merge

    assert get_type_hints(make_merge).get("events") is GraphEventStream, (
        "make_merge's `events` parameter must be typed GraphEventStream"
    )


def _names_bound_to_get_stream(tree: ast.AST) -> set[str]:
    bound: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        call = node.value
        if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Name)):
            continue
        if not call.func.id.endswith("get_stream"):
            continue
        bound.update(t.id for t in node.targets if isinstance(t, ast.Name))
    return bound


def test_a_real_stream_satisfies_every_call_the_build_makes(build_run_tree):
    """Whatever the build calls on its stream must exist on the real class —
    so moving a method on GraphEventStream breaks here, not in production."""
    stream_names = _names_bound_to_get_stream(build_run_tree)
    assert stream_names, "graph_build_run no longer binds a stream via get_stream()"

    live = GraphEventStream()
    try:
        called = _attributes_called_on(build_run_tree, stream_names)
        assert called, "the build binds a stream but never calls anything on it"
        for attr in called:
            assert hasattr(live, attr), (
                f"the build calls .{attr}() on the stream, "
                "which GraphEventStream does not provide"
            )
    finally:
        live.reset()
