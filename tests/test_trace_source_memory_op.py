"""Unit tests for ``infrastructure.trace_source._memory_op`` — the memory-op
classifier that decides whether a transcript tool call was a Cortex
``remember`` or ``recall``.

Why this file exists: Cortex v4.15.0 renamed its plugin ``cortex`` ->
``hypermnesia-mcp`` (community-directory name collision). The host derives a
plugin-scoped MCP tool name as ``mcp__plugin_<plugin-name>_<server-key>__<tool>``
— BOTH halves are in the name — so that rename rewrote every Cortex tool name
its consumers see. The rename kept the server key ``cortex``, which is the only
reason this classifier survived it: it matches on the substring ``"cortex"``,
and ``mcp__plugin_hypermnesia-mcp_cortex__remember`` still contains it.

That survival was luck, not design, and it was undetectable: an unresolvable
or unrecognized tool name here does not raise — ``_memory_op`` returns ``None``
and the memory op is silently dropped from the trace. So the post-rename
spelling is pinned below. If a future rename moves the SERVER key, these tests
fail loudly instead of the trace quietly losing every memory node.
"""

from __future__ import annotations

import pytest

from cortex_viz.infrastructure.trace_source import _memory_op


@pytest.mark.parametrize(
    "name",
    [
        # Post-rename plugin-scoped spelling (Cortex >= 4.15.0). This is the
        # name the host actually emits today.
        "mcp__plugin_hypermnesia-mcp_cortex__remember",
        # Pre-rename spelling: still classified, so historical transcripts
        # recorded before 4.15.0 keep rendering their memory nodes.
        "mcp__plugin_cortex_cortex__remember",  # mcp-prefix-allow-legacy
        # Bare server-scoped spelling (.mcp.json style).
        "mcp__cortex__remember",
        "cortex:remember",
        # Case independence — the classifier lowercases first.
        "MCP__PLUGIN_HYPERMNESIA-MCP_CORTEX__REMEMBER",
    ],
)
def test_remember_spellings_classify_as_remember(name: str) -> None:
    assert _memory_op(name) == "remember"


@pytest.mark.parametrize(
    "name",
    [
        "mcp__plugin_hypermnesia-mcp_cortex__recall",
        "mcp__plugin_cortex_cortex__recall",  # mcp-prefix-allow-legacy
        "mcp__cortex__recall",
        "cortex:recall",
        "MCP__PLUGIN_HYPERMNESIA-MCP_CORTEX__RECALL",
    ],
)
def test_recall_spellings_classify_as_recall(name: str) -> None:
    assert _memory_op(name) == "recall"


@pytest.mark.parametrize(
    "name",
    [
        # Not a memory op, even though it is a Cortex tool.
        "mcp__plugin_hypermnesia-mcp_cortex__memory_stats",
        "mcp__plugin_hypermnesia-mcp_cortex__navigate_memory",
        # Right operation word, wrong server — must not be attributed to Cortex.
        "mcp__plugin_someone-else_other__remember",  # mcp-prefix-allow-legacy
        "mcp__plugin_someone-else_other__recall",  # mcp-prefix-allow-legacy
        # Ordinary session tools.
        "Read",
        "Bash",
        # Degenerate inputs must not raise.
        "",
    ],
)
def test_non_memory_ops_classify_as_none(name: str) -> None:
    assert _memory_op(name) is None


def test_none_input_is_tolerated() -> None:
    """The call site passes a transcript-derived name that can be absent."""
    assert _memory_op(None) is None  # type: ignore[arg-type]


def test_remember_wins_when_both_words_are_present() -> None:
    """Documents the classifier's precedence rather than leaving it implicit.

    ``remember`` is tested before ``recall``, so a name carrying both resolves
    to ``remember``. Pinned so a reordering of the two branches is a test
    failure and not a silent reclassification of trace history.
    """
    assert _memory_op("mcp__plugin_hypermnesia-mcp_cortex__remember_after_recall") == (
        "remember"
    )
