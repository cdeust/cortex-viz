"""Tests for the shared record parsers of the workflow-graph builders.

``_require`` and ``_as_tool`` sit at the trust boundary where raw ingest
records become typed graph input: every ``_ingest_*`` / ``ingest_*`` helper
in ``workflow_graph_builder_ingest`` and ``workflow_graph_builder_relational``
funnels its record fields through them. Both raise on bad input and neither
arm had a test, so a parser that silently accepted a malformed record — or
stopped rejecting an unknown tool — would not have failed the suite.

The identity test at the bottom pins the de-duplication: ``_relational``
carried its own linear-scan copy of ``_as_tool`` until it was replaced by an
import of the canonical one. A re-introduced local copy fails that test.
"""

from __future__ import annotations

import pytest

from cortex_viz.core import workflow_graph_builder_relational as relational
from cortex_viz.core.workflow_graph_builder_ingest import _as_tool, _require
from cortex_viz.core.workflow_graph_schema import ToolKind


# ── _as_tool ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("tool", list(ToolKind))
def test_as_tool_resolves_every_member_by_exact_value(tool: ToolKind) -> None:
    """Every declared ToolKind round-trips through its own wire value."""
    assert _as_tool(tool.value) is tool


@pytest.mark.parametrize("tool", list(ToolKind))
def test_as_tool_is_case_insensitive(tool: ToolKind) -> None:
    """Transcripts spell tools inconsistently; casing must not decide."""
    assert _as_tool(tool.value.lower()) is tool
    assert _as_tool(tool.value.upper()) is tool


def test_as_tool_rejects_unknown_name_and_names_it() -> None:
    """An unknown tool raises, and the message carries the offending name
    so the failure is actionable from the log alone."""
    with pytest.raises(ValueError, match="unknown ToolKind"):
        _as_tool("Nope")
    with pytest.raises(ValueError, match="Nope"):
        _as_tool("Nope")


def test_as_tool_rejects_empty_name() -> None:
    """The empty string is not a silent match for any member."""
    with pytest.raises(ValueError, match="unknown ToolKind"):
        _as_tool("")


def test_as_tool_does_not_match_on_substring() -> None:
    """Resolution is exact-or-casefold, never prefix/substring — 'Read'
    must not be reachable from 'Reader' or 'Rea'."""
    for name in ("Rea", "Reader", "Bash ", " Bash"):
        with pytest.raises(ValueError, match="unknown ToolKind"):
            _as_tool(name)


# ── _require ─────────────────────────────────────────────────────────


def test_require_returns_present_value() -> None:
    assert _require({"tool": "Edit"}, "tool", "tool_event") == "Edit"


def test_require_returns_falsy_but_present_values() -> None:
    """0, "" and False are legitimate record values; only absence and
    None are errors. A truthiness check here would drop real data."""
    rec = {"count": 0, "label": "", "flag": False}
    assert _require(rec, "count", "ctx") == 0
    assert _require(rec, "label", "ctx") == ""
    assert _require(rec, "flag", "ctx") is False


def test_require_raises_on_missing_key_with_context() -> None:
    """The raise names both the context and the key, which is the only
    thing distinguishing one malformed-record failure from another."""
    with pytest.raises(ValueError, match="tool_event: missing key 'tool'"):
        _require({}, "tool", "tool_event")


def test_require_raises_on_none_value() -> None:
    """A present-but-null field is as unusable as an absent one."""
    with pytest.raises(ValueError, match="missing key 'tool'"):
        _require({"tool": None}, "tool", "tool_event")


# ── de-duplication guard ─────────────────────────────────────────────


def test_relational_shares_the_canonical_parsers() -> None:
    """``_relational`` must use the ingest parsers, not its own copies.

    Two independently-maintained parsers on the same trust boundary drift:
    the copy this replaced still did a linear scan over ToolKind after the
    canonical one had moved to a dict lookup.
    """
    assert relational._as_tool is _as_tool
    assert relational._require is _require
