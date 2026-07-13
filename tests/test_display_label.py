"""Unit tests for ``core.display_label.derive_display_label``.

Covers the UX bug where entity/wiki node labels showed a full absolute
path, a qualified name, or a raw wiki title instead of a short,
scannable display form.
"""

from __future__ import annotations

from cortex_viz.core.display_label import derive_display_label


class TestDeriveDisplayLabel:
    def test_absolute_path_takes_basename(self):
        assert (
            derive_display_label("/Users/cdeust/Developments/repo/b.json") == "b.json"
        )

    def test_relative_path_takes_last_segment(self):
        assert (
            derive_display_label("docs/groomer-scheduling.md")
            == "groomer-scheduling.md"
        )

    def test_qualified_name_takes_last_segment(self):
        assert derive_display_label("video/generate.py::Particle::alive") == "alive"

    def test_entity_type_prefix_is_stripped(self):
        assert derive_display_label("import:SwiftUI", "import") == "SwiftUI"

    def test_wiki_process_title_takes_last_qualified_segment(self):
        raw = "Process — process::tests_py/hooks/test_x.py::test_x"
        assert derive_display_label(raw) == "test_x"

    def test_prose_with_slash_and_whitespace_is_unchanged(self):
        raw = "Decision: We decided to migrate from MySQL / PostgreSQL"
        assert derive_display_label(raw) == raw

    def test_simple_name_is_unchanged(self):
        assert derive_display_label("pgvector") == "pgvector"

    def test_empty_string_is_unchanged(self):
        assert derive_display_label("") == ""

    def test_home_relative_path_trailing_slash(self):
        assert derive_display_label("~/x/plugins/") == "plugins"

    def test_tmp_path(self):
        assert derive_display_label("/tmp/c1.out") == "c1.out"
