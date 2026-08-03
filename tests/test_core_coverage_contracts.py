"""Behavior contracts for the extracted pure graph and temporal modules."""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

import pytest

from cortex_viz.core import graph_builder_dedup as dedup
from cortex_viz.core import graph_builder_edges as edge_logic
from cortex_viz.core import graph_quality_scorer as quality
from cortex_viz.core import temporal


def test_domain_aggregation_merges_signals_and_drops_trivial_profiles(monkeypatch):
    monkeypatch.setattr(
        "cortex_viz.shared.domain_mapping.resolve_domain", lambda key: key
    )
    profiles = {
        "a": {
            "label": "/Users/clement/alpha-project",
            "sessionCount": 2,
            "confidence": 0.4,
            "entryPoints": [{"pattern": "start"}],
            "recurringPatterns": [{"pattern": "shared", "frequency": 2}],
            "toolPreferences": {"Read": {"ratio": 0.2, "avgPerSession": 1}},
            "featureActivations": {"care": 0.2},
        },
        "b": {
            "label": "/Documents/alpha-project",
            "sessionCount": 3,
            "confidence": 0.9,
            "entryPoints": [{"pattern": "start"}, {"pattern": "debug"}],
            "recurringPatterns": [
                {"pattern": "shared", "frequency": 9},
                {"pattern": "unique", "frequency": 5},
            ],
            "toolPreferences": {"Read": {"ratio": 0.8, "avgPerSession": 3}},
            "featureActivations": {"care": 0.6, "speed": 0.5},
            "connectionBridges": [{"toDomain": "other", "weight": 0.7}],
        },
        "tiny": {"label": "tiny", "sessionCount": 1},
        "empty": None,
    }

    merged = dedup.aggregate_domains(profiles)

    assert set(merged) == {"alpha project"}
    profile = merged["alpha project"]
    assert profile["sessionCount"] == 5
    assert profile["confidence"] == 0.9
    assert [row["pattern"] for row in profile["entryPoints"]] == ["start", "debug"]
    assert [row["pattern"] for row in profile["recurringPatterns"]] == [
        "unique",
        "shared",
    ]
    assert profile["toolPreferences"]["Read"] == {
        "ratio": pytest.approx(0.5),
        "avgPerSession": pytest.approx(2.0),
    }
    assert profile["featureActivations"] == {
        "care": pytest.approx(0.4),
        "speed": pytest.approx(0.5),
    }
    assert profile["connectionBridges"][0]["toDomain"] == "other"
    assert profile["_orig_keys"] == ["a", "b"]


def test_domain_group_key_and_single_profile_contract(monkeypatch):
    monkeypatch.setattr(
        "cortex_viz.shared.domain_mapping.resolve_domain", lambda key: key
    )
    assert dedup.domain_group_key(" /Users/Documents/ ") == "/Users/Documents/"
    assert dedup.domain_group_key("My-Useful_Project extra") == "my useful"
    source = {"label": "solo", "sessionCount": 2}
    assert dedup.aggregate_domains({"solo": source}) == {"solo": source}
    assert source["_orig_keys"] == ["solo"]


def test_graph_edge_construction_deduplicates_and_filters_noise():
    edges: list[dict] = []
    edge_logic.add_bridge_edges(
        {
            "connectionBridges": [
                {"toDomain": "b", "weight": 0.8, "pattern": "shared"},
                {"toDomain": "missing"},
            ]
        },
        "hub-a",
        ["a", "b"],
        {"a": "hub-a", "b": "hub-b"},
        edges,
    )
    edge_logic.add_persistent_feature_edges(
        {
            "persistentFeatures": [
                {"domains": ["a", "b"], "persistence": 0.6, "label": "one"},
                {"domains": ["b", "a"], "persistence": 1.8, "label": "two"},
                {"domains": ["a", "a"], "persistence": 1.0},
                {"domains": ["a", "missing"], "persistence": 1.0},
            ]
        },
        {"a": "hub-a", "b": "hub-b"},
        edges,
    )
    edge_logic.add_relationship_edges(
        [
            {"source_entity_id": 1, "target_entity_id": 2, "type": "calls"},
            {"source_entity_id": 1, "target_entity_id": 2, "type": "co_occurrence"},
            {"source_entity_id": 1, "target_entity_id": 1, "type": "related"},
            {"source_entity_id": 1, "target_entity_id": 9, "type": "related"},
        ],
        {1: "e1", 2: "e2"},
        edges,
    )

    assert [edge["type"] for edge in edges] == [
        "bridge",
        "persistent-feature",
        "calls",
    ]
    assert edges[1]["weight"] == 1.0
    assert edges[1]["label"] == "2 shared features"
    assert edges[2]["color"] == edge_logic.EDGE_COLORS["calls"]


