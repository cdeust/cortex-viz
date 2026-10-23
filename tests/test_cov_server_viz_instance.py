"""viz_instance registry + http_launcher behaviour (real files/processes)."""

from __future__ import annotations

import http.server
import json
import os
import signal
import subprocess
import sys
import threading
import time
import types
from pathlib import Path

import pytest

from cortex_viz.server import http_launcher as hl
from cortex_viz.server import viz_instance as vi


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    return tmp_path


@pytest.fixture
def http_server():
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield srv.server_address[1]
    srv.shutdown()
    srv.server_close()


# ── registry file ──


def test_write_then_read_instance_roundtrip(home):
    vi.write_instance(4321)
    path = vi.instance_path()
    assert path == home / ".cache" / "cortex" / "viz-server.json"
    data = json.loads(path.read_text())
    assert data["pid"] == os.getpid() and data["port"] == 4321
    inst = vi.read_instance()
    assert inst["pid"] == os.getpid() and inst["port"] == 4321
    assert not [p for p in path.parent.iterdir() if ".tmp." in p.name]


def test_write_instance_swallows_os_errors(home, monkeypatch):
    def boom(*a, **k):
        raise OSError("ro fs")

    monkeypatch.setattr(vi.tempfile, "mkstemp", boom)
    vi.write_instance(1)
    assert not vi.instance_path().exists()


@pytest.mark.parametrize(
    "content",
    [None, "not json", "{}", '{"pid": "x", "port": 1}', "[]", '{"pid": 1}'],
)
def test_read_instance_invalid_returns_none(home, content):
    p = vi.instance_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    if content is not None:
        p.write_text(content)
    assert vi.read_instance() is None


def test_read_instance_dead_pid_is_none(home):
    p = vi.instance_path()
    p.parent.mkdir(parents=True)
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait()
    p.write_text(json.dumps({"pid": child.pid, "port": 5, "started_at": 1}))
    assert vi.read_instance() is None


# ── liveness ──


def test_pid_alive_posix_variants(monkeypatch):
    assert vi._pid_alive(0) is False and vi._pid_alive(-3) is False
    assert vi._pid_alive(os.getpid()) is True
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait(5)
    # reaped child: process lookup fails
    assert vi._pid_alive(child.pid) is False

    def perm(pid, sig):
        raise PermissionError

    monkeypatch.setattr(vi.os, "kill", perm)
    assert vi._pid_alive(99999) is True


def test_pid_alive_reaps_zombie_children():
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    deadline = time.time() + 5
    while time.time() < deadline and vi._pid_alive(child.pid):
        time.sleep(0.05)
    assert vi._pid_alive(child.pid) is False


def test_pid_alive_nt_dispatch(monkeypatch):
    monkeypatch.setattr(vi.os, "name", "nt")
    monkeypatch.setattr(vi, "_pid_alive_windows", lambda pid: pid == 7)
    assert vi._pid_alive(7) is True and vi._pid_alive(8) is False


class _K32:
    def __init__(self, handle, wait, err):
        self._handle, self._wait, self._err = handle, wait, err
        self.closed = []
        self.OpenProcess = types.SimpleNamespace()
        self.WaitForSingleObject = types.SimpleNamespace()
        outer = self

        class OP:
            restype = None
            argtypes = None

            def __call__(self, *a):
                return outer._handle

        class WF:
            restype = None
            argtypes = None

            def __call__(self, *a):
                return outer._wait

        self.OpenProcess = OP()
        self.WaitForSingleObject = WF()

    def CloseHandle(self, h):  # noqa: N802 - mimics kernel32
        self.closed.append(h)


def _win(monkeypatch, handle, wait=0, err=0):
    import ctypes

    k = _K32(handle, wait, err)
    monkeypatch.setattr(ctypes, "WinDLL", lambda *a, **kw: k, raising=False)
    monkeypatch.setattr(ctypes, "get_last_error", lambda: err, raising=False)
    return k


