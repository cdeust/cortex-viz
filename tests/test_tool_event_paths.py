"""``load_tool_events`` path extraction, per tool.

Edit/Write/Read name a file directly; Grep/Glob name only the search ROOT,
so the directory becomes the file node (the exact matched files arrive
separately via JSONL tool_uses). Both root patterns were compiled but never
consulted — Grep/Glob events bucketed under ``file_path=None`` while the
module comment claimed the root was extracted (CodeQL py/unused-global-
variable #143/#144, 2026-07-28).

No live PG: a fake store serves canned memory rows. ``_tool_from_tags`` is
the REAL one, so these exercise the whole ``tool:grep`` tag → ``"Grep"`` →
pattern chain rather than assuming the tool label the table is keyed on.
"""

from __future__ import annotations

from cortex_viz.infrastructure.workflow_graph_source import _tool_from_tags
from cortex_viz.infrastructure.workflow_graph_source_pg import load_tool_events


class _FakeStore:
    def __init__(self, rows: list[dict]):
        self._rows = rows

    def search_by_tag_vector(self, **kwargs):
        return self._rows


def _row(tool: str, content: str) -> dict:
    return {
        "tags": [f"tool:{tool}"],
        "domain": "proj",
        "directory_context": None,
        "content": content,
        "created_at": "2026-07-28T10:00:00",
    }


def _paths(rows: list[dict]) -> dict[str, str | None]:
    """Return {tool: file_path} for one event per tool."""
    events = load_tool_events(
        _FakeStore(rows),
        _tool_from_tags,
        lambda _d: None,
        lambda c: "hash",
        lambda c: c,
    )
    return {e["tool"]: e["file_path"] for e in events}


def test_grep_body_yields_its_search_root():
    got = _paths([_row("grep", "**Grep:** `def foo` in `/src/core`")])
    assert got["Grep"] == "/src/core"


def test_glob_body_yields_its_root():
    got = _paths([_row("glob", "**Glob:** `*.py` (root=`/src/infra`)")])
    assert got["Glob"] == "/src/infra"


def test_read_body_still_yields_the_named_file():
    got = _paths([_row("read", "**Read:** `/src/core/thing.py`")])
    assert got["Read"] == "/src/core/thing.py"


def test_tool_with_no_path_pattern_contributes_no_path():
    """Bash is absent from the table — it must not borrow another pattern."""
    got = _paths([_row("bash", "**Command:** `ls /src/core`")])
    assert got["Bash"] is None


def test_unparseable_grep_body_yields_no_path():
    """The failure arm: a body that does not match must bucket as None."""
    got = _paths([_row("grep", "no structured grep marker here")])
    assert got["Grep"] is None


def test_each_tool_buckets_under_its_own_root():
    rows = [
        _row("grep", "**Grep:** `x` in `/a`"),
        _row("glob", "**Glob:** `*.py` (root=`/b`)"),
        _row("read", "**Read:** `/c/f.py`"),
    ]
    assert _paths(rows) == {"Grep": "/a", "Glob": "/b", "Read": "/c/f.py"}