def test_cluster_and_pagination_contracts():
    nodes = [
        {"id": "root", "type": "root", "group": "a", "color": "#abc"},
        {"id": "domain", "type": "domain", "group": "a"},
        {"id": "c1", "type": "memory", "group": "a"},
        {"id": "c2", "type": "memory", "group": "b"},
        {"id": "c3", "type": "memory", "group": "b"},
    ]
    edges = [
        {"source": "root", "target": "domain"},
        {"source": "root", "target": "c1"},
        {"source": "c1", "target": "c2"},
    ]
    clusters = edge_logic.build_clusters(nodes, {"a": "root", "b": "absent"})
    assert {row["domain"] for row in clusters} == {"a", "b"}
    assert next(row for row in clusters if row["domain"] == "a")["color"] == "#abc"

    unchanged = edge_logic.apply_batch_pagination(nodes, edges, clusters, 0, 0)
    assert unchanged == (nodes, edges, clusters, 1)
    skeleton, skeleton_edges, skeleton_clusters, batches = (
        edge_logic.apply_batch_pagination(nodes, edges, clusters, 0, 2)
    )
    assert [row["id"] for row in skeleton] == ["root", "domain"]
    assert skeleton_edges == [{"source": "root", "target": "domain"}]
    assert skeleton_clusters == clusters
    assert batches == 2
    page, page_edges, page_clusters, _ = edge_logic.apply_batch_pagination(
        nodes, edges, clusters, 1, 2
    )
    assert [row["id"] for row in page] == ["c1", "c2"]
    assert page_edges == [
        {"source": "root", "target": "c1"},
        {"source": "c1", "target": "c2"},
    ]
    assert page_clusters == []


@pytest.mark.parametrize(
    ("node", "connections", "label_fragment"),
    [
        ({"type": "root"}, 2, "root node"),
        ({"type": "agent", "toolCount": 20}, 2, "20 tools"),
        ({"type": "domain", "sessionCount": 1, "confidence": 0.2}, 0, "sparse"),
        ({"type": "domain", "sessionCount": 5, "confidence": 0.5}, 4, "moderate"),
        ({"type": "domain", "sessionCount": 20, "confidence": 1.5}, 30, "strong"),
        ({"type": "entry-point", "frequency": 1}, 0, "rare"),
        ({"type": "entry-point", "frequency": 2, "confidence": 0.5}, 0, "moderate"),
        ({"type": "entry-point", "frequency": 5, "confidence": 1.5}, 0, "frequent"),
        ({"type": "recurring-pattern", "frequency": 1}, 0, "weak"),
        ({"type": "recurring-pattern", "frequency": 2}, 0, "moderate"),
        ({"type": "recurring-pattern", "frequency": 5}, 4, "connections"),
        ({"type": "tool-preference", "ratio": 0.1}, 0, "rare"),
        ({"type": "tool-preference", "ratio": 0.2}, 0, "regular"),
        ({"type": "tool-preference", "ratio": 0.5, "avgPerSession": 3}, 0, "/session"),
        ({"type": "behavioral-feature", "activation": 0.1}, 0, "weak"),
        ({"type": "behavioral-feature", "activation": -0.2}, 0, "moderate"),
        ({"type": "behavioral-feature", "activation": 0.5}, 0, "strong"),
        (
            {
                "type": "memory",
                "heat": 1,
                "importance": 1,
                "accessCount": 5,
                "lastRecallRank": 3,
            },
            0,
            "excellent",
        ),
        ({"type": "memory", "accessCount": 1, "lastRecallRank": 10}, 0, "top 10"),
        ({"type": "memory", "lastRecallRank": 20}, 0, "retrievable"),
        ({"type": "memory", "lastRecallRank": 21}, 0, "hard to find"),
        ({"type": "memory"}, 0, "not yet recall-tested"),
        ({"type": "entity", "heat": 1}, 1, "isolated"),
        ({"type": "entity", "heat": 1}, 2, "connected entity"),
        ({"type": "entity", "heat": 1}, 5, "well-connected"),
        ({"type": "discussion", "turnCount": 1}, 0, "brief"),
        ({"type": "discussion", "turnCount": 5, "toolsUsed": ["a"]}, 0, "moderate"),
        (
            {"type": "discussion", "turnCount": 20, "duration": 1_800_001},
            0,
            "long session",
        ),
        (
            {"type": "symbol", "name": "_helper", "symbol_type": "function"},
            0,
            "private",
        ),
        (
            {
                "type": "symbol",
                "name": "Public",
                "symbol_type": "class",
                "signature": "()",
            },
            3,
            "class",
        ),
        ({"type": "symbol", "name": "Public", "symbol_type": "struct"}, 10, "central"),
        ({"type": "unknown"}, 0, "unscored"),
    ],
)
def test_quality_scorer_branch_contracts(node, connections, label_fragment):
    score, label = quality._score_node(node, connections, 50)
    assert 0 <= score <= 1
    assert label_fragment in label