def test_pid_alive_windows_paths(monkeypatch):
    k = _win(monkeypatch, handle=0, err=5)
    assert vi._pid_alive_windows(10) is True  # access denied -> exists
    _win(monkeypatch, handle=0, err=87)
    assert vi._pid_alive_windows(10) is False
    k = _win(monkeypatch, handle=11, wait=0)  # signaled -> exited
    assert vi._pid_alive_windows(10) is False and k.closed == [11]
    k = _win(monkeypatch, handle=11, wait=258)  # timeout -> running
    assert vi._pid_alive_windows(10) is True and k.closed == [11]


# ── probe / freshness ──


def test_probe_true_on_live_server_false_otherwise(http_server):
    assert vi.probe(http_server) is True
    assert vi.probe(1, timeout=0.2) is False


def test_newest_source_mtime_and_is_current(tmp_path):
    assert vi.newest_source_mtime(tmp_path) == 0.0
    (tmp_path / "mcp_server" / "__pycache__").mkdir(parents=True)
    (tmp_path / "ui").mkdir()
    a = tmp_path / "mcp_server" / "a.py"
    a.write_text("x")
    pyc = tmp_path / "mcp_server" / "a.pyc"
    pyc.write_text("x")
    cached = tmp_path / "mcp_server" / "__pycache__" / "z.py"
    cached.write_text("x")
    ui = tmp_path / "ui" / "i.js"
    ui.write_text("x")
    os.utime(a, (100, 100))
    os.utime(ui, (200, 200))
    os.utime(pyc, (900, 900))
    os.utime(cached, (900, 900))
    assert vi.newest_source_mtime(tmp_path) == 200
    assert vi.is_current({"started_at": 200}, tmp_path) is True
    assert vi.is_current({"started_at": 199.5}, tmp_path) is False
    assert vi.is_current({}, tmp_path) is False
    assert vi.is_current({"started_at": "soon"}, tmp_path) is False


def test_newest_source_mtime_skips_vanished_files(tmp_path, monkeypatch):
    (tmp_path / "ui").mkdir()
    (tmp_path / "ui" / "f.js").write_text("x")
    real = os.stat

    def flaky(p, *a, **k):
        if str(p).endswith("f.js"):
            raise OSError("gone")
        return real(p, *a, **k)

    monkeypatch.setattr(vi.os, "stat", flaky)
    assert vi.newest_source_mtime(tmp_path) == 0.0


def test_reusable_instance_decisions(home, http_server, tmp_path, monkeypatch):
    assert vi.reusable_instance(None) is None  # nothing registered
    vi.write_instance(http_server)
    assert vi.reusable_instance(None)["port"] == http_server
    src = tmp_path / "src"
    (src / "ui").mkdir(parents=True)
    f = src / "ui" / "x.js"
    f.write_text("x")
    os.utime(f, (time.time() + 1000, time.time() + 1000))
    assert vi.reusable_instance(src) is None  # source newer than the server
    os.utime(f, (1, 1))
    assert vi.reusable_instance(src)["port"] == http_server
    monkeypatch.setattr(vi, "probe", lambda port: False)
    assert vi.reusable_instance(None) is None


# ── kill_and_wait ──


def _sleeper():
    return subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])


def test_kill_and_wait_terminates_real_child():
    p = _sleeper()
    assert vi.kill_and_wait(p.pid, timeout=5) is True
    assert p.poll() is not None


def test_kill_and_wait_already_dead_returns_true():
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait(5)
    assert vi.kill_and_wait(p.pid) is True


def test_kill_and_wait_escalates_to_sigkill_on_stubborn_child():
    code = (
        "import signal,time,sys;"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN);"
        "print('ready', flush=True);time.sleep(60)"
    )
    p = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE)
    assert p.stdout.readline().strip() == b"ready"
    assert vi.kill_and_wait(p.pid, timeout=0.3) is True
    p.wait(5)
    assert vi._pid_alive(p.pid) is False


def test_kill_and_wait_signal_errors(monkeypatch):
    alive = {"v": True}
    monkeypatch.setattr(vi, "_pid_alive", lambda pid: alive["v"])

    def term_fail(pid, sig):
        alive["v"] = False
        raise ProcessLookupError

    monkeypatch.setattr(vi.os, "kill", term_fail)
    assert vi.kill_and_wait(5) is True  # died while signalling

    alive["v"] = True
    calls = []

    def kill_fail(pid, sig):
        calls.append(sig)
        if sig == signal.SIGKILL:
            raise PermissionError

    monkeypatch.setattr(vi.os, "kill", kill_fail)
    monkeypatch.setattr(vi, "_poll_until_dead", lambda pid, dl: False)
    assert vi.kill_and_wait(5, timeout=0) is False
    assert calls == [signal.SIGTERM, signal.SIGKILL]


