"""Unit tests for Cortex-plugin discovery and the migration subprocess.

``find_cortex_plugin_root`` is exercised against a tmp_path fixture tree
(no real ``~/.claude`` dependency); ``run_schema_migration`` is
exercised with ``subprocess.run`` monkeypatched — no real subprocess or
plugin install is spawned. The frozen contract under test (Cortex
commit 5c931b9b): ``DATABASE_URL=<url> python3 -m mcp_server.migrate``,
run with ``cwd=<plugin_root>`` — no ``scripts/launcher.py`` hop, no
``CLAUDE_PLUGIN_ROOT`` env var.
"""

from __future__ import annotations

import sys
import subprocess

from cortex_viz.infrastructure.schema_migrate import (
    MigrationResult,
    find_cortex_plugin_root,
    run_schema_migration,
)


def _make_plugin_version(
    cache_root, version: str, *, marker: str = "mcp_server"
) -> None:
    """Create a version directory qualifying as an installed plugin.

    ``marker="mcp_server"`` creates the ``mcp_server/`` package dir;
    ``marker="manifest"`` creates ``.claude-plugin/plugin.json`` instead
    — either alone must be sufficient (see _is_installed_plugin_version).
    """
    version_dir = cache_root / version
    if marker == "mcp_server":
        (version_dir / "mcp_server").mkdir(parents=True)
    elif marker == "manifest":
        manifest_dir = version_dir / ".claude-plugin"
        manifest_dir.mkdir(parents=True)
        (manifest_dir / "plugin.json").write_text("{}")
    else:  # pragma: no cover - test-authoring guard
        raise ValueError(marker)


class TestFindCortexPluginRoot:
    def test_absent_cache_root_returns_none(self, tmp_path):
        assert find_cortex_plugin_root(tmp_path / "does-not-exist") is None

    def test_empty_cache_root_returns_none(self, tmp_path):
        cache_root = tmp_path / "cortex"
        cache_root.mkdir()
        assert find_cortex_plugin_root(cache_root) is None

    def test_version_dir_without_marker_is_ignored(self, tmp_path):
        cache_root = tmp_path / "cortex"
        (cache_root / "1.0.0").mkdir(parents=True)
        assert find_cortex_plugin_root(cache_root) is None

    def test_version_dir_with_only_scripts_launcher_is_ignored(self, tmp_path):
        """scripts/launcher.py is no longer a qualifying marker — the
        frozen contract never invokes it (see schema_migrate module
        docstring)."""
        cache_root = tmp_path / "cortex"
        scripts = cache_root / "1.0.0" / "scripts"
        scripts.mkdir(parents=True)
        (scripts / "launcher.py").write_text("# not a marker anymore\n")
        assert find_cortex_plugin_root(cache_root) is None

    def test_mcp_server_package_qualifies(self, tmp_path):
        cache_root = tmp_path / "cortex"
        _make_plugin_version(cache_root, "4.13.3", marker="mcp_server")
        found = find_cortex_plugin_root(cache_root)
        assert found == cache_root / "4.13.3"

    def test_plugin_manifest_alone_qualifies(self, tmp_path):
        cache_root = tmp_path / "cortex"
        _make_plugin_version(cache_root, "4.13.3", marker="manifest")
        found = find_cortex_plugin_root(cache_root)
        assert found == cache_root / "4.13.3"

    def test_multiple_versions_picks_highest(self, tmp_path):
        cache_root = tmp_path / "cortex"
        for v in ("4.2.0", "4.13.3", "4.9.10", "10.0.0"):
            _make_plugin_version(cache_root, v)
        found = find_cortex_plugin_root(cache_root)
        assert found == cache_root / "10.0.0"


