"""Unit tests for Cortex-plugin discovery and the migration subprocess.

``find_cortex_plugin_root`` is exercised against a tmp_path fixture tree
(no real ``~/.claude`` dependency); ``run_schema_migration`` is
exercised with ``subprocess.run`` monkeypatched — no real subprocess or
plugin install is spawned.
"""

from __future__ import annotations

import subprocess

from cortex_viz.infrastructure.schema_migrate import (
    MigrationResult,
    find_cortex_plugin_root,
    run_schema_migration,
)


def _make_plugin_version(cache_root, version: str) -> None:
    scripts = cache_root / version / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "launcher.py").write_text("# fake launcher\n")


class TestFindCortexPluginRoot:
    def test_absent_cache_root_returns_none(self, tmp_path):
        assert find_cortex_plugin_root(tmp_path / "does-not-exist") is None

    def test_empty_cache_root_returns_none(self, tmp_path):
        cache_root = tmp_path / "cortex"
        cache_root.mkdir()
        assert find_cortex_plugin_root(cache_root) is None

    def test_version_dir_without_launcher_is_ignored(self, tmp_path):
        cache_root = tmp_path / "cortex"
        (cache_root / "1.0.0").mkdir(parents=True)
        assert find_cortex_plugin_root(cache_root) is None

    def test_single_version_is_returned(self, tmp_path):
        cache_root = tmp_path / "cortex"
        _make_plugin_version(cache_root, "4.13.3")
        found = find_cortex_plugin_root(cache_root)
        assert found == cache_root / "4.13.3"

    def test_multiple_versions_picks_highest(self, tmp_path):
        cache_root = tmp_path / "cortex"
        for v in ("4.2.0", "4.13.3", "4.9.10", "10.0.0"):
            _make_plugin_version(cache_root, v)
        found = find_cortex_plugin_root(cache_root)
        assert found == cache_root / "10.0.0"


class TestRunSchemaMigration:
    def test_plugin_not_found_short_circuits_no_subprocess(self, tmp_path, monkeypatch):
        called = {"run": False}

        def _fail_if_called(*a, **kw):
            called["run"] = True
            raise AssertionError("subprocess.run should not be called")

        monkeypatch.setattr(subprocess, "run", _fail_if_called)
        empty_cache_root = tmp_path / "empty-cortex-cache"
        assert find_cortex_plugin_root(empty_cache_root) is None

        from cortex_viz.infrastructure import schema_migrate

        monkeypatch.setattr(schema_migrate, "find_cortex_plugin_root", lambda: None)
        result = schema_migrate.run_schema_migration("postgresql://x")
        assert called["run"] is False
        assert result == MigrationResult(
            plugin_found=False, exit_code=None, stderr="", timed_out=False
        )

    def test_success_exit_zero(self, tmp_path, monkeypatch):
        cache_root = tmp_path / "cortex"
        _make_plugin_version(cache_root, "4.13.3")
        plugin_root = cache_root / "4.13.3"

        def _fake_run(cmd, cwd, env, capture_output, text, timeout):
            assert cmd[-1] == "mcp_server.migrate"
            assert env["DATABASE_URL"] == "postgresql://x"
            return subprocess.CompletedProcess(cmd, returncode=0, stdout="", stderr="")

        monkeypatch.setattr(subprocess, "run", _fake_run)
        result = run_schema_migration("postgresql://x", plugin_root=plugin_root)
        assert result == MigrationResult(
            plugin_found=True, exit_code=0, stderr="", timed_out=False
        )

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