def test_kill_and_wait_nt_branch(monkeypatch):
    state = {"alive": True}
    monkeypatch.setattr(vi.os, "name", "nt")
    monkeypatch.setattr(vi, "_pid_alive", lambda pid: state["alive"])
    sent = []
    monkeypatch.setattr(vi.os, "kill", lambda pid, sig: sent.append(sig))
    monkeypatch.setattr(vi, "_poll_until_dead", lambda pid, dl: True)
    assert vi.kill_and_wait(9) is True and sent == [signal.SIGTERM]
    monkeypatch.setattr(vi, "_poll_until_dead", lambda pid, dl: False)
    assert vi.kill_and_wait(9) is False  # still alive after the wait
    state["alive"] = True

    def fail(pid, sig):
        state["alive"] = False
        raise OSError

    monkeypatch.setattr(vi.os, "kill", fail)
    assert vi.kill_and_wait(9) is True


def test_poll_until_dead_times_out_and_succeeds(monkeypatch):
    monkeypatch.setattr(vi, "_pid_alive", lambda pid: True)
    assert vi._poll_until_dead(1, time.monotonic() + 0.15) is False
    monkeypatch.setattr(vi, "_pid_alive", lambda pid: False)
    assert vi._poll_until_dead(1, time.monotonic() + 5) is True


# ── pids on port ──


def test_pids_on_port_posix_parses_lsof(monkeypatch):
    monkeypatch.setattr(
        vi.subprocess, "check_output", lambda *a, **k: b"12\n bad\n34\n\n"
    )
    assert vi.pids_on_port(3458) == [12, 34]

    def missing(*a, **k):
        raise FileNotFoundError

    monkeypatch.setattr(vi.subprocess, "check_output", missing)
    assert vi.pids_on_port(3458) == []


def test_pids_on_port_windows_parses_netstat(monkeypatch):
    out = "\n".join(
        [
            "Active Connections",
            "  Proto  Local Address  Foreign Address  State  PID",
            "  TCP    127.0.0.1:3458    0.0.0.0:0    LISTENING    1234",
            "  TCP    127.0.0.1:34580   0.0.0.0:0    LISTENING    55",
            "  TCP    127.0.0.1:3458    1.2.3.4:5    ESTABLISHED  77",
            "  UDP    127.0.0.1:3458    *:*                       88",
            "  TCP    127.0.0.1:3458    0.0.0.0:0    LISTENING    abc",
            "  TCP    short",
        ]
    ).encode()
    monkeypatch.setattr(vi.subprocess, "check_output", lambda *a, **k: out)
    assert vi._pids_on_port_windows(3458) == [1234]
    monkeypatch.setattr(vi.os, "name", "nt")
    assert vi.pids_on_port(3458) == [1234]

    def boom(*a, **k):
        raise OSError

    monkeypatch.setattr(vi.subprocess, "check_output", boom)
    assert vi._pids_on_port_windows(3458) == []


# ── http_launcher ──


def test_kill_port_kills_each_listener(monkeypatch):
    killed = []
    monkeypatch.setattr(vi, "pids_on_port", lambda port: [3, 4])
    monkeypatch.setattr(vi, "kill_and_wait", lambda pid: killed.append(pid))
    hl._kill_port(3458)
    assert killed == [3, 4]


def _make_root(p: Path):
    (p / "mcp_server").mkdir(parents=True)
    (p / "ui").mkdir()
    (p / "ui" / "unified-viz.html").write_text("<html>")
    return p


def test_detect_dev_source_env_opt_in(tmp_path, monkeypatch):
    root = _make_root(tmp_path / "dev")
    monkeypatch.setenv("CORTEX_DEV_ROOT", str(root))
    monkeypatch.delenv("CORTEX_DEV_SOURCE_SYNC", raising=False)
    base = hl._detect_dev_source()
    assert base != root  # env ignored without the explicit opt-in
    monkeypatch.setenv("CORTEX_DEV_SOURCE_SYNC", "1")
    assert hl._detect_dev_source() == root


