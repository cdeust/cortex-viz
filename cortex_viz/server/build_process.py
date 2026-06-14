"""Out-of-process galaxy build — keeps the HTTP server responsive.

The galaxy build is CPU-bound (graph assembly + igraph force-directed layout).
On a thread inside the server process it monopolises the GIL and starves the
Python HTTP-server thread — even static files stop serving until the build
finishes (verified: identical on Cortex main pre-extraction). The fix is a
separate PROCESS with its own GIL.

    server process (HTTP, responsive)
      ├── start_build(url) ──> child process: runs the real build
      │                          • small progress + BOUNDED slim deltas over a
      │                            multiprocessing.Queue (live SSE streaming)
      │                          • the FULL final graph via a temp FILE, not the
      │                            queue — piping tens of MB of pickled nodes
      │                            through a Queue chokes its feeder/drain and
      │                            re-pins a core. A file is one bulk read.
      └── drain thread: replays progress → _build_progress, full-dict deltas →
          _graph_cache + graph_event_stream (SSE), and loads the final file →
          _graph_cache. Pure I/O, never CPU-bound, so HTTP stays snappy.

The child opens its OWN MemoryReader (own pools) from the shared DATABASE_URL;
nothing live crosses the boundary.

Lamport protocol (2026-06-14):
  * Every build is an integer EPOCH. The server stamps the epoch via
    begin_epoch() before spawning; the child stamps it onto every queue
    message via http_standalone_graph._forward. The drain epoch-gates every
    message and the appliers drop stale-epoch ones — a child that outlived
    its build (roster re-kick) cannot corrupt the new build.
  * No one-shot _spawned latch: a re-kick (roster change) is allowed once the
    prior child is dead OR has been killed. kill_current_build terminates the
    current child; start_build refuses only while a LIVE child exists.
  * Watchdog: the drain uses q.get(timeout=15); on Empty it checks
    proc.is_alive() — a hung or dead child yields apply_done("killed") and the
    drain exits, so progress always reaches done-or-killed.
"""

from __future__ import annotations

import multiprocessing as mp
import os
import pickle
import queue as _queue
import sys
import tempfile
import threading

# Monotone epoch counter + the live child handle. _proc_lock guards the
# (epoch, proc) pair so start_build and kill_current_build never race.
_proc_lock = threading.Lock()
_epoch: int = 0
_proc = None  # type: ignore[assignment]


def _is_alive() -> bool:
    """True iff a build child exists and is still running. Caller holds
    _proc_lock."""
    return _proc is not None and _proc.is_alive()


def start_build(url: str, domain_filter: str | None) -> bool:
    """Spawn the build child + drain thread for a NEW epoch.

    Pre: url is a DB connection string.
    Post: returns False (no-op) if a live build child already exists;
    otherwise begin_epoch(new_epoch) has reset server state, a fresh child
    is running under that epoch, and a drain thread is replaying its
    messages. Returns True.

    Re-kick safety: unlike the old _spawned one-shot latch, a roster change
    can re-kick once the prior child is dead or killed — the only block is a
    currently-live child (its AST loop owns the build).
    """
    global _epoch, _proc
    from cortex_viz.server import graph_appliers

    with _proc_lock:
        if _is_alive():
            return False
        _epoch += 1
        epoch = _epoch
        # Single server-side reset point — publishes the empty cache + resets
        # phase/progress state BEFORE any delta of this epoch can arrive.
        graph_appliers.begin_epoch(epoch)
        ctx = mp.get_context("spawn")
        q: mp.Queue = ctx.Queue(maxsize=4096)
        proc = ctx.Process(
            target=_worker,
            args=(q, url, domain_filter, epoch),
            name="cortex-graph-build-proc",
            daemon=True,
        )
        proc.start()
        _proc = proc
    threading.Thread(
        target=_drain,
        args=(q, proc, epoch),
        name="cortex-build-drain",
        daemon=True,
    ).start()
    return True


def kill_current_build() -> None:
    """Terminate the current build child if one is alive (roster change).

    terminate → join(2s) → kill → join. The drain thread for the old epoch
    exits on its own watchdog (q.get times out, proc not alive). The next
    start_build bumps the epoch, so any late message from the killed child is
    dropped by the epoch gate even if it slips through before death.
    """
    global _proc
    with _proc_lock:
        proc = _proc
        if proc is None or not proc.is_alive():
            _proc = None
            return
    try:
        proc.terminate()
        proc.join(timeout=2)
        if proc.is_alive():
            proc.kill()
            proc.join(timeout=2)
    except Exception:  # pragma: no cover - defensive
        pass
    with _proc_lock:
        if _proc is proc:
            _proc = None


