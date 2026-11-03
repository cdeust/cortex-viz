"""Persistent launcher dependencies must meet the published security floors."""

import importlib.metadata
import importlib.util
import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "viz_security_launcher", ROOT / "scripts/launcher.py"
)
launcher = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(launcher)


def install_metadata(directory, name, version):
    info = directory / f"{name}-{version}.dist-info"
    info.mkdir(parents=True, exist_ok=True)
    (info / "METADATA").write_text(
        f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n"
    )
    return info


@pytest.fixture
def dependencies(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setattr(launcher, "_importable", lambda *_: True)
    monkeypatch.setattr(launcher, "_REQUIRED", [])
    return tmp_path


def test_stale_dependencies_are_refreshed_before_acceptance(dependencies, monkeypatch):
    old_certifi = install_metadata(dependencies, "certifi", "2026.5.20")
    old_pillow = install_metadata(dependencies, "pillow", "12.3.0")
    calls = []

    def update(directory, packages):
        calls.append((directory, packages))
        shutil.rmtree(old_certifi)
        shutil.rmtree(old_pillow)
        install_metadata(dependencies, "certifi", "2026.10.19")
        install_metadata(dependencies, "pillow", "13.0.0")

    monkeypatch.setattr(launcher, "_pip_install", update)
    launcher._ensure_deps(str(dependencies))
    assert calls == [(str(dependencies), ["certifi>=2026.10.19", "Pillow>=13.0.0"])]


@pytest.mark.parametrize("version", ["2026.10.19", "2026.10.20"])
def test_fixed_versions_are_accepted_without_reinstallation(
    dependencies, monkeypatch, version
):
    install_metadata(dependencies, "certifi", version)
    install_metadata(dependencies, "pillow", "13.0.0")
    monkeypatch.setattr(
        launcher, "_pip_install", lambda *_: pytest.fail("unexpected installation")
    )
    launcher._ensure_deps(str(dependencies))


def test_failed_or_ineffective_installation_is_not_accepted(dependencies, monkeypatch):
    install_metadata(dependencies, "certifi", "2026.5.20")
    install_metadata(dependencies, "pillow", "13.0.0")
    monkeypatch.setattr(launcher, "_pip_install", lambda *_: None)
    with pytest.raises(RuntimeError, match=r"certifi>=2026\.10\.19"):
        launcher._ensure_deps(str(dependencies))


def test_staged_install_replaces_only_obsolete_security_metadata(
    dependencies, monkeypatch
):
    old = install_metadata(dependencies, "certifi", "2026.5.20")
    unrelated = install_metadata(dependencies, "other", "1.0")

    def pip(command, **_kwargs):
        staging = Path(command[command.index("--target") + 1])
        install_metadata(staging, "certifi", "2026.10.19")
        (staging / "certifi").mkdir()
        (staging / "certifi" / "__init__.py").write_text("")
        return type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(launcher.subprocess, "run", pip)
    launcher._pip_install(str(dependencies), ["certifi>=2026.10.19"])
    assert not old.exists()
    assert unrelated.exists()
    assert importlib.metadata.version("certifi") == "2026.10.19"


def test_failed_pip_keeps_old_metadata_and_fails_closed(
    dependencies, monkeypatch, capsys
):
    old = install_metadata(dependencies, "certifi", "2026.5.20")
    install_metadata(dependencies, "pillow", "13.0.0")
    result = type("Result", (), {"returncode": 1, "stdout": "", "stderr": "offline"})()
    monkeypatch.setattr(launcher.subprocess, "run", lambda *_a, **_kw: result)
    with pytest.raises(RuntimeError, match=r"certifi>=2026\.10\.19"):
        launcher._ensure_deps(str(dependencies))
    assert old.exists()
    assert "dependency install failed" in capsys.readouterr().err
    assert not list(dependencies.parent.glob(dependencies.name + ".tmp-*"))


def test_absent_optional_pillow_is_not_installed(dependencies, monkeypatch):
    install_metadata(dependencies, "certifi", "2026.10.19")
    distribution = importlib.metadata.distribution

    def without_pillow(name):
        if name.lower() == "pillow":
            raise importlib.metadata.PackageNotFoundError(name)
        return distribution(name)

    monkeypatch.setattr(importlib.metadata, "distribution", without_pillow)
    monkeypatch.setattr(importlib.util, "find_spec", lambda _: None)
    monkeypatch.setattr(
        launcher, "_pip_install", lambda *_: pytest.fail("optional package installed")
    )
    launcher._ensure_deps(str(dependencies))


def test_python310_fails_before_any_dependency_install(monkeypatch, capsys):
    monkeypatch.setattr(launcher.sys, "version_info", (3, 10, 19))
    monkeypatch.setattr(
        launcher, "_ensure_deps", lambda *_: pytest.fail("unsupported interpreter used")
    )
    with pytest.raises(SystemExit) as result:
        launcher.main()
    assert result.value.code == 1
    assert "Python 3.11 or newer" in capsys.readouterr().err


def test_fixed_metadata_first_does_not_hide_a_stale_duplicate(
    dependencies, monkeypatch
):
    fixed = install_metadata(dependencies, "certifi", "2026.10.19")
    install_metadata(dependencies, "certifi", "2026.5.20")
    install_metadata(dependencies, "pillow", "13.0.0")
    original = importlib.metadata.distribution

    def fixed_first(name):
        if name.lower() == "certifi":
            return importlib.metadata.PathDistribution(fixed)
        return original(name)

    monkeypatch.setattr(importlib.metadata, "distribution", fixed_first)
    assert importlib.metadata.version("certifi") == "2026.10.19"
    calls = []
    monkeypatch.setattr(
        launcher, "_pip_install", lambda _path, specs: calls.append(specs)
    )
    with pytest.raises(RuntimeError, match=r"certifi>=2026\.10\.19"):
        launcher._ensure_deps(str(dependencies))
    assert calls == [["certifi>=2026.10.19"]]


def test_concurrent_obsolete_metadata_removal_is_idempotent(dependencies, monkeypatch):
    missing = dependencies / "certifi-2026.5.20.dist-info"
    staging = dependencies.parent / "complete-staging"
    install_metadata(staging, "certifi", "2026.10.19")
    monkeypatch.setattr(launcher, "_obsolete_security_metadata", lambda *_: [missing])
    launcher._publish_dependencies(str(dependencies), str(staging))
    assert (dependencies / "certifi-2026.10.19.dist-info").is_dir()