def test_detect_dev_source_none_when_no_markers(monkeypatch):
    monkeypatch.delenv("CORTEX_DEV_SOURCE_SYNC", raising=False)
    monkeypatch.setattr(hl, "__file__", "/nonexistent/a/b/c/x.py")
    assert hl._detect_dev_source() is None


def test_find_ap_binary(monkeypatch):
    from cortex_viz.infrastructure import ap_bridge

    monkeypatch.setenv("CORTEX_AP_COMMAND", "preset")
    assert hl._find_ap_binary() is None
    monkeypatch.delenv("CORTEX_AP_COMMAND")
    monkeypatch.setattr(ap_bridge, "_resolve_command", lambda: {"command": "/bin/ap"})
    assert hl._find_ap_binary() == "/bin/ap"
    monkeypatch.setattr(ap_bridge, "_resolve_command", lambda: {"command": None})
    assert hl._find_ap_binary() is None
    monkeypatch.setattr(ap_bridge, "_resolve_command", lambda: "str")
    assert hl._find_ap_binary() is None

    def boom():
        raise RuntimeError

    monkeypatch.setattr(ap_bridge, "_resolve_command", boom)
    assert hl._find_ap_binary() is None


def test_ensure_ap_graph_noop_without_binary(monkeypatch):
    monkeypatch.delenv("CORTEX_AP_COMMAND", raising=False)
    monkeypatch.setattr(hl, "_find_ap_binary", lambda: None)
    env = {}
    hl._ensure_ap_graph(None, env)
    assert env == {}


def test_ensure_ap_graph_uses_existing_graph_path(tmp_path, monkeypatch):
    monkeypatch.delenv("CORTEX_AP_COMMAND", raising=False)
    monkeypatch.setattr(hl, "_find_ap_binary", lambda: "/bin/ap")
    g = tmp_path / "graph"
    g.write_text("x")
    env = {"CORTEX_AP_GRAPH_PATH": str(g)}
    hl._ensure_ap_graph(None, env)
    assert json.loads(env["CORTEX_AP_COMMAND"]) == {"command": "/bin/ap", "args": []}
    assert env["CORTEX_AP_GRAPH_PATH"] == str(g)


def test_ensure_ap_graph_reuses_cached_graph(home, monkeypatch):
    monkeypatch.delenv("CORTEX_AP_COMMAND", raising=False)
    monkeypatch.setattr(hl, "_find_ap_binary", lambda: "/bin/ap")
    cache = home / ".cortex" / "ap_graph"
    cache.mkdir(parents=True)
    (cache / "graph").write_text("x")
    env = {}
    hl._ensure_ap_graph(None, env)
    assert env["CORTEX_AP_GRAPH_PATH"] == str(cache / "graph")


def test_ensure_ap_graph_starts_background_index(home, monkeypatch, tmp_path):
    monkeypatch.delenv("CORTEX_AP_COMMAND", raising=False)
    monkeypatch.setattr(hl, "_find_ap_binary", lambda: "/bin/ap")
    popen_calls = []
    monkeypatch.setattr(hl.subprocess, "Popen", lambda *a, **k: popen_calls.append(a))
    from cortex_viz.infrastructure import ap_bridge

    log = []

    class FakeBridge:
        async def analyze_codebase(self, target, output_dir, language):
            log.append((target, output_dir, language))

        async def close(self):
            log.append("closed")

    monkeypatch.setattr(ap_bridge, "APBridge", FakeBridge)
    done = threading.Event()
    real_thread = threading.Thread

    def thread(target, name, daemon):
        def run():
            target()
            done.set()

        return real_thread(target=run, name=name, daemon=daemon)

    monkeypatch.setattr(threading, "Thread", thread)
    dev = tmp_path / "devsrc"
    env = {}
    hl._ensure_ap_graph(dev, env)
    assert done.wait(5)
    assert popen_calls and popen_calls[0][0] == ["/bin/ap"]
    cache = home / ".cortex" / "ap_graph"
    assert log == [(str(dev), str(cache), "auto"), "closed"]
    assert env["CORTEX_AP_GRAPH_PATH"] == str(cache / "graph")
    assert cache.is_dir()


