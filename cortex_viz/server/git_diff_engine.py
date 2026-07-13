"""Shared git-diff engine used by ``/api/file-diff`` and ``/api/trace/file``.

Single contract, one implementation — previously each endpoint carried its
own divergent copy (``http_file_diff._resolve_diff`` vs
``http_standalone_trace._git_history``), and the ``/api/trace/file`` copy
was missing ``--diff-merges=first-parent``: on a merge commit HEAD, plain
``git show HEAD -- rel`` renders 0 bytes for every file the merge didn't
directly conflict-resolve, which was ~99% of clicks in this repo (3/355
files touched by HEAD directly). That bug is why this module exists.

Ladder (strict order, contract A.2):
    1. uncommitted — ``git diff HEAD -- rel`` (worktree + staged + an
       uncommitted deletion, all in one call).
    2. last commit that touched the file — ``git log --follow`` to find the
       sha, then ``git show --diff-merges=first-parent`` so a merge HEAD
       still yields the real patch (the fix for the bug above). Covers a
       committed deletion (the patch is a pure-deletion diff).
    3. untracked — file on disk, not in the index → full content as adds.
    4. none — tracked, clean, no commit found. An honest terminal state,
       not "file outside this checkout".

Read-only: only ``git diff`` / ``git log`` / ``git show`` / ``git
ls-files`` / ``git rev-parse``, never anything that mutates the tree.
"""

from __future__ import annotations

import os
import subprocess

# Cap the rendered diff so a huge file (or a full-content new file) can't
# ship a multi-MB payload the modal would choke on.
# source: mirrors the galaxy's pre-existing ~2k-line diff budget (see git
# history of http_file_diff.py); larger diffs set truncated=True.
_MAX_LINES = 2000
_GIT_TIMEOUT_S = 6


def _git(root: str, args: list[str]) -> str | None:
    """Run ``git -C root <args>`` read-only; return stdout or None on failure."""
    try:
        return subprocess.check_output(
            ["git", "-C", root, *args],
            stderr=subprocess.DEVNULL,
            timeout=_GIT_TIMEOUT_S,
        ).decode("utf-8", "replace")
    except Exception:
        return None


def _expand_user(path: str) -> str:
    """Expand ``~`` in ``path`` only — no symlink resolution.

    Absoluteness must be checked against THIS (contract A.1: "is not
    absolute" is about what the caller passed in, modulo ``~``-expansion,
    not about where ``realpath`` happens to land it). ``os.path.realpath``
    on a relative path silently absolutizes it against the process CWD, so
    checking ``startswith('/')`` after realpath can never observe a
    relative input — a bare hash or a repo-relative path like
    ``src/main.py`` would pass the absoluteness gate and get resolved
    against the server's CWD instead of being rejected.
    """
    return os.path.expanduser(path or "")


def _expand_and_realpath(path: str) -> str:
    """Expand ``~`` and canonicalize symlinks in ``path``.

    ``git rev-parse --show-toplevel`` always returns a symlink-resolved
    root. Any path we later ``os.path.relpath`` against that root must be
    canonicalized the same way, or a symlinked ancestor (e.g. macOS's
    ``/tmp`` -> ``/private/tmp``) turns the relpath into a bogus
    ``../../..`` walk back out to the original absolute path — which then
    fails to match the tracked file in every downstream ``git`` call and
    falls through the ladder to the wrong ``diff_type``. ``os.path.realpath``
    is safe to call even when the path (or its parent) does not exist: it
    resolves every symlink it can and leaves the rest of the path literal.

    Callers that need the absoluteness gate (contract A.1) MUST check it
    against ``_expand_user`` first — see ``resolve_repo_root``. This
    function is only safe to call once that gate has already passed.
    """
    return os.path.realpath(_expand_user(path))


