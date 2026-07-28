"""Which absolute paths the visualizer may read off disk.

``/api/file-diff?name=`` and ``/api/trace/file?path=`` take a filesystem
path from the request and render that file's git diff. Before this module
existed there was no boundary at all: the absolute-path branch of
``http_file_diff._resolve_name`` handed the caller's string straight to the
diff engine, so any file inside any git repository anywhere on the machine
was readable in full through ``diff_type: "untracked"`` (which renders the
whole file as ``add`` lines). Reproduced 2026-07-28 against a throwaway repo
outside every known root: the endpoint returned its complete contents.

The server binds ``127.0.0.1`` and validates the ``Host`` header, and CORS
only reflects loopback origins — so a page on the public internet cannot
read the response. A page served from ANY other loopback port is a
same-category origin under that policy and can, which is the exposure this
module closes.

Sandbox roots — the places the graph's file nodes actually come from:

* every candidate development root (``shared.domain_mapping``), i.e. the
  parents the repo registry itself scans;
* ``~/.claude`` (``config.CLAUDE_DIR``) — Claude Code's own tree, which the
  activity spine references directly and which contains the wiki;
* the temp roots agent scratchpad sessions live under (see ``_TEMP_ROOTS``).

source: measured against the live activity spine 2026-07-28 —
``select ... from session_activity where target_kind='file'`` over all 1069
rows carrying an absolute path: 464 under ``~/Documents/Developments``, 81
under ``~/.claude``, 524 under ``/private/tmp``, **0 anywhere else**. The
sandbox therefore costs no reachable functionality while refusing
``~/.ssh``, ``~/Library``, ``/etc`` and every repository outside the
configured development roots.

Note the sandbox is not the only thing standing between a request and a
file: the diff engine additionally requires the path to resolve inside a
git repository. The sandbox's job is to bound WHICH repositories — the
user's development trees rather than every repository on the machine.
"""

from __future__ import annotations

import os
import tempfile

from cortex_viz.infrastructure.config import CLAUDE_DIR
from cortex_viz.shared.domain_mapping import candidate_dev_roots
from cortex_viz.shared.path_containment import resolve_under_any

__all__ = ["readable_roots", "resolve_readable_path"]

# Refusal reason surfaced to the client. Deliberately identical for
# "outside the sandbox" and "does not exist": a distinguishable message
# would turn the endpoint into a filesystem-existence oracle for paths the
# caller is not allowed to read.
OUTSIDE_SANDBOX = "path is outside the readable roots"

# BOTH temp roots are required, and one is not a superset of the other.
# ``tempfile.gettempdir()`` honours ``$TMPDIR`` — on macOS a per-user
# ``/var/folders/.../T`` tree, which is where pytest's ``tmp_path`` fixtures
# live. Claude Code's own session scratchpads live under the platform temp
# directory instead (``/tmp``, ``/private/tmp`` once resolved). Measured
# 2026-07-28: 524 of the 1069 graphed absolute paths are under
# ``/private/tmp/claude-<uid>`` and none under ``$TMPDIR``, so listing only
# ``gettempdir()`` would have refused every scratchpad path. A root that
# does not exist on the platform simply never matches, so listing ``/tmp``
# unconditionally is safe on Windows too.
_TEMP_ROOTS = (tempfile.gettempdir(), os.sep + "tmp")


def readable_roots() -> list[str]:
    """The roots the diff endpoints are allowed to read under.

    Recomputed per call rather than cached: ``candidate_dev_roots`` reads
    ``$CORTEX_DEV_ROOT`` and probes the filesystem, so a cache would pin
    whichever layout happened to exist at first import — the staleness bug
    that a module-level constant would introduce here.
    """
    roots = [str(root) for root in candidate_dev_roots()]
    roots.append(str(CLAUDE_DIR))
    roots.extend(_TEMP_ROOTS)
    return roots


def resolve_readable_path(path: str) -> tuple[str | None, str | None]:
    """``(resolved_path, reason)`` for a request-supplied absolute path.

    ``reason`` is set only when ``resolved_path`` is ``None``. The returned
    path is symlink-canonicalized and proven to lie under one of
    ``readable_roots`` — callers must use THAT value, not the input, or the
    containment they just paid for is discarded.
    """
    contained = resolve_under_any(readable_roots(), path)
    if contained is None:
        return None, OUTSIDE_SANDBOX
    return contained, None