def test_ensure_ap_graph_spawn_failure_rolls_back(home, monkeypatch):
    monkeypatch.delenv("CORTEX_AP_COMMAND", raising=False)
    monkeypatch.setattr(hl, "_find_ap_binary", lambda: "/bin/ap")

    def boom(*a, **k):
        raise OSError("no exec")

    monkeypatch.setattr(hl.subprocess, "Popen", boom)
    env = {}
    hl._ensure_ap_graph(None, env)
    assert "CORTEX_AP_COMMAND" not in env and "CORTEX_AP_GRAPH_PATH" not in env


def test_sync_dev_source_rsync_and_copytree(tmp_path, monkeypatch):
    src = tmp_path / "src"
    (src / "mcp_server").mkdir(parents=True)
    (src / "mcp_server" / "a.py").write_text("new")
    pkg = tmp_path / "pkg"
    (pkg / "mcp_server").mkdir(parents=True)
    (pkg / "mcp_server" / "stale.py").write_text("old")
    runs = []
    monkeypatch.setattr(hl.shutil, "which", lambda n: "/usr/bin/rsync")
    monkeypatch.setattr(hl.subprocess, "run", lambda cmd, **k: runs.append(cmd))
    hl._sync_dev_source(src, pkg)
    assert runs == [
        ["/usr/bin/rsync", "-a", "--delete", f"{src}/mcp_server/", f"{pkg}/mcp_server/"]
    ]
    # fallback: copytree replaces the destination
    monkeypatch.setattr(hl.shutil, "which", lambda n: None)
    hl._sync_dev_source(src, pkg)
    assert (pkg / "mcp_server" / "a.py").read_text() == "new"
    assert not (pkg / "mcp_server" / "stale.py").exists()
    # copy failure is swallowed
    monkeypatch.setattr(
        hl.shutil, "copytree", lambda *a, **k: (_ for _ in ()).throw(OSError())
    )
    hl._sync_dev_source(src, pkg)


def test_probe_port(http_server):
    assert hl._probe_port(http_server) == f"http://127.0.0.1:{http_server}"
    assert hl._probe_port(1) is None


def _launch_env(monkeypatch, dev_src=None):
    monkeypatch.setattr(hl, "_detect_dev_source", lambda: dev_src)
    monkeypatch.setattr(hl, "_ensure_ap_graph", lambda d, e: e.setdefault("AP", "1"))


def test_launch_reuses_registered_instance_without_dev_source(monkeypatch):
    _launch_env(monkeypatch)
    monkeypatch.setattr(vi, "reusable_instance", lambda src: {"port": 9999})
    assert hl.launch_server("unified") == "http://127.0.0.1:9999"


def test_launch_with_dev_source_syncs_reuses_or_kills_stale(monkeypatch, tmp_path):
    dev = tmp_path / "dev"
    _launch_env(monkeypatch, dev)
    synced = []
    monkeypatch.setattr(hl, "_sync_dev_source", lambda s, p: synced.append((s, p)))
    monkeypatch.setattr(vi, "reusable_instance", lambda src: {"port": 1234})
    assert hl.launch_server("unified") == "http://127.0.0.1:1234"
    assert synced[0][0] == dev

    killed, ports = [], []
    monkeypatch.setattr(vi, "reusable_instance", lambda src: None)
    monkeypatch.setattr(vi, "read_instance", lambda: {"pid": 77, "port": 5})
    monkeypatch.setattr(vi, "kill_and_wait", lambda pid: killed.append(pid))
    monkeypatch.setattr(hl, "_kill_port", lambda p: ports.append(p))
    monkeypatch.setattr(hl, "_probe_port", lambda p: "http://127.0.0.1:3458")
    assert hl.launch_server("unified") == "http://127.0.0.1:3458"
    assert killed == [77] and ports == [3458]
    monkeypatch.setattr(vi, "read_instance", lambda: None)
    killed.clear()
    hl.launch_server("unified")
    assert killed == []


