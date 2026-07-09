"""Unit tests for ``core.activity_paths`` — the P4 spine/galaxy FILE
node-id unification. Verifies ``file_target_id`` mints the SAME id
``NodeIdFactory.file_id`` mints for the equivalent absolute path (the join
key the live activity spine and the galaxy workflow graph must share), for
absolute / ``~``-prefixed / ``cwd``-relative raw inputs, and that legacy
(pre-fix) rows self-heal to that same id.
"""

from __future__ import annotations

import cortex_viz.core.activity_paths as mod
from cortex_viz.core.workflow_graph_schema import NodeIdFactory


def test_absolute_path_is_already_clean_and_matches_galaxy_hash():
    # The common case: Claude Code's own tool contract requires Read/Edit/
    # Write file_path arguments to already be absolute — canonicalization
    # must be a no-op so the hash equals what the galaxy would mint for the
    # identical tool event.
    got = mod.file_target_id("/Users/dev/repo/foo.py", cwd="/Users/dev/repo")
    expected = NodeIdFactory.file_id("/Users/dev/repo/foo.py")
    assert got == expected


def test_tilde_expands_against_home(monkeypatch):
    monkeypatch.setenv("HOME", "/Users/dev")
    got = mod.canonicalize_path("~/notes.md", cwd="/Users/dev/repo")
    assert got == "/Users/dev/notes.md"


def test_relative_path_resolves_against_cwd():
    got = mod.canonicalize_path("./src/foo.py", cwd="/Users/dev/repo")
    assert got == "/Users/dev/repo/src/foo.py"


def test_dotdot_relative_path_resolves_against_cwd():
    got = mod.canonicalize_path("../sibling/bar.py", cwd="/Users/dev/repo/sub")
    assert got == "/Users/dev/repo/sibling/bar.py"


def test_relative_path_without_cwd_stays_relative_but_normalized():
    # No cwd context available — best-effort: normalize what we have rather
    # than fabricate an absolute path out of nothing.
    got = mod.canonicalize_path("./foo/../bar.py", cwd="")
    assert got == "bar.py"


def test_redundant_slashes_and_dot_segments_collapse():
    got = mod.canonicalize_path("/Users/dev//repo/./foo.py", cwd="")
    assert got == "/Users/dev/repo/foo.py"


def test_file_target_id_for_relative_path_matches_resolved_absolute_hash():
    got = mod.file_target_id("src/foo.py", cwd="/Users/dev/repo")
    expected = NodeIdFactory.file_id("/Users/dev/repo/src/foo.py")
    assert got == expected


def test_is_canonical_file_target_id_recognizes_hash_shape():
    hashed = mod.file_target_id("/a/b.py", cwd="")
    assert mod.is_canonical_file_target_id(hashed)


def test_is_canonical_file_target_id_rejects_legacy_raw_path_shape():
    assert not mod.is_canonical_file_target_id("file:/Users/dev/repo/foo.py")
    assert not mod.is_canonical_file_target_id("file:~/notes.md")
    assert not mod.is_canonical_file_target_id("file:?")
    assert not mod.is_canonical_file_target_id("")
    assert not mod.is_canonical_file_target_id(None)


def test_canonical_file_id_for_legacy_recovers_the_same_id_a_fresh_row_would_get():
    legacy_target_id = "file:/Users/dev/repo/foo.py"  # pre-P4 raw-path row
    healed = mod.canonical_file_id_for_legacy(legacy_target_id, cwd="")
    fresh = mod.file_target_id("/Users/dev/repo/foo.py", cwd="")
    assert healed == fresh


def test_canonical_file_id_for_legacy_resolves_relative_legacy_path_via_cwd():
    legacy_target_id = "file:./src/foo.py"
    healed = mod.canonical_file_id_for_legacy(legacy_target_id, cwd="/Users/dev/repo")
    fresh = mod.file_target_id("src/foo.py", cwd="/Users/dev/repo")
    assert healed == fresh
