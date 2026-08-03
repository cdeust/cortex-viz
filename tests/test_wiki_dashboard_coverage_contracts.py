"""Behavioral contracts for generated per-project wiki dashboards."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from cortex_viz.core import wiki_coverage_dashboard as dashboard
from cortex_viz.shared import domain_mapping


def scope(name, title, *, covered, anchor=None, count=0):
    definition = SimpleNamespace(
        name=name,
        title=title,
        description=f"Describe {title}",
    )
    return SimpleNamespace(
        scope=definition,
        covered=covered,
        anchor_page=anchor,
        suggested_path=f"reference/project/{name}.md",
        page_count=count,
    )


def test_scope_statuses_are_typed(monkeypatch):
    monkeypatch.setattr(
        dashboard,
        "audit_domain",
        lambda _root, _domain: SimpleNamespace(
            scopes=[scope("architecture", "Architecture", covered=True, count=2)]
        ),
    )
    statuses = dashboard._scope_slot_statuses("/wiki", "project")
    assert statuses == [
        dashboard.SlotStatus(
            scope_name="architecture",
            title="Architecture",
            description="Describe Architecture",
            covered=True,
            anchor_path=None,
            suggested_path="reference/project/architecture.md",
            pages_count=2,
        )
    ]


def test_count_curation_gaps_handles_frontmatter_and_read_errors(tmp_path, monkeypatch):
    assert dashboard._count_curation_gaps_under(tmp_path / "missing") == (0, 0)
    domain = tmp_path / "domain"
    domain.mkdir()
    (domain / "good.md").write_text(
        "---\ntitle: Good\ncuration_gaps:\n  - first\n  - second\nkind: note\n---\nBody"
    )
    (domain / "plain.md").write_text("No frontmatter")
    (domain / "broken.md").write_text("---\ncuration_gaps:\n  - open")
    denied = domain / "denied.md"
    denied.write_text("---\n---")
    original = Path.read_text

    def read(path, *args, **kwargs):
        if path.name == "denied.md":
            raise OSError("denied")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read)
    assert dashboard._count_curation_gaps_under(domain) == (4, 2)


def test_kind_page_counts_ignores_private_and_missing_domains(tmp_path):
    for kind in ("reference", "how-to", "_dashboards", ".hidden", "notes"):
        (tmp_path / kind).mkdir()
    (tmp_path / "reference" / "project").mkdir()
    (tmp_path / "reference" / "project" / "one.md").write_text("x")
    (tmp_path / "reference" / "project" / "nested").mkdir()
    (tmp_path / "reference" / "project" / "nested" / "two.md").write_text("x")
    (tmp_path / "how-to" / "other").mkdir()
    assert dashboard._kind_page_counts(tmp_path, "project") == {"reference": 2}


def test_render_dashboard_surfaces_slots_files_gaps_and_kinds(tmp_path, monkeypatch):
    reference = tmp_path / "reference" / "project"
    reference.mkdir(parents=True)
    (reference / "architecture.md").write_text(
        "---\ncuration_gaps:\n  - verify boundary\n---\n# Architecture"
    )
    notes = tmp_path / "notes" / "project"
    notes.mkdir(parents=True)
    (notes / "decision.md").write_text("---\n---\n# Decision")
    hidden = tmp_path / "_dashboards"
    hidden.mkdir()

    monkeypatch.setattr(
        dashboard,
        "_scope_slot_statuses",
        lambda _root, _domain: [
            dashboard.SlotStatus(
                "architecture",
                "Architecture",
                "Architecture description",
                True,
                "reference/project/architecture.md",
                "reference/project/architecture.md",
                1,
            ),
            dashboard.SlotStatus(
                "security",
                "Security",
                "Security description",
                False,
                None,
                "reference/project/security.md",
                0,
            ),
        ],
    )
    monkeypatch.setattr(
        dashboard,
        "audit_files",
        lambda _root, _domain: SimpleNamespace(
            source_root="/src",
            source_file_count=40,
            covered_file_count=8,
            uncovered_files=[f"pkg/file_{i}.py" for i in range(35)],
        ),
    )
    rendered = dashboard.render_dashboard(str(tmp_path), "project")
    assert "Canonical slots filled:** 1/2 (50%)" in rendered
    assert "Source files referenced somewhere:** 8/40 (20%)" in rendered
    assert "Open curation gaps awaiting LLM authoring:** 1" in rendered
    assert "✅ filled" in rendered
    assert "missing — queued" in rendered
    assert "Security description" in rendered
    assert "| reference | 1 |" in rendered
    assert "pkg/file_0.py" in rendered
    assert "… +5 more" in rendered


def test_render_dashboard_handles_empty_slots_and_no_source_root(tmp_path, monkeypatch):
    monkeypatch.setattr(dashboard, "_scope_slot_statuses", lambda *_args: [])
    monkeypatch.setattr(
        dashboard,
        "audit_files",
        lambda *_args: SimpleNamespace(
            source_root=None,
            source_file_count=0,
            covered_file_count=0,
            uncovered_files=[],
        ),
    )
    rendered = dashboard.render_dashboard(str(tmp_path), "empty")
    assert "Canonical slots filled:** 0/0 (0%)" in rendered
    assert "no source root resolved" in rendered
    assert "What's still missing" not in rendered
    assert "Pages by kind" not in rendered


def test_write_dashboards_explicit_domains_and_index(tmp_path, monkeypatch):
    monkeypatch.setattr(
        dashboard,
        "render_dashboard",
        lambda _root, domain: f"# dashboard {domain}\n",
    )
    assert dashboard.write_dashboards(tmp_path / "missing", domains=["x"]) == {}
    written = dashboard.write_dashboards(tmp_path, domains=["beta", "alpha"])
    assert set(written) == {"alpha", "beta"}
    assert (tmp_path / "_dashboards" / "alpha.md").read_text() == "# dashboard alpha\n"
    index = (tmp_path / "_dashboards" / "_index.md").read_text()
    assert index.index("alpha") < index.index("beta")


def test_write_dashboards_discovers_domains_and_degrades_on_failures(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        domain_mapping,
        "_build_registry",
        lambda: SimpleNamespace(
            repos=[
                SimpleNamespace(canonical="alpha"),
                SimpleNamespace(canonical="alpha"),
            ]
        ),
    )
    monkeypatch.setattr(dashboard, "render_dashboard", lambda *_args: "page")
    assert set(dashboard.write_dashboards(tmp_path)) == {"alpha"}

    monkeypatch.setattr(
        domain_mapping,
        "_build_registry",
        lambda: (_ for _ in ()).throw(RuntimeError("registry unavailable")),
    )
    assert dashboard.write_dashboards(tmp_path) == {}


def test_write_dashboards_skips_failed_page_and_index_writes(tmp_path, monkeypatch):
    monkeypatch.setattr(dashboard, "render_dashboard", lambda *_args: "page")
    original = Path.write_text

    def fail_selected(path, *args, **kwargs):
        if path.name in {"broken.md", "_index.md"}:
            raise OSError("readonly")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", fail_selected)
    written = dashboard.write_dashboards(tmp_path, domains=["good", "broken"])
    assert set(written) == {"good"}
