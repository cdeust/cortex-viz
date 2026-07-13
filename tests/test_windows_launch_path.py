"""Windows-portability tests for the viz launch path (cortex-viz#13).

Run on macOS/Linux CI by mocking the Windows branch: ``os.name`` is
monkeypatched to ``"nt"`` and the ``ctypes.windll.kernel32`` surface
that ``_pid_alive_windows`` touches is faked out, so the nt code path
gets real coverage without a Windows runner. The POSIX bodies are
asserted unchanged by re-running the pre-existing behavior with
``os.name`` untouched.
"""

from __future__ import annotations

# ctypes is imported eagerly here (real, POSIX build) so the later
# ``os.name = "nt"`` monkeypatches never trigger ctypes' own nt-only
# import branch (``from _ctypes import FormatError``) against a POSIX
# ``_ctypes`` extension module.
import ctypes
import subprocess
import types

import pytest

from cortex_viz.server import http_launcher, viz_instance


class _FakeKernel32:
    """Stand-in for ``ctypes.windll.kernel32`` — records the calls
    ``_pid_alive_windows`` makes and returns scripted results.

    Each Win32 entry point is a *plain function* (not a bound method)
    assigned as an instance attribute: real ``ctypes`` function
    pointers allow ``.restype``/``.argtypes`` assignment, and bound
    methods do not, so the fake must match that shape or the
    implementation's ``kernel32.OpenProcess.restype = ...`` line
    raises ``AttributeError`` against the fake.
    """

    def __init__(self, handle: int, wait_result: int, last_error: int = 0):
        self.closed_handles: list[int] = []
        self.open_process_args: tuple | None = None

        def open_process(access, inherit, pid):  # noqa: N802 — Win32 name
            self.open_process_args = (access, inherit, pid)
            return handle

        def get_last_error():  # noqa: N802 — Win32 name
            return last_error

        def wait_for_single_object(h, timeout_ms):  # noqa: N802
            return wait_result

        def close_handle(h):  # noqa: N802 — Win32 name
            self.closed_handles.append(h)
            return True

        self.OpenProcess = open_process
        self.GetLastError = get_last_error
        self.WaitForSingleObject = wait_for_single_object
        self.CloseHandle = close_handle


def _install_fake_windll(monkeypatch, fake_kernel32: _FakeKernel32) -> None:
    """Fake ``ctypes.windll`` on a non-Windows interpreter, where the
    real attribute does not exist."""
    fake_windll = types.SimpleNamespace(kernel32=fake_kernel32)
    monkeypatch.setattr(ctypes, "windll", fake_windll, raising=False)


def test_pid_alive_dispatches_to_windows_branch_when_os_name_is_nt(monkeypatch):
    """``_pid_alive`` must route to the ctypes implementation on nt and
    never touch ``os.kill`` (which is the WinError-87 bug site)."""
    monkeypatch.setattr(viz_instance.os, "name", "nt")
    fake = _FakeKernel32(handle=123, wait_result=1)  # 1 != WAIT_OBJECT_0(0)
    _install_fake_windll(monkeypatch, fake)

    def _boom(*a, **kw):
        raise AssertionError("os.kill must not be called on the nt path")

    monkeypatch.setattr(viz_instance.os, "kill", _boom)

    assert viz_instance._pid_alive(999) is True
    assert fake.open_process_args == (0x00100000, False, 999)
    assert fake.closed_handles == [123]


def test_pid_alive_windows_reports_dead_when_wait_object_0(monkeypatch):
    """WAIT_OBJECT_0 (0) from WaitForSingleObject means the process
    handle was signaled — i.e. the process already exited."""
    fake = _FakeKernel32(handle=123, wait_result=0)  # WAIT_OBJECT_0
    _install_fake_windll(monkeypatch, fake)

    assert viz_instance._pid_alive_windows(42) is False


def test_pid_alive_windows_null_handle_mirrors_posix_permission_error(monkeypatch):
    """A null handle with ERROR_ACCESS_DENIED (5) means the pid exists
    but this process lacks rights to open it — same "alive" verdict as
    the POSIX ``PermissionError`` branch."""
    fake = _FakeKernel32(handle=0, wait_result=0, last_error=5)
    _install_fake_windll(monkeypatch, fake)

    assert viz_instance._pid_alive_windows(1) is True


def test_pid_alive_windows_null_handle_other_error_means_dead(monkeypatch):
    """A null handle with any error other than ACCESS_DENIED (e.g. the
    pid does not exist) means dead."""
    fake = _FakeKernel32(handle=0, wait_result=0, last_error=87)
    _install_fake_windll(monkeypatch, fake)

    assert viz_instance._pid_alive_windows(1) is False


