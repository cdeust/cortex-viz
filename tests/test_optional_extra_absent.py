"""What every optional-extra code path does when the extra is absent (#88).

These are the ``ImportError`` guards behind ``/api/tile``, ``/api/quadtree``
and ``/api/recompute_layout``. They decide whether a missing ~200 MB extra
reads as "this install cannot do that" or as a fault (#90) — and until
this module existed, not one of them ran in any environment: with the
extras installed the guard is unreachable, and without them the tests that
would have reached it were skipped.

Absence is simulated by blocking the import rather than by uninstalling,
so these tests run in EVERY environment — including a ``dev``-only one,
which is the environment whose blind spot opened #88. Nothing here is
behind an ``importorskip``, by design.
"""

from __future__ import annotations

import builtins
import io

import pytest

from cortex_viz.core import layout_engine, tile_renderer
from cortex_viz.handlers import quadtree_handler, recompute_layout, tile_handler


def block_imports(monkeypatch, *blocked: str) -> None:
    """Make ``import <blocked>`` (and its submodules) raise ImportError.

    Matches on the dotted prefix so blocking ``datashader`` also blocks
    ``datashader.transfer_functions``. Everything else imports normally,
    so the code under test still reaches the guard by its real route
    instead of being handed a pre-built exception.
    """
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        root = name.split(".")[0]
        if root in blocked or name in blocked:
            raise ImportError(f"No module named {name!r}")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)


class _FakeHandler:
    """BaseHTTPRequestHandler stand-in: records status/headers/body."""

    def __init__(self, path: str) -> None:
        self.path = path
        self.status: int | None = None
        self.headers: dict[str, str] = {}
        self.headers_sent: dict[str, str] = {}
        self.wfile = io.BytesIO()

    def send_response(self, code: int) -> None:
        self.status = code

    def send_header(self, key: str, value: str) -> None:
        self.headers_sent[key] = value

    def end_headers(self) -> None:
        pass

    def body_json(self):
        import json

        return json.loads(self.wfile.getvalue().decode())


# ── core.tile_renderer ───────────────────────────────────────────────


def test_render_tile_png_names_the_extra_when_datashader_is_absent(monkeypatch):
    """The message must carry the fix, since nothing downstream can.

    ``send_json_capability_unavailable`` deliberately never echoes the
    ImportError to the client (#90), so this string only ever reaches a
    server log — which is exactly where an operator looks.
    """
    block_imports(monkeypatch, "datashader", "pandas")

    with pytest.raises(ImportError) as excinfo:
        tile_renderer.render_tile_png([("memory:1", 0.0, 0.0, "memory")], z=0, x=0, y=0)

    assert "viz-tile" in str(excinfo.value)
    assert "pip install cortex-viz[viz-tile]" in str(excinfo.value)


def test_empty_tile_png_reports_the_missing_pillow(monkeypatch):
    """The empty-tile branch imports PIL directly, not through datashader.

    Called at the unit, not through ``render_tile_png``: that entry
    imports datashader BEFORE it tests ``if not rows``, so on a
    ``dev``-only install the datashader guard fires first and this branch
    is never reached. Going through the public function would therefore
    assert the Pillow guard only in environments that HAVE Pillow —
    precisely the blind spot #88 is about (measured 2026-08-02 in a
    dev-only venv).
    """
    block_imports(monkeypatch, "PIL")

    with pytest.raises(ImportError, match="Pillow"):
        tile_renderer._empty_tile_png(512)


# ── /api/tile ────────────────────────────────────────────────────────


def test_tile_route_degrades_when_the_renderer_cannot_import(monkeypatch):
    """The guard that actually fires when ``viz-tile`` is absent.

    ``tile_renderer`` and the two stores import fine without the extra —
    none of them imports datashader at module level — so the route's
    FIRST guard is never reached by a missing extra. This second one, on
    the lazy import inside ``render_tile_png``, is the live path.
    """
    reads: list[dict] = []

    def _read(store, **kwargs):
        reads.append(kwargs)
        return [("memory:1", 0.0, 0.0, "memory")]

    monkeypatch.setattr(
        "cortex_viz.infrastructure.layout_pg_store.read_positions_in_bbox", _read
    )
    block_imports(monkeypatch, "datashader", "pandas")
    handler = _FakeHandler("/api/tile/0/0/0.png")

    tile_handler.serve(handler, store=object())

    # Discriminates the two guards: the first one returns BEFORE the bbox
    # read, so a read having happened proves the response came from the
    # second. Without this the test would pass either way.
    assert reads, "the first guard fired — this test is no longer testing the second"
    # 200, not 503: an uninstalled opt-in extra is a supported
    # configuration, and a status code must never contradict its body (#90).
    assert handler.status == 200
    body = handler.body_json()
    assert body == {
        "status": "unavailable",
        "capability": "viz-tile",
        "reason": "extra_not_installed",
        "fallback": "/api/graph",
    }
    assert "detail" not in body, "the ImportError text must not reach the client"