def test_quality_scorer_annotates_nodes_from_edges():
    nodes = [
        {"id": "a", "type": "root"},
        {"id": "b", "type": "entity", "heat": 0.5},
    ]
    quality.score_all_nodes(nodes, [{"source": "a", "target": "b"}])
    assert nodes[0]["quality"] == 1.0
    assert nodes[1]["qualityLabel"].startswith("isolated")


def test_temporal_parsing_matching_and_decay_contracts():
    assert temporal.is_temporal_query("What happened yesterday?")
    assert not temporal.is_temporal_query("What color is this?")
    hints = temporal.extract_date_hints("Met 15 March 2024 then 2024-03-16")
    assert "15 March 2024" in hints
    assert "2024-03-16" in hints
    assert temporal.compute_temporal_proximity("15 March 2024", hints) == 1.0
    assert (
        temporal.compute_temporal_proximity("March was busy", ["15 March 2024"]) == 0.5
    )
    assert temporal.compute_temporal_proximity("nothing", []) == 0.0
    assert temporal.parse_date("2024-03-15") == datetime(2024, 3, 15)
    assert temporal.parse_date("March 15, 2024") == datetime(2024, 3, 15)
    assert temporal.parse_date("created 2024-03-15 later") == datetime(2024, 3, 15)
    assert temporal.parse_date("not a date") is None
    assert temporal.normalize_date_to_iso("2024-03-15") == "2024-03-15T00:00:00"
    assert (
        temporal.normalize_date_to_iso("2024-03-15T12:00:00") == "2024-03-15T12:00:00"
    )
    assert temporal.normalize_date_to_iso("") is None
    assert temporal.compute_date_distance_score("", ["2024-03-15"]) == 0.0
    score = temporal.compute_date_distance_score("2024-03-20", ["2024-03-15"])
    assert score == pytest.approx(math.exp(-5 / 14))


def test_recency_boost_boundaries():
    now = datetime.now(timezone.utc)
    assert temporal.compute_recency_boost(None) == 0.0
    assert temporal.compute_recency_boost("bad") == 0.0
    assert temporal.compute_recency_boost(123) == 0.0
    assert temporal.compute_recency_boost(now + timedelta(days=1)) == 0.0
    assert temporal.compute_recency_boost(now - timedelta(days=91)) == 0.0
    assert temporal.compute_recency_boost(now - timedelta(days=30)) == pytest.approx(
        0.075, rel=1e-5
    )
