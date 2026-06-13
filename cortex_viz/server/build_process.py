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
      └── drain thread: replays progress → _build_progress, slim deltas →
          _graph_cache + graph_event_stream (SSE), and loads the final file →
          _graph_cache. Pure I/O, never CPU-bound, so HTTP stays snappy.

The child opens its OWN MemoryReader (own pools) from the shared DATABASE_URL;
nothing live crosses the boundary.
"""

from __future__ import annotations

import multiprocessing as mp
import os
import pickle
import sys
import tempfile
import threading

_started = threading.Lock()
_spawned = False


def start_build(url: str, domain_filter: str | None) -> bool:
    """Spawn the build child + drain thread at most once."""
    global _spawned
    with _started:
        if _spawned:
            return False
        _spawned = True
    ctx = mp.get_context("spawn")
    q: mp.Queue = ctx.Queue(maxsize=4096)
    proc = ctx.Process(
        target=_worker,
        args=(q, url, domain_filter),
        name="cortex-graph-build-proc",
        daemon=True,
    )
    proc.start()
    threading.Thread(
        target=_drain, args=(q, proc), name="cortex-build-drain", daemon=True
    ).start()
    return True


def _worker(q: "mp.Queue", url: str, domain_filter: str | None) -> None:
    """Child process: run the real build, forwarding progress + small deltas
    over ``q`` and the full final graph via a temp file."""
    try:
        from cortex_viz.infrastructure.memory_read import MemoryReader
        from cortex_viz.server import http_standalone, http_standalone_graph as g

        # Resolve AP for THIS child: _auto_enable_ap() only sets CORTEX_AP_*
        # env vars (binary path discovery) — it runs in the parent main, but
        # those env vars do NOT cross the spawn boundary, so the child must
        # re-run it or the AST (L6) phase has no bridge command and hangs.
        http_standalone._auto_enable_ap()

        g._SINK_Q = q
        store = MemoryReader(url)
        g._kick_background_build(store, domain_filter)
        for t in threading.enumerate():
            if t.name == "cortex-graph-build":
                t.join()
        data = g.graph_cache_data()
        if data is not None:
            fd, path = tempfile.mkstemp(prefix="cortex-galaxy-", suffix=".pkl")
            with os.fdopen(fd, "wb") as fh:
                pickle.dump(data, fh, protocol=pickle.HIGHEST_PROTOCOL)
            q.put(("graph_file", path))
    except Exception as exc:  # pragma: no cover - defensive
        print(f"[cortex] build worker error: {exc}", file=sys.stderr)
        q.put(("progress", {"phase": "error", "message": str(exc)}))
    finally:
        q.put(("done", None))


def _drain(q: "mp.Queue", proc) -> None:
    """Server process: apply forwarded messages to in-process state."""
    from cortex_viz.server import http_standalone_graph as g

    while True:
        try:
            msg = q.get()
        except (EOFError, OSError):
            break
        kind = msg[0]
        if kind == "done":
            break
        try:
            if kind == "progress":
                g.apply_progress(msg[1])
            elif kind == "delta":
                g.apply_delta(msg[1], msg[2], msg[3])
            elif kind == "graph_file":
                path = msg[1]
                try:
                    with open(path, "rb") as fh:
                        data = pickle.load(fh)
                    g.apply_graph_replace(data)
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