def resolve_repo_root(path: str) -> tuple[str | None, str | None]:
    """``(git_root, reason)`` for an absolute filesystem path (contract A.1).

    ``reason`` is set only when ``git_root`` is ``None``. Expands ``~``
    first and checks absoluteness on THAT (see ``_expand_user``) — the
    check must happen before ``realpath``, not after, or a relative input
    gets silently absolutized against the server's CWD and wrongly passes
    (contract A.1). If the path is still not absolute, resolution stops
    here (the caller is responsible for name resolution — see
    ``http_file_diff._resolve_name`` for the basename/relative ladder used
    by ``/api/file-diff``). Once absoluteness is confirmed, symlinks are
    canonicalized (see ``_expand_and_realpath``). If the immediate parent
    directory no longer exists (a deleted file/dir whose repo is still
    alive), walks up to the nearest existing ancestor and resolves the
    repo from there — a deleted file's history still lives in a repo
    rooted above it.
    """
    if not _expand_user(path).startswith("/"):
        return None, "path is not absolute"
    expanded = _expand_and_realpath(path)
    ancestor = os.path.dirname(expanded) or "/"
    while ancestor != "/" and not os.path.isdir(ancestor):
        ancestor = os.path.dirname(ancestor)
    if not os.path.isdir(ancestor):
        return None, "no git repository found for this path"
    out = _git(ancestor, ["rev-parse", "--show-toplevel"])
    root = out.strip() if out else ""
    if not root:
        return None, "no git repository found for this path"
    return root, None


def repo_root_and_relpath(path: str) -> tuple[str | None, str | None, str | None]:
    """``(root, rel, reason)`` — the single place that resolves a repo root
    AND the path relative to it, both derived from the same
    symlink-canonicalized input.

    This exists because the relpath computation was previously duplicated
    in ``http_standalone_trace._file_git_root_rel`` using a
    non-canonicalized path while ``resolve_repo_root`` canonicalized
    internally — the mismatch is exactly how the symlinked-ancestor bug
    got introduced twice. Every caller that needs a repo-relative path
    must go through here.
    """
    root, reason = resolve_repo_root(path)
    if root is None:
        return None, None, reason
    rel = os.path.relpath(_expand_and_realpath(path), root)
    return root, rel, None


def _is_untracked(root: str, rel: str) -> bool:
    """True when the file exists but git does not yet track it."""
    tracked = _git(root, ["ls-files", "--error-unmatch", "--", rel])
    return tracked is None or tracked.strip() == ""


def _parse_unified(diff_text: str) -> tuple[list[dict], bool]:
    """Parse ``git diff``/``git show`` unified output into {type,text} lines.

    Only emits lines once inside a hunk (after the first ``@@`` marker) —
    contract A.4. This is what keeps a ``git show`` commit-message body (or
    any other pre-hunk header text) from leaking into the rendered diff as
    fake ``context`` lines, which previously happened on 100% of
    ``last_commit`` responses.
    """
    lines: list[dict] = []
    truncated = False
    in_hunk = False
    for raw in diff_text.splitlines():
        if raw.startswith("@@"):
            in_hunk = True
        if not in_hunk:
            continue
        if len(lines) >= _MAX_LINES:
            truncated = True
            break
        if raw.startswith("@@"):
            lines.append({"type": "hunk", "text": raw})
        elif raw.startswith("+++") or raw.startswith("---"):
            continue  # file-header markers, not content
        elif raw.startswith("+"):
            lines.append({"type": "add", "text": raw[1:]})
        elif raw.startswith("-"):
            lines.append({"type": "del", "text": raw[1:]})
        elif raw.startswith(" "):
            lines.append({"type": "context", "text": raw[1:]})
        # any other in-hunk line (e.g. "\ No newline at end of file") ignored
    return lines, truncated


def _full_content_as_adds(root: str, rel: str) -> tuple[list[dict], bool]:
    """Render an untracked file's whole content as additions."""
    abs_path = os.path.join(root, rel)
    try:
        with open(abs_path, "r", encoding="utf-8", errors="replace") as fh:
            body = fh.read().splitlines()
    except OSError:
        return [], False
    truncated = len(body) > _MAX_LINES
    body = body[:_MAX_LINES]
    lines: list[dict] = [{"type": "hunk", "text": f"@@ -0,0 +1,{len(body)} @@"}]
    lines += [{"type": "add", "text": ln} for ln in body]
    return lines, truncated


