"""Unit tests for the D10 checkout-scope discriminant.

``resolve_instance_scope`` distinguishes two viz server processes serving
different checkouts of this repo (e.g. the main clone vs a git worktree) —
the discriminant ``workflow_graph_snapshot`` scoping depends on.
"""

from __future__ import annotations

from pathlib import Path

import cortex_viz
from cortex_viz.shared.instance_scope import FALLBACK_SCOPE, resolve_instance_scope

PKG_DIR = Path(cortex_viz.__file__).resolve().parent


def test_resolves_to_the_checkout_root_owning_the_running_package(monkeypatch):
    """With no ``CLAUDE_PLUGIN_ROOT``, the scope is the directory one level
    above the ``cortex_viz`` package — the git checkout root in this dev
    layout (the pre-D12 behaviour, preserved for worktree dev servers)."""
    monkeypatch.delenv("CLAUDE_PLUGIN_ROOT", raising=False)
    assert resolve_instance_scope() == str(PKG_DIR.parent)


def test_plugin_root_wins_when_it_owns_the_package(tmp_path, monkeypatch):
    """D12: an install whose ``cortex_viz`` is a SYMLINK to the working repo
    (the dev montage) must still scope to the install root.

    Without this, CPython resolves the package symlink into ``__file__`` and
    the same install reports the repo path once mounted — orphaning every
    snapshot it wrote while unmounted (observed 2026-07-28).
    """
    install_root = tmp_path / "cache" / "cortex-viz" / "2.7.1"
    install_root.mkdir(parents=True)
    (install_root / "cortex_viz").symlink_to(PKG_DIR, target_is_directory=True)
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(install_root))

    assert resolve_instance_scope() == str(install_root)
    # The bug this pins: the resolved package path is NOT the install root.
    assert str(PKG_DIR.parent) != str(install_root)


def test_mounted_and_unmounted_installs_agree_on_scope(tmp_path, monkeypatch):
    """The same install root yields one scope whether the package is reached
    through a symlink (mounted) or sits there directly (unmounted)."""
    mounted = tmp_path / "mounted"
    mounted.mkdir()
    (mounted / "cortex_viz").symlink_to(PKG_DIR, target_is_directory=True)

    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(mounted))
    via_symlink = resolve_instance_scope()
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(PKG_DIR.parent))
    via_real_dir = resolve_instance_scope()

    assert via_symlink == str(mounted)
    assert via_real_dir == str(PKG_DIR.parent)


def test_plugin_root_ignored_when_it_does_not_own_the_package(tmp_path, monkeypatch):
    """A ``CLAUDE_PLUGIN_ROOT`` from a DIFFERENT plugin must not be trusted —
    it would scope this process to an install that does not contain it."""
    other_plugin = tmp_path / "some-other-plugin" / "1.0.0"
    other_plugin.mkdir(parents=True)
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(other_plugin))

    assert resolve_instance_scope() == str(PKG_DIR.parent)


def test_plugin_root_ignored_when_symlink_points_elsewhere(tmp_path, monkeypatch):
    """An install whose ``cortex_viz`` resolves to a DIFFERENT package
    directory is not the owner of this process."""
    decoy_pkg = tmp_path / "decoy" / "cortex_viz"
    decoy_pkg.mkdir(parents=True)
    install_root = tmp_path / "install"
    install_root.mkdir()
    (install_root / "cortex_viz").symlink_to(decoy_pkg, target_is_directory=True)
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(install_root))

    assert resolve_instance_scope() == str(PKG_DIR.parent)


def test_empty_plugin_root_falls_back_to_package_path(monkeypatch):
    """An empty env var is absence, not an install root."""
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", "")
    assert resolve_instance_scope() == str(PKG_DIR.parent)


def test_unreadable_plugin_root_does_not_raise(tmp_path, monkeypatch):
    """A non-existent ``CLAUDE_PLUGIN_ROOT`` degrades quietly."""
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(tmp_path / "nope" / "gone"))
    assert resolve_instance_scope() == str(PKG_DIR.parent)


def test_stable_across_repeated_calls():
    """Pure function: same process, same answer every time."""
    assert resolve_instance_scope() == resolve_instance_scope()


def test_falls_back_when_package_cannot_self_locate():
    """A ``cortex_viz`` module without ``__file__`` (e.g. a frozen import)
    degrades to the documented fallback instead of raising.

    ``resolve_instance_scope`` does its own ``import cortex_viz`` internally
    (module-local, not a top-level binding), so the substitution has to go
    through ``sys.modules`` — patching the cached module object itself.
    """
    import sys

    real_module = sys.modules["cortex_viz"]
    try:
        broken = type(sys)("cortex_viz")  # a module object with no __file__
        sys.modules["cortex_viz"] = broken
        assert resolve_instance_scope() == FALLBACK_SCOPE
    finally:
        sys.modules["cortex_viz"] = real_module
