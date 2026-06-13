"""Bidirectional conversion between filesystem paths, Claude project IDs,
human-readable labels, and domain identifiers.

Claude Code stores project data in directories named by mangled filesystem paths.

POSIX example:    /Users/dev/cortex          -> -Users-dev-cortex
Windows example:  C:\\Users\\michael.crawford -> c--users-michael-crawford
Git-Bash form:    /c/users/michael.crawford  -> c--users-michael-crawford

Path normalization is an abstraction barrier — consumers of cwd_to_project_id
must not need to know the source OS. The shape of the input path (drive letter,
backslash, leading single-letter directory) determines the dialect, never the
host OS. The function is therefore deterministic across platforms and
idempotent on canonical slugs already on disk.
"""

from __future__ import annotations

import re

_STRIP_PREFIX_RE = re.compile(r"^-?Users-[^-]+(-Documents)?(-Developments)?-")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")
_LEADING_TRAILING_DASH_RE = re.compile(r"^-|-$")

# Drive-letter prefix: "C:", "C:/", "C:\\". The colon is the structural marker.
_WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:")
# Git-Bash drive translation: "/c/...", "/C/...". The single-letter directory
# at root is the structural marker. We convert to drive form and reuse the
# Windows normalizer to keep one canonical slug per logical path.
_GITBASH_DRIVE_RE = re.compile(r"^/([A-Za-z])/")
# Whole-string non-alphanumeric (incl. ':', '\\', '/', '.') for Windows slug.
_WINDOWS_SLUG_RE = re.compile(r"[^a-z0-9]")


def _is_windows_path(path: str) -> bool:
    """True if path looks like a Windows absolute path (drive letter)."""
    return bool(_WINDOWS_DRIVE_RE.match(path))


def _gitbash_to_windows(path: str) -> str | None:
    """Convert '/c/users/foo' → 'c:/users/foo'. Returns None if not gitbash."""
    m = _GITBASH_DRIVE_RE.match(path)
    if not m:
        return None
    drive = m.group(1)
    return f"{drive}:/{path[3:]}"


def _windows_slug(path: str) -> str:
    """Normalize a Windows-style absolute path to its on-disk Claude slug.

    Lowercase, then replace each non-alphanumeric character with a single '-'.
    Per-character (not per-run) substitution is intentional: it matches the
    Claude Code convention where 'C:\\Users' becomes 'c--users' (two dashes
    from ':' and '\\').
    """
    return _WINDOWS_SLUG_RE.sub("-", path.lower())


def cwd_to_project_id(cwd: str | None) -> str | None:
    """Convert a working directory path to a Claude project ID.

    Preconditions:
        - cwd is None, empty, or a filesystem path in any platform's syntax
          (POSIX, Windows forward-slash, Windows backslash, Git-Bash).
    Postconditions:
        - Returns None when cwd is None or empty.
        - Returns the slug Claude Code uses as its on-disk project directory
          name for that path.
        - Idempotent on canonical slugs already on disk: passing back a
          previously-produced slug returns the same slug.
    """
    if not cwd:
        return None

    # Git-Bash drive translation '/c/...' canonicalizes to Windows form first.
    gb = _gitbash_to_windows(cwd)
    if gb is not None:
        return _windows_slug(gb)

    # Windows absolute path: full lowercase + per-char non-alnum→'-'.
    if _is_windows_path(cwd):
        return _windows_slug(cwd)

    # POSIX or already-a-slug path: case-preserving, only path separators
    # become dashes. Backslashes are normalized for safety on mixed inputs
    # (e.g., a slug pasted into a Windows shell that escaped its own seps).
    return cwd.replace("\\", "/").replace("/", "-")


def project_id_to_label(project_id: str | None) -> str:
    """Convert a Claude project ID to a human-readable label.

    Strips common path prefixes (Users, Documents, Developments)
    and replaces dashes with spaces.
    """
    if not project_id:
        return "Unknown"
    result = _STRIP_PREFIX_RE.sub("", project_id).replace("-", " ").strip()
    return result or project_id


def domain_id_from_label(label: str | None) -> str:
    """Convert a human-readable label to a kebab-case domain ID."""
    if not label:
        return ""
    result = _NON_ALNUM_RE.sub("-", label.lower())
    return _LEADING_TRAILING_DASH_RE.sub("", result)
