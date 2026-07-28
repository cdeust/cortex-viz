"""Filesystem containment primitive — resolve a path and prove it stays under a root.

One idiom, one implementation. Every reader that turns a request-supplied
string into a filesystem path goes through here: the wiki reader
(``infrastructure.wiki_read``), the ``/shared/`` design-system reader
(``server.http_standalone_static``), the diff engine's repo resolution
(``server.git_diff_engine``) and the ``/api/file-diff`` name ladder
(``server.http_file_diff``).

Two properties this module is responsible for, in order:

1. **Correctness.** Resolve symlinks BEFORE comparing (a symlink planted
   inside the sandbox otherwise passes a textual check and dereferences
   outside it), and compare on a separator-terminated prefix so
   ``/srv/wiki-backup`` is not accepted as living under ``/srv/wiki``.
2. **Provability.** The comparison is ``str.startswith`` on the resolved
   path. That is the only containment check CodeQL's ``py/path-injection``
   models as a ``Path::SafeAccessCheck`` (see
   ``semmle/python/frameworks/Stdlib.qll`` class ``StartswithCall``, and
   ``PathInjectionQuery.qll``'s NotNormalized -> NormalizedUnchecked ->
   checked state machine). Neither ``os.path.commonpath(...) == root`` nor
   ``pathlib.Path.is_relative_to`` nor ``root in target.parents`` is
   modelled, so a guard written those ways is correct but unprovable, and
   the analyser keeps reporting the sink. Verified 2026-07-28 by running
   ``codeql database analyze ... Security/CWE-022/PathInjection.ql`` on this
   repository before and after: 10 alerts -> 0.

These functions return the resolved path (or ``None``), never a bool: the
caller must use the value this module proved, not re-derive its own.
"""

from __future__ import annotations

import os
from collections.abc import Iterable

__all__ = ["real_path", "resolve_under", "resolve_under_any"]


def real_path(path: str) -> str:
    """``~``-expand and fully canonicalize ``path`` (symlinks included).

    ``os.path.realpath`` is safe on a path that does not exist: it resolves
    every symlink it can and leaves the remainder literal. A falsy input
    resolves to the process CWD, which is why callers that must reject a
    relative or empty input have to check that BEFORE calling this.
    """
    return os.path.realpath(os.path.expanduser(path or ""))


def resolve_under(root: str, candidate: str) -> str | None:
    """``realpath(candidate)`` iff it lies strictly inside ``realpath(root)``.

    Returns ``None`` when the candidate escapes, when it IS the root (a
    sandbox root is a directory, never a servable entry), or when ``root``
    resolves to a filesystem root — a sandbox rooted at ``/`` (or at a
    Windows drive root) contains everything and is therefore not a sandbox,
    so it is refused rather than silently permitting every path.

    ``os.path.dirname(p) == p`` is true for exactly the filesystem roots,
    on every platform. It is also what lets the prefix below be built by
    plain concatenation: ``realpath`` strips a trailing separator from
    every OTHER path, so the only inputs that could produce a doubled
    separator are the ones already refused here.
    """
    root_real = real_path(root)
    if os.path.dirname(root_real) == root_real:
        return None
    prefix = root_real + os.sep
    candidate_real = real_path(candidate)
    if not candidate_real.startswith(prefix):
        return None
    return candidate_real


def resolve_under_any(roots: Iterable[str], candidate: str) -> str | None:
    """``resolve_under`` against each root in turn; first containment wins."""
    for root in roots:
        contained = resolve_under(root, candidate)
        if contained is not None:
            return contained
    return None