def test_pid_alive_posix_path_unchanged_when_os_name_is_posix(monkeypatch):
    """Non-nt path still uses ``os.kill`` — the pre-existing POSIX
    body is untouched by the platform branch."""
    calls: list[tuple] = []

    def _fake_kill(pid, sig):
        calls.append((pid, sig))
        raise ProcessLookupError

    monkeypatch.setattr(viz_instance.os, "kill", _fake_kill)
    assert viz_instance._pid_alive(4242) is False
    assert calls == [(4242, 0)]


def test_pids_on_port_windows_parses_netstat_listening_rows(monkeypatch):
    monkeypatch.setattr(viz_instance.os, "name", "nt")
    netstat_output = (
        "\n"
        "Active Connections\n"
        "\n"
        "  Proto  Local Address          Foreign Address        State           PID\n"
        "  TCP    127.0.0.1:3458         0.0.0.0:0              LISTENING       4242\n"
        "  TCP    127.0.0.1:3459         0.0.0.0:0              LISTENING       9999\n"
        "  TCP    127.0.0.1:3458         10.0.0.5:51000         ESTABLISHED     1234\n"
    )

    def _fake_check_output(cmd, **kwargs):
        assert cmd == ["netstat", "-ano", "-p", "tcp"]
        return netstat_output.encode()

    monkeypatch.setattr(subprocess, "check_output", _fake_check_output)

    assert viz_instance.pids_on_port(3458) == [4242]


def test_pids_on_port_windows_empty_on_tool_failure(monkeypatch):
    monkeypatch.setattr(viz_instance.os, "name", "nt")

    def _fake_check_output(cmd, **kwargs):
        raise FileNotFoundError("netstat not found")

    monkeypatch.setattr(subprocess, "check_output", _fake_check_output)

    assert viz_instance.pids_on_port(3458) == []


def test_pids_on_port_posix_path_still_uses_lsof(monkeypatch):
    calls: list[list[str]] = []

    def _fake_check_output(cmd, **kwargs):
        calls.append(cmd)
        return b"111\n222\n"

    monkeypatch.setattr(subprocess, "check_output", _fake_check_output)

    assert viz_instance.pids_on_port(3458) == [111, 222]
    assert calls == [["lsof", "-t", "-i", ":3458"]]


def test_open_in_browser_uses_startfile_on_windows(monkeypatch):
    """On nt, ``open_in_browser`` must call ``os.startfile`` and never
    reach the ``open``/``xdg-open`` subprocess branches (the silent
    no-tab bug from cortex-viz#13)."""
    monkeypatch.setattr(http_launcher.os, "name", "nt")
    calls: list[str] = []
    monkeypatch.setattr(
        http_launcher.os, "startfile", lambda url: calls.append(url), raising=False
    )

    def _boom(*a, **kw):
        raise AssertionError("subprocess.Popen must not run on the nt path")

    monkeypatch.setattr(http_launcher.subprocess, "Popen", _boom)

    http_launcher.open_in_browser("http://127.0.0.1:3458/")

    assert calls == ["http://127.0.0.1:3458/"]


def test_open_in_browser_startfile_failure_is_swallowed(monkeypatch):
    """Best-effort: ``os.startfile`` raising OSError must not propagate."""
    monkeypatch.setattr(http_launcher.os, "name", "nt")

    def _raise(url):
        raise OSError("no association")

    monkeypatch.setattr(http_launcher.os, "startfile", _raise, raising=False)

    http_launcher.open_in_browser("http://127.0.0.1:3458/")  # must not raise


def test_open_in_browser_posix_path_unchanged(monkeypatch):
    """Non-nt path still tries ``open`` via subprocess — unaffected by
    the new nt branch."""
    calls: list[list[str]] = []

    def _fake_popen(cmd, **kwargs):
        calls.append(cmd)
        return None

    monkeypatch.setattr(http_launcher.subprocess, "Popen", _fake_popen)

    http_launcher.open_in_browser("http://127.0.0.1:3458/")

    assert calls == [["open", "http://127.0.0.1:3458/"]]


def test_open_in_browser_rejects_non_localhost_url_on_windows(monkeypatch):
    """The allowlist check runs before the platform branch on every
    platform — nt must not bypass it."""
    monkeypatch.setattr(http_launcher.os, "name", "nt")
    calls: list[str] = []
    monkeypatch.setattr(
        http_launcher.os, "startfile", lambda url: calls.append(url), raising=False
    )

    http_launcher.open_in_browser("http://evil.example.com/")

    assert calls == []


@pytest.mark.parametrize("port", [3458])
def test_kill_port_delegates_to_pids_on_port(monkeypatch, port):
    """``_kill_port`` no longer inlines ``lsof`` — it must route
    through the cross-platform ``viz_instance.pids_on_port``."""
    killed: list[int] = []
    monkeypatch.setattr(viz_instance, "pids_on_port", lambda p: [111, 222])
    monkeypatch.setattr(viz_instance, "kill_and_wait", lambda pid: killed.append(pid))

    http_launcher._kill_port(port)

    assert killed == [111, 222]
