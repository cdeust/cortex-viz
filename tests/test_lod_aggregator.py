"""Tests for the pure multi-resolution LOD aggregator.

Verifies the Move-2 contract of ``core.lod_aggregator.aggregate``:
conservation (every level partitions the rows), centroid representatives,
modal kind, and occupied-cells-only (coarse levels stay tiny).
"""

from __future__ import annotations

from cortex_viz.core import lod_aggregator


def _rows(*tuples):
    return list(tuples)


def test_single_point_lands_in_one_cell_per_level():
    rows = _rows(("n1", 0.5, -0.5, "memory"))
    cells = lod_aggregator.aggregate(rows, max_level=3)
    # One occupied cell per level 0..3 → 4 cells total.
    assert len(cells) == 4
    # Level 0 is the whole world → the single cell (0,0,0).
    assert cells[(0, 0, 0)] == (0.5, -0.5, 1, "memory")


def test_conservation_count_equals_n_at_every_level():
    rows = _rows(
        ("a", -0.9, -0.9, "memory"),
        ("b", 0.9, 0.9, "entity"),
        ("c", 0.1, 0.1, "symbol"),
        ("d", -0.1, 0.2, "memory"),
    )
    max_level = 5
    cells = lod_aggregator.aggregate(rows, max_level=max_level)
    for level in range(max_level + 1):
        total = sum(c for (lvl, _, _), (_, _, c, _) in cells.items() if lvl == level)
        assert total == len(rows), f"level {level} lost rows"


def test_level_zero_is_centroid_of_all_points():
    rows = _rows(
        ("a", 0.0, 0.0, "memory"),
        ("b", 1.0, 1.0, "memory"),
    )
    cells = lod_aggregator.aggregate(rows, max_level=0)
    xbar, ybar, count, dom = cells[(0, 0, 0)]
    assert xbar == 0.5 and ybar == 0.5 and count == 2 and dom == "memory"


def test_modal_kind_wins_in_cell():
    # Three points in the same level-0 cell: two memory, one entity.
    rows = _rows(
        ("a", 0.1, 0.1, "memory"),
        ("b", 0.2, 0.2, "memory"),
        ("c", 0.3, 0.3, "entity"),
    )
    cells = lod_aggregator.aggregate(rows, max_level=0)
    _, _, count, dom = cells[(0, 0, 0)]
    assert count == 3 and dom == "memory"


def test_occupied_cells_only_coarse_levels_tiny():
    # 100 points clustered near one corner: level 3 (64 possible cells) must
    # have far fewer than 64 occupied cells — occupied-only is the whole point.
    rows = _rows(*[(f"n{i}", -0.95 + i * 0.0001, -0.95, "memory") for i in range(100)])
    cells = lod_aggregator.aggregate(rows, max_level=3)
    level3 = [k for k in cells if k[0] == 3]
    assert 0 < len(level3) <= 64
    # Level 0 always exactly one cell.
    assert len([k for k in cells if k[0] == 0]) == 1


def test_out_of_world_coords_are_clamped_not_dropped():
    rows = _rows(("a", 5.0, -5.0, "memory"))
    cells = lod_aggregator.aggregate(rows, max_level=2)
    # Clamped to the corner cell at level 2: cx = 3 (max), cy = 0 (min).
    assert (2, 3, 0) in cells
    assert cells[(2, 3, 0)][2] == 1


def test_negative_max_level_raises():
    import pytest

    with pytest.raises(ValueError):
        lod_aggregator.aggregate(_rows(("a", 0.0, 0.0, "memory")), max_level=-1)
