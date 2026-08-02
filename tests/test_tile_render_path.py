"""End-to-end coverage of the ``viz-tile`` render path (#88).

The 13 advisories closed in #87 were all Pillow, and Pillow is reached
only here — ``tile_renderer`` hands the Datashader raster to PIL, which
encodes the PNG that ``/api/tile`` returns. Until this module existed,
that decoder sat behind zero tests in either environment, so a
regression in the image path (or an incompatibility introduced by the
very version bump that closed the CVEs) could not fail CI.

Scope: everything from the HTTP route down to the PNG bytes is REAL —
datashader aggregation, PIL encoding, response framing. Only the
PostgreSQL read is substituted, because the layout table is not a
property of the render path.

The whole module skips when ``viz-tile`` is absent, which is correct
locally; ``tests/test_optional_extras_present.py`` is what makes that
skip fail in CI instead of passing silently.
"""

from __future__ import annotations

import io

import pytest

pytest.importorskip("datashader")
pytest.importorskip("PIL")

from PIL import Image  # noqa: E402 - after the extra guard, by design

from cortex_viz.core import tile_renderer  # noqa: E402
from cortex_viz.handlers import tile_handler  # noqa: E402

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"

# Four nodes inside the z=0 world bbox ([-1,1]²), two of them sharing a
# kind so the categorical aggregation has both a repeated and a unique
# category to colour.
_ROWS = [
    ("memory:1", -0.5, -0.5, "memory"),
    ("memory:2", 0.5, 0.5, "memory"),
    ("file:3", 0.0, 0.25, "file"),
    ("agent:4", -0.25, 0.75, "agent"),
]


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

    def body(self) -> bytes:
        return self.wfile.getvalue()

    def body_json(self):
        import json

        return json.loads(self.wfile.getvalue().decode())


# ── tile_renderer: the Pillow-consuming unit ─────────────────────────


def test_render_tile_png_emits_a_decodable_png():
    """The bytes must be a PNG that an image decoder actually accepts.

    Asserting the magic number alone would pass on a truncated file; the
    point of this test is that Pillow can read back what Pillow wrote.
    """
    png = tile_renderer.render_tile_png(_ROWS, z=0, x=0, y=0)

    assert png.startswith(PNG_MAGIC)
    img = Image.open(io.BytesIO(png))
    img.load()  # force full decode — Image.open is lazy
    assert img.format == "PNG"
    assert img.size == (512, 512)


def test_render_tile_png_paints_the_points():
    """A populated tile must not be blank — the aggregation has to land.

    Guards the failure mode a magic-number check misses: a valid PNG of
    the right size that contains nothing, which is what a broken
    datashader/pandas interop produces.
    """
    png = tile_renderer.render_tile_png(_ROWS, z=0, x=0, y=0)
    img = Image.open(io.BytesIO(png)).convert("RGBA")

    alpha_min, alpha_max = img.getchannel("A").getextrema()
    assert alpha_max > 0, "populated tile rendered fully transparent"
    assert alpha_min == 0, "a 4-point tile should still be mostly empty"


def test_render_tile_png_empty_input_is_a_valid_transparent_tile():
    """Empty input takes ``_empty_tile_png`` — the direct PIL path.

    Client compositors expect a valid tile rather than a 404, so the
    contract is a fully transparent PNG of the requested size.
    """
    png = tile_renderer.render_tile_png([], z=3, x=1, y=1, tile_size=256)

    img = Image.open(io.BytesIO(png)).convert("RGBA")
    img.load()
    assert img.size == (256, 256)
    assert img.getchannel("A").getextrema() == (0, 0)


def test_render_tile_png_honours_tile_size():
    png = tile_renderer.render_tile_png(_ROWS, z=0, x=0, y=0, tile_size=128)
    assert Image.open(io.BytesIO(png)).size == (128, 128)


def test_render_tile_png_clips_to_the_tile_bbox():
    """Points outside the tile's world bbox must not be painted.

    z=1/x=0/y=0 is the top-left quadrant ([-1,0] × [0,1]); a point in the
    opposite quadrant belongs to a different tile.
    """
    outside = [("memory:9", 0.75, -0.75, "memory")]
    png = tile_renderer.render_tile_png(outside, z=1, x=0, y=0)

    img = Image.open(io.BytesIO(png)).convert("RGBA")
    assert img.getchannel("A").getextrema() == (0, 0)


# ── /api/tile: the route, end to end ─────────────────────────────────


def test_serve_returns_png_bytes_with_consistent_framing(monkeypatch):
    """The whole route: parse → read → datashader → PIL → HTTP framing."""
    monkeypatch.setattr(
        "cortex_viz.infrastructure.layout_pg_store.read_positions_in_bbox",
        lambda store, **kwargs: list(_ROWS),
    )
    handler = _FakeHandler("/api/tile/0/0/0.png")

    tile_handler.serve(handler, store=object())

    assert handler.status == 200
    assert handler.headers_sent["Content-Type"] == "image/png"
    body = handler.body()
    assert body.startswith(PNG_MAGIC)
    # A wrong Content-Length is how a keep-alive client hangs (issue #66).
    assert handler.headers_sent["Content-Length"] == str(len(body))
    Image.open(io.BytesIO(body)).load()


def test_serve_renders_an_empty_layout_as_a_blank_tile(monkeypatch):
    """No rows in the bbox is a normal tile, not an error."""
    monkeypatch.setattr(
        "cortex_viz.infrastructure.layout_pg_store.read_positions_in_bbox",
        lambda store, **kwargs: [],
    )
    handler = _FakeHandler("/api/tile/2/1/1.png")

    tile_handler.serve(handler, store=object())

    assert handler.status == 200
    img = Image.open(io.BytesIO(handler.body())).convert("RGBA")
    assert img.getchannel("A").getextrema() == (0, 0)


@pytest.mark.parametrize(
    "path",
    [
        "/api/tile/0/0/0.jpg",
        "/api/tile/0/0.png",
        "/api/tile/a/0/0.png",
        "/api/tile/0/0/0.png/../../etc",
    ],
)
def test_serve_404s_a_malformed_path(path):
    handler = _FakeHandler(path)
    tile_handler.serve(handler, store=object())
    assert handler.status == 404