def test_tile_route_degrades_when_the_package_itself_is_broken(monkeypatch):
    """The route's first guard, covering what it can genuinely catch.

    Not the absent extra (see above) but a broken/partial install of
    cortex_viz itself. Same answer either way, which is why the guard
    stays: the client degrades instead of reading a connection reset.
    """

    def _must_not_read(store, **kwargs):
        raise AssertionError("the first guard must return before any bbox read")

    monkeypatch.setattr(
        "cortex_viz.infrastructure.layout_pg_store.read_positions_in_bbox",
        _must_not_read,
    )
    block_imports(monkeypatch, "cortex_viz.infrastructure")
    handler = _FakeHandler("/api/tile/0/0/0.png")

    tile_handler.serve(handler, store=object())

    assert handler.status == 200
    assert handler.body_json()["capability"] == "viz-tile"


def test_tile_route_logs_the_cause_to_stderr(monkeypatch, capsys):
    """The operator's only copy of the diagnosis."""
    monkeypatch.setattr(
        "cortex_viz.infrastructure.layout_pg_store.read_positions_in_bbox",
        lambda store, **kwargs: [("memory:1", 0.0, 0.0, "memory")],
    )
    block_imports(monkeypatch, "datashader", "pandas")

    tile_handler.serve(_FakeHandler("/api/tile/0/0/0.png"), store=object())

    err = capsys.readouterr().err
    assert "/api/tile unavailable" in err
    assert "viz-tile extra absent" in err


# ── /api/quadtree ────────────────────────────────────────────────────


def test_quadtree_route_degrades_without_pyarrow(monkeypatch):
    """Arrow IPC is imported at the top of ``serve`` — reached directly."""
    block_imports(monkeypatch, "pyarrow")
    handler = _FakeHandler("/api/quadtree")

    quadtree_handler.serve(handler, store=object())

    assert handler.status == 200
    assert handler.body_json() == {
        "status": "unavailable",
        "capability": "viz-tile",
        "reason": "extra_not_installed",
        "fallback": "/api/graph",
    }


def test_quadtree_route_logs_the_cause_to_stderr(monkeypatch, capsys):
    block_imports(monkeypatch, "pyarrow")

    quadtree_handler.serve(_FakeHandler("/api/quadtree"), store=object())

    assert "/api/quadtree unavailable" in capsys.readouterr().err


# ── /api/recompute_layout ────────────────────────────────────────────


def test_layout_engine_names_the_extra_when_igraph_is_absent(monkeypatch):
    block_imports(monkeypatch, "igraph")

    with pytest.raises(ImportError) as excinfo:
        layout_engine.layout(["a", "b"], [("a", "b")])

    assert "viz-tile" in str(excinfo.value)


def test_recompute_reports_igraph_missing_instead_of_raising(monkeypatch):
    """The route must answer JSON, not propagate the ImportError.

    Blocking the import (rather than stubbing ``layout_engine.layout``)
    keeps the test on the real route: the guard is reached through the
    engine's own failure, exactly as it would be on a ``dev``-only install.
    """
    from cortex_viz.server import graph_cache_state

    monkeypatch.setattr(
        graph_cache_state,
        "_graph_cache",
        {
            "data": {
                "nodes": [
                    {"id": "memory:1", "kind": "memory"},
                    {"id": "memory:2", "kind": "memory"},
                ],
                "edges": [{"source": "memory:1", "target": "memory:2"}],
            }
        },
        raising=False,
    )
    block_imports(monkeypatch, "igraph")

    result = recompute_layout.run_recompute(store=object())

    assert result["status"] == "error"
    assert result["reason"] == "igraph_missing"
    assert "viz-tile" in result["detail"]