def _last_commit_diff(root: str, rel: str) -> dict | None:
    """Ladder step 2 — the last commit that touched ``rel``, if any.

    ``--diff-merges=first-parent`` is load-bearing: without it, ``git show``
    on a merge commit renders 0 bytes for any file the merge itself didn't
    directly conflict-resolve (the dominant bug this module fixes — proven
    live: ``git show HEAD -- <file>`` = 0 lines, ``git log -1 -p -- <file>``
    = 258 lines, on a repo whose HEAD is a merge commit).
    """
    log_out = _git(root, ["log", "-1", "--format=%H\x1f%s", "--follow", "--", rel])
    if not log_out:
        return None
    sha, _, subject = log_out.strip("\n").partition("\x1f")
    if not sha:
        return None
    show = _git(
        root,
        [
            "show",
            sha,
            "--format=",
            "--unified=3",
            "--diff-merges=first-parent",
            "--",
            rel,
        ],
    )
    if not show or not show.strip():
        return None
    lines, trunc = _parse_unified(show)
    if not lines:
        return None
    return {
        "diff_type": "last_commit",
        "lines": lines,
        "truncated": trunc,
        "commit": {"sha": sha[:10], "subject": subject},
    }


def _untracked_diff(root: str, rel: str) -> dict | None:
    """Ladder step 3 — file on disk but not in the git index."""
    if not _is_untracked(root, rel):
        return None
    if not os.path.isfile(os.path.join(root, rel)):
        return None
    lines, trunc = _full_content_as_adds(root, rel)
    return {"diff_type": "untracked", "lines": lines, "truncated": trunc}


def _resolve_diff(root: str, rel: str) -> dict:
    """Run the strict ladder (contract A.2) and return the response dict.

    Step 1 normally diffs against ``HEAD`` (worktree + index + an
    uncommitted deletion in one call). A freshly-initialized repo has no
    ``HEAD`` yet, so ``git diff HEAD`` fails outright (not "empty diff",
    an error) and a staged file would otherwise fall through the whole
    ladder to ``none`` — the file's content silently vanishes even though
    it is sitting in the index. When ``HEAD`` does not resolve, diff the
    index directly (``git diff --cached``) instead; this is the same
    "uncommitted" contract the HEAD-diff makes (content not yet in a
    commit), just against the empty-tree baseline instead of HEAD.
    """
    has_head = _git(root, ["rev-parse", "--verify", "HEAD"]) is not None
    if has_head:
        uncommitted = _git(root, ["diff", "HEAD", "--unified=3", "--", rel])
    else:
        uncommitted = _git(root, ["diff", "--cached", "--unified=3", "--", rel])
    if uncommitted and uncommitted.strip():
        lines, trunc = _parse_unified(uncommitted)
        return {"diff_type": "uncommitted", "lines": lines, "truncated": trunc}

    last_commit = _last_commit_diff(root, rel)
    if last_commit is not None:
        return last_commit

    untracked = _untracked_diff(root, rel)
    if untracked is not None:
        return untracked

    return {
        "diff_type": "none",
        "lines": [],
        "truncated": False,
        "reason": "no working, staged, or committed changes",
    }


def diff_for_path(path: str) -> dict:
    """Full engine entry point for an absolute path.

    Returns ``{available, diff_type, lines, truncated, reason?, commit?}`` —
    the shared shape both ``/api/file-diff`` and ``/api/trace/file`` expose.
    ``available:false`` means no repo could be resolved at all; a resolved
    repo with no diff to show is ``available:true, diff_type:'none'``.
    """
    root, rel, reason = repo_root_and_relpath(path)
    if root is None:
        return {
            "available": False,
            "diff_type": "none",
            "lines": [],
            "truncated": False,
            "reason": reason,
        }
    result = _resolve_diff(root, rel)
    result["available"] = True
    return result


__all__ = [
    "resolve_repo_root",
    "repo_root_and_relpath",
    "diff_for_path",
    "_MAX_LINES",
    "_git",
    "_is_untracked",
    "_parse_unified",
    "_full_content_as_adds",
    "_last_commit_diff",
    "_untracked_diff",
    "_resolve_diff",
]