class TestRunSchemaMigration:
    def test_plugin_not_found_short_circuits_no_subprocess(self, monkeypatch):
        called = {"run": False}

        def _fail_if_called(*a, **kw):
            called["run"] = True
            raise AssertionError("subprocess.run should not be called")

        monkeypatch.setattr(subprocess, "run", _fail_if_called)

        from cortex_viz.infrastructure import schema_migrate

        monkeypatch.setattr(schema_migrate, "find_cortex_plugin_root", lambda: None)
        result = schema_migrate.run_schema_migration("postgresql://x")
        assert called["run"] is False
        assert result == MigrationResult(
            plugin_found=False, exit_code=None, stderr="", timed_out=False
        )

    def test_success_exit_zero_uses_frozen_argv_and_env(self, tmp_path, monkeypatch):
        cache_root = tmp_path / "cortex"
        _make_plugin_version(cache_root, "4.13.3")
        plugin_root = cache_root / "4.13.3"
        captured = {}

        def _fake_run(cmd, cwd, env, capture_output, text, timeout):
            captured["cmd"] = cmd
            captured["cwd"] = cwd
            captured["env"] = env
            return subprocess.CompletedProcess(cmd, returncode=0, stdout="", stderr="")

        monkeypatch.setattr(subprocess, "run", _fake_run)
        result = run_schema_migration("postgresql://x", plugin_root=plugin_root)

        assert result == MigrationResult(
            plugin_found=True, exit_code=0, stderr="", timed_out=False
        )
        # Frozen contract (Cortex commit 5c931b9b): the FULL argv, not
        # just the last token — no scripts/launcher.py hop.
        assert captured["cmd"] == [sys.executable, "-m", "mcp_server.migrate"]
        assert "launcher.py" not in " ".join(captured["cmd"])
        assert captured["cwd"] == str(plugin_root)
        assert captured["env"]["DATABASE_URL"] == "postgresql://x"
        assert "CLAUDE_PLUGIN_ROOT" not in captured["env"]

    def test_nonzero_exit_carries_stderr(self, tmp_path, monkeypatch):
        cache_root = tmp_path / "cortex"
        _make_plugin_version(cache_root, "4.13.3")
        plugin_root = cache_root / "4.13.3"

        def _fake_run(cmd, cwd, env, capture_output, text, timeout):
            return subprocess.CompletedProcess(
                cmd,
                returncode=1,
                stdout="",
                stderr="ModuleNotFoundError: mcp_server.migrate",
            )

        monkeypatch.setattr(subprocess, "run", _fake_run)
        result = run_schema_migration("postgresql://x", plugin_root=plugin_root)
        assert result.plugin_found is True
        assert result.exit_code == 1
        assert "ModuleNotFoundError" in result.stderr
        assert result.timed_out is False

    def test_timeout_reports_timed_out(self, tmp_path, monkeypatch):
        cache_root = tmp_path / "cortex"
        _make_plugin_version(cache_root, "4.13.3")
        plugin_root = cache_root / "4.13.3"

        def _fake_run(cmd, cwd, env, capture_output, text, timeout):
            raise subprocess.TimeoutExpired(cmd, timeout)

        monkeypatch.setattr(subprocess, "run", _fake_run)
        result = run_schema_migration(
            "postgresql://x", plugin_root=plugin_root, timeout_s=5.0
        )
        assert result.plugin_found is True
        assert result.exit_code is None
        assert result.timed_out is True
        assert "5" in result.stderr

    def test_default_discovery_path_used_when_plugin_root_omitted(self, monkeypatch):
        """When ``plugin_root`` is omitted, ``run_schema_migration`` must
        fall back to ``find_cortex_plugin_root()`` — verified by
        monkeypatching the module-level discovery function and checking
        it was consulted."""
        from cortex_viz.infrastructure import schema_migrate

        monkeypatch.setattr(schema_migrate, "find_cortex_plugin_root", lambda: None)
        result = schema_migrate.run_schema_migration("postgresql://x")
        assert result == MigrationResult(
            plugin_found=False, exit_code=None, stderr="", timed_out=False
        )
