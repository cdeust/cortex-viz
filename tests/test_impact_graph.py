"""Unit tests for ``core.impact_graph.impact_to_graph`` — P4 node-
unification: the center FILE node id must equal the SAME
``NodeIdFactory.file_id`` hash the activity spine mints for the edited
file, so the live blast-radius fragment attaches to the SAME node the
edit action already targets (not a duplicate ``file:<literal path>``
node, the pre-fix behavior).
"""

from __future__ import annotations

from cortex_viz.core.impact_graph import impact_to_graph
from cortex_viz.core.workflow_graph_schema import NodeIdFactory


def test_center_node_id_matches_galaxy_hash_scheme():
    frag = impact_to_graph("/Users/dev/repo/foo.py", {})
    assert frag["nodes"][0]["id"] == NodeIdFactory.file_id("/Users/dev/repo/foo.py")


def test_upstream_symbol_edges_point_into_the_edited_file():
    impact = {"upstream": [{"qualified_name": "mod::caller"}]}
    frag = impact_to_graph("/Users/dev/repo/foo.py", impact)
    fid = NodeIdFactory.file_id("/Users/dev/repo/foo.py")
    edge = next(e for e in frag["edges"] if e["kind"] == "impacts")
    assert edge["target"] == fid
    assert edge["source"] == "symbol:mod::caller"


def test_downstream_symbol_edges_point_out_of_the_edited_file():
    impact = {"downstream": [{"qualified_name": "mod::dep"}]}
    frag = impact_to_graph("/Users/dev/repo/foo.py", impact)
    fid = NodeIdFactory.file_id("/Users/dev/repo/foo.py")
    edge = next(e for e in frag["edges"] if e["kind"] == "uses")
    assert edge["source"] == fid
    assert edge["target"] == "symbol:mod::dep"