class _Proc:
    def __init__(self, line):
        self.stdout = types.SimpleNamespace(readline=lambda: line, close=lambda: None)


def test_launch_spawns_detached_server_and_reads_url(monkeypatch):
    _launch_env(monkeypatch)
    monkeypatch.setattr(vi, "reusable_instance", lambda src: None)
    monkeypatch.setattr(hl, "_probe_port", lambda p: None)
    monkeypatch.setenv("PYTHONPATH", "/existing")
    spawned = {}

    def popen(cmd, **kw):
        spawned["cmd"], spawned["kw"] = cmd, kw
        return _Proc(b'{"url": "http://127.0.0.1:4000"}\n')

    monkeypatch.setattr(hl.subprocess, "Popen", popen)
    assert hl.launch_server("unified") == "http://127.0.0.1:4000"
    assert spawned["cmd"][0] == sys.executable
    assert spawned["cmd"][-4:] == ["--type", "unified", "--port", "3458"]
    assert spawned["kw"]["start_new_session"] is True
    pp = spawned["kw"]["env"]["PYTHONPATH"]
    assert (
        pp.endswith(":/existing") and str(Path(hl.__file__).parent.parent.parent) in pp
    )
    assert spawned["kw"]["env"]["AP"] == "1"


def test_launch_sets_pythonpath_when_unset_and_falls_back_to_probe(monkeypatch):
    _launch_env(monkeypatch)
    monkeypatch.setattr(vi, "reusable_instance", lambda src: None)
    monkeypatch.delenv("PYTHONPATH", raising=False)
    probes = iter([None, "http://127.0.0.1:3458"])
    monkeypatch.setattr(hl, "_probe_port", lambda p: next(probes))
    seen = {}

    def popen(cmd, **kw):
        seen["env"] = kw["env"]
        return _Proc(b"garbage")

    monkeypatch.setattr(hl.subprocess, "Popen", popen)
    assert hl.launch_server("unified") == "http://127.0.0.1:3458"
    assert seen["env"]["PYTHONPATH"] == str(Path(hl.__file__).parent.parent.parent)


def test_launch_raises_when_child_never_reports(monkeypatch):
    _launch_env(monkeypatch)
    monkeypatch.setattr(vi, "reusable_instance", lambda src: None)
    monkeypatch.setattr(hl, "_probe_port", lambda p: None)
    monkeypatch.setattr(hl.subprocess, "Popen", lambda *a, **k: _Proc(b""))
    with pytest.raises(RuntimeError, match="Failed to start standalone unified"):
        hl.launch_server("unified")


def test_open_in_browser_validation_and_fallbacks(monkeypatch):
    calls = []
    monkeypatch.setattr(hl.subprocess, "Popen", lambda cmd, **k: calls.append(cmd))
    hl.open_in_browser("http://evil.example.com:80/")
    hl.open_in_browser("file:///etc/passwd")
    assert calls == []
    hl.open_in_browser("http://127.0.0.1:3458/x")
    assert calls == [["open", "http://127.0.0.1:3458/x"]]

    calls.clear()

    def no_open(cmd, **k):
        calls.append(cmd[0])
        if cmd[0] == "open":
            raise FileNotFoundError

    monkeypatch.setattr(hl.subprocess, "Popen", no_open)
    hl.open_in_browser("http://127.0.0.1:3458")
    assert calls == ["open", "xdg-open"]

    def none(cmd, **k):
        raise FileNotFoundError

    monkeypatch.setattr(hl.subprocess, "Popen", none)
    hl.open_in_browser("http://127.0.0.1:3458")  # no opener: silently ignored


def test_open_in_browser_windows_uses_startfile(monkeypatch):
    started = []
    monkeypatch.setattr(hl.os, "name", "nt")
    monkeypatch.setattr(hl.os, "startfile", lambda u: started.append(u), raising=False)
    hl.open_in_browser("http://127.0.0.1:3458")
    assert started == ["http://127.0.0.1:3458"]

    def fail(u):
        raise OSError

    monkeypatch.setattr(hl.os, "startfile", fail, raising=False)
    hl.open_in_browser("http://127.0.0.1:3458")