def _worker(
    q: "mp.Queue", url: str, domain_filter: str | None, epoch: int
) -> None:
    """Child process: run the real build, forwarding progress + full-dict
    deltas over ``q`` (epoch-stamped) and the full final graph via a temp
    file."""
    try:
        from cortex_viz.infrastructure.memory_read import MemoryReader
        from cortex_viz.server import (
            graph_build,
            graph_cache_state as g_state,
            http_standalone,
        )

        # Resolve AP for THIS child: _auto_enable_ap() only sets CORTEX_AP_*
        # env vars (binary path discovery) — it runs in the parent main, but
        # those env vars do NOT cross the spawn boundary, so the child must
        # re-run it or the AST (L6) phase has no bridge command and hangs.
        http_standalone._auto_enable_ap()

        # Write to the OWNER module (graph_cache_state). Writing through a
        # re-export alias would not update the owner's live global, forking
        # the cross-process state.
        g_state._SINK_Q = q
        g_state.set_build_epoch(epoch)
        store = MemoryReader(url)
        graph_build._kick_background_build(store, domain_filter)
        for t in threading.enumerate():
            if t.name == "cortex-graph-build":
                t.join()
        data = g_state.graph_cache_data()
        if data is not None:
            fd, path = tempfile.mkstemp(prefix="cortex-galaxy-", suffix=".pkl")
            with os.fdopen(fd, "wb") as fh:
                pickle.dump(data, fh, protocol=pickle.HIGHEST_PROTOCOL)
            q.put(("graph_file", epoch, path))
    except Exception as exc:  # pragma: no cover - defensive
        print(f"[cortex] build worker error: {exc}", file=sys.stderr)
        q.put(("progress", epoch, {"phase": "error", "message": str(exc)}))
        q.put(("done", epoch, "error"))
        return
    q.put(("done", epoch, "ok"))


def _drain(q: "mp.Queue", proc, epoch: int) -> None:
    """Server process: apply forwarded messages to in-process state.

    Watchdog: q.get(timeout=15) bounds the wait. On Empty, if the child is
    no longer alive we mark this epoch done("killed") and exit — a hung or
    crashed child can never wedge progress. Every message is epoch-gated:
    a message whose epoch != this drain's epoch is dropped (and its
    out-of-band graph_file unlinked) so a stale child cannot corrupt a
    newer build.
    """
    from cortex_viz.server import graph_appliers as g

    while True:
        try:
            msg = q.get(timeout=15)
        except _queue.Empty:
            # Watchdog tick — no message in 15 s. If the child has died (or
            # been killed) without a terminal "done", mark this epoch killed
            # and exit so progress never wedges. A slow-but-alive child just
            # loops back to wait again.
            if not proc.is_alive():
                g.apply_done(epoch, "killed")
                break
            continue
        except (EOFError, OSError):
            break

        kind = msg[0]
        msg_epoch = msg[1]
        stale = msg_epoch != epoch
        if kind == "done":
            if not stale:
                g.apply_done(epoch, msg[2] if len(msg) > 2 else "ok")
            break
        if stale:
            # Drop the stale message; unlink any orphan graph_file it carried.
            if kind == "graph_file":
                try:
                    os.unlink(msg[2])
                except OSError:
                    pass
            continue
        try:
            if kind == "progress":
                g.apply_progress(epoch, msg[2])
            elif kind == "delta":
                # ("delta", epoch, phase_key, stage, nodes, edges)
                g.apply_delta(epoch, msg[2], msg[3], msg[4], msg[5])
            elif kind == "phase_ready":
                # ("phase_ready", epoch, phase_key, phase_seq)
                g.apply_phase_ready(epoch, msg[2], msg[3])
            elif kind == "graph_file":
                path = msg[2]
                try:
                    with open(path, "rb") as fh:
                        data = pickle.load(fh)
                    g.apply_graph_replace(epoch, data)
                finally:
                    try:
                        os.unlink(path)
                    except OSError:
                        pass
        except Exception as exc:  # pragma: no cover - defensive
            print(f"[cortex] build drain error: {exc}", file=sys.stderr)

    try:
        proc.join(timeout=5)
    except Exception:
        pass
    # Clear the live-child handle if it is still ours, so a future roster
    # re-kick is allowed.
    with _proc_lock:
        global _proc
        if _proc is proc:
            _proc = None
