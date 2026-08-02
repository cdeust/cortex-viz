"""Domain mapping — resolves paths, slugs, and hints to canonical domain names.

Builds the mapping dynamically from git repos discovered on the filesystem.
No hardcoded domain list — git remote URLs are the structural invariant
(they survive renames, moves, worktree creation).

Algorithm (Rejewski + Shannon):
  1. Discover git repos under ~/Developments
  2. Group related repos by shared remote-URL name prefix
  3. Build a slug decoder (encode known paths as slugs, match by prefix)
  4. Build a fragment index (all substrings of known names)
  5. Resolve: cwd → git_root → longest prefix match → canonical name

Pure business logic — uses subprocess only for `git remote get-url origin`.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path


@dataclass
class RepoInfo:
    fs_path: str
    dir_name: str
    remote_name: str
    canonical: str = ""


# ── Step 1: Discover git repos ────────────────────────────────────────


def _get_remote_url(repo_path: Path) -> str:
    """Get git remote origin URL. Returns '' if no remote."""
    try:
        return (
            subprocess.check_output(
                ["git", "-C", str(repo_path), "remote", "get-url", "origin"],
                stderr=subprocess.DEVNULL,
                timeout=3,
            )
            .decode()
            .strip()
        )
    except Exception:
        return ""


def _extract_repo_name(url: str) -> str:
    """Extract repo name from remote URL.

    'github.com/cdeust/Cortex.git' → 'cortex'
    'github.com/cdeust/ai-architect-pipeline.git' → 'ai-architect-pipeline'
    """
    if not url:
        return ""
    name = url.rstrip("/").rsplit("/", 1)[-1]
    if name.endswith(".git"):
        name = name[:-4]
    return name.lower()


def _discover_repos(dev_root: Path) -> list[RepoInfo]:
    """Scan for git repos under dev_root (2 levels deep)."""
    repos: list[RepoInfo] = []
    if not dev_root.is_dir():
        return repos
    for item in dev_root.iterdir():
        if not item.is_dir() or item.name.startswith("."):
            continue
        repos.extend(_repos_under(item))
    return repos


def _repos_under(item: Path) -> list[RepoInfo]:
    """The checkouts reachable from one dev-root entry.

    Either the entry is itself a checkout, or it is an org directory
    (e.g. ``anthropic/ai-automatised-pipeline``) whose children are —
    which is the second of the two levels ``_discover_repos`` scans.
    """
    if (item / ".git").is_dir():
        return [_repo_info(item)]
    return [
        _repo_info(sub)
        for sub in item.iterdir()
        if sub.is_dir() and (sub / ".git").is_dir()
    ]


def _repo_info(path: Path) -> RepoInfo:
    """Describe one checkout, preferring the git remote's name over the
    directory's when the two disagree."""
    remote = _get_remote_url(path)
    return RepoInfo(
        fs_path=str(path),
        dir_name=path.name.lower(),
        remote_name=_extract_repo_name(remote) or path.name.lower(),
    )


# ── Step 2: Group repos by shared remote-name prefix ─────────────────


def _shared_prefix(a: str, b: str) -> str:
    """Find the longest shared hyphen-delimited prefix between two names.

    'ai-architect-pipeline' and 'ai-architect-prd-builder' → 'ai-architect'
    'cortex' and 'cortex-cowork' → 'cortex'
    'career-ops' and 'memory-monitor' → '' (no shared prefix)
    """
    parts_a = a.split("-")
    parts_b = b.split("-")
    common: list[str] = []
    # strict=False on purpose: the two names routinely have different
    # segment counts, and stopping at the shorter one IS the common-prefix
    # rule ('cortex' vs 'cortex-cowork' → 'cortex').
    for pa, pb in zip(parts_a, parts_b, strict=False):
        if pa == pb:
            common.append(pa)
        else:
            break
    prefix = "-".join(common)
    # Require prefix to be meaningful: at least 4 chars
    # This allows "cortex" (6 chars, 1 segment) to group cortex-cowork
    # but rejects "ai" (2 chars) from falsely grouping unrelated ai-* repos
    return prefix if len(prefix) >= 4 else ""


def _group_repos(repos: list[RepoInfo]) -> dict[str, str]:
    """Group repos by shared remote-name prefix. Return name→canonical mapping.

    Uses pairwise prefix detection: if two repos share a prefix of >= 2
    hyphen segments, they belong to the same family. The shared prefix
    becomes the canonical name.
    """
    # Collect all names
    all_names = [(r.remote_name, r) for r in repos]

    # Find all pairwise shared prefixes
    prefix_groups: dict[str, set[str]] = {}  # prefix → set of names
    for i in range(len(all_names)):
        for j in range(i + 1, len(all_names)):
            prefix = _shared_prefix(all_names[i][0], all_names[j][0])
            if prefix:  # _shared_prefix already enforces >= 4 chars
                prefix_groups.setdefault(prefix, set()).update(
                    {all_names[i][0], all_names[j][0]}
                )

    # Merge overlapping groups: when a name appears in several prefix groups,
    # the longest prefix wins.
    name_to_canonical: dict[str, str] = {}
    for prefix, members in sorted(prefix_groups.items(), key=lambda x: -len(x[0])):
        for member in members:
            if member not in name_to_canonical:
                name_to_canonical[member] = prefix

    # Assign canonical to repos and register dir_names
    for repo in repos:
        rn = repo.remote_name
        if rn in name_to_canonical:
            repo.canonical = name_to_canonical[rn]
        else:
            # Standalone repo — canonical is itself
            repo.canonical = rn
            name_to_canonical[rn] = rn

        # Also register dir_name → canonical
        if repo.dir_name != rn and repo.dir_name not in name_to_canonical:
            name_to_canonical[repo.dir_name] = repo.canonical

    return name_to_canonical


# ── Step 3: Build slug decoder ────────────────────────────────────────


def _build_slug_index(repos: list[RepoInfo]) -> dict[str, RepoInfo]:
    """Map slug-encoded repo paths to RepoInfo for prefix matching."""
    index: dict[str, RepoInfo] = {}
    for repo in repos:
        # Encode the real path as a slug (same encoding Claude uses)
        slug = repo.fs_path.replace("/", "-").lstrip("-").lower()
        index[slug] = repo
    return index


def _match_slug(slug: str, slug_index: dict[str, RepoInfo]) -> RepoInfo | None:
    """Match a project slug against known repo slugs by longest prefix."""
    clean = slug.lstrip("-").lower()
    # Strip worktree noise: slug contains "--" before worktree suffix
    if "--" in clean:
        clean = clean.split("--")[0]
    # Also strip at "-worktrees-" if present without double dash
    if "-worktrees-" in clean:
        clean = clean[: clean.index("-worktrees-")]

    best: RepoInfo | None = None
    best_len = 0
    for known_slug, repo in slug_index.items():
        if clean.startswith(known_slug) and len(known_slug) > best_len:
            best = repo
            best_len = len(known_slug)
    return best


# ── Step 4: Build fragment index ──────────────────────────────────────


def _build_fragment_index(
    repos: list[RepoInfo],
    name_to_canonical: dict[str, str],
) -> dict[str, str]:
    """Map meaningful fragments to canonical names.

    For each repo, generate all contiguous sub-sequences of hyphen-delimited
    parts (length >= 4 chars). Longer fragments win ties.
    """
    fragments: dict[str, tuple[str, int]] = {}  # fragment → (canonical, length)

    def _claim(fragment: str, canonical: str) -> None:
        """Record ``fragment -> canonical`` unless a longer claim already won."""
        if len(fragment) < 4:
            return
        existing = fragments.get(fragment)
        if existing is None or len(fragment) > existing[1]:
            fragments[fragment] = (canonical, len(fragment))

    def _sub_sequences(name: str) -> list[str]:
        """Every contiguous run of hyphen-delimited parts in ``name``."""
        parts = name.split("-")
        return [
            "-".join(parts[i:j])
            for i in range(len(parts))
            for j in range(i + 1, len(parts) + 1)
        ]

    for repo in repos:
        for name in {repo.dir_name, repo.remote_name}:
            for fragment in _sub_sequences(name):
                _claim(fragment, repo.canonical)

    return {k: v[0] for k, v in fragments.items()}


# ── Step 5: Git root resolution ───────────────────────────────────────


def _git_root(path: str) -> str | None:
    """Find the git repo root for a path. Returns None if not in a repo."""
    try:
        return (
            subprocess.check_output(
                ["git", "-C", path, "rev-parse", "--show-toplevel"],
                stderr=subprocess.DEVNULL,
                timeout=3,
            )
            .decode()
            .strip()
        )
    except Exception:
        return None


# ── Registry ──────────────────────────────────────────────────────────


@dataclass
class DomainRegistry:
    repos: list[RepoInfo]
    name_to_canonical: dict[str, str]
    slug_index: dict[str, RepoInfo]
    fragment_index: dict[str, str]
    path_to_repo: dict[str, RepoInfo] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.path_to_repo = {r.fs_path: r for r in self.repos}


def candidate_dev_roots() -> list[Path]:
    """Plausible parent directories for the user's git repos.

    Probed in order:
      1. ``$CORTEX_DEV_ROOT`` env var — explicit override.
      2. ``~/Developments`` — original assumption.
      3. ``~/Documents/Developments`` — the common macOS Documents-nested layout.
      4. ``~/dev`` and ``~/code`` — common alternative parents.

    The first directory that exists wins. Without this fallback the
    registry returns zero repos on systems where the user keeps source
    under ``~/Documents`` (a real layout in production today).
    """
    import os as _os

    cands: list[Path] = []
    env = _os.environ.get("CORTEX_DEV_ROOT")
    if env:
        cands.append(Path(env))
    home = Path.home()
    cands.extend(
        [
            home / "Developments",
            home / "Documents" / "Developments",
            home / "dev",
            home / "code",
        ]
    )
    return [c for c in cands if c.is_dir()]


@lru_cache(maxsize=1)
def _build_registry() -> DomainRegistry:
    """Build the complete domain registry from git repos. Cached at startup.

    Scans every candidate dev root (see ``candidate_dev_roots``) so the
    registry works regardless of whether the user keeps repos at
    ``~/Developments`` or ``~/Documents/Developments``.
    """
    repos: list[RepoInfo] = []
    seen_paths: set[str] = set()
    for dev_root in candidate_dev_roots():
        for r in _discover_repos(dev_root):
            if r.fs_path in seen_paths:
                continue
            seen_paths.add(r.fs_path)
            repos.append(r)
    name_to_canonical = _group_repos(repos)
    slug_index = _build_slug_index(repos)
    fragment_index = _build_fragment_index(repos, name_to_canonical)
    return DomainRegistry(repos, name_to_canonical, slug_index, fragment_index)


# ── Public API ────────────────────────────────────────────────────────


def resolve_domain(input_str: str) -> str:
    """Resolve any input to a canonical domain name.

    Handles:
    - Filesystem paths: /Users/cdeust/Developments/Cortex/mcp_server
    - Project slugs: -Users-cdeust-Developments-Cortex
    - Domain hints: 'cortex', 'ai-architect'
    - Broken fragments: 'architect', 'builder', 'loop'
    """
    if not input_str or not input_str.strip():
        return ""

    registry = _build_registry()
    clean = input_str.strip()
    lower = clean.lower()

    # Ordered most-specific first: an answer from an earlier step is
    # evidence-backed (a real git root, a real slug), a later one is a
    # guess from a shared substring.
    for candidate in (
        _match_by_path(clean, registry),
        _match_by_slug(clean, registry),
        registry.name_to_canonical.get(lower),
        _match_by_fragment(lower, registry),
    ):
        if candidate:
            return candidate

    return _slug_tail_fallback(clean, lower)


def _match_by_path(clean: str, registry) -> str | None:
    """Step 1 — filesystem path → git root → known repo."""
    if "/" not in clean or clean.startswith("-"):
        return None
    root = _git_root(clean)
    if root and root in registry.path_to_repo:
        return registry.path_to_repo[root].canonical
    # Try prefix match against known repo paths
    for repo in registry.repos:
        if clean.startswith(repo.fs_path):
            return repo.canonical
    return None


def _match_by_slug(clean: str, registry) -> str | None:
    """Step 2 — a path-encoded project slug (starts with ``-``)."""
    if not clean.startswith("-") or len(clean) <= 10:
        return None
    repo = _match_slug(clean, registry.slug_index)
    return repo.canonical if repo else None


def _match_by_fragment(lower: str, registry) -> str | None:
    """Step 4 — the longest known fragment contained in the input.

    Exact fragment hits win outright; otherwise the longest substring
    match does, so 'ai-architect-prd' beats 'prd'.
    """
    exact = registry.fragment_index.get(lower)
    if exact:
        return exact
    best, best_len = None, 0
    for frag, canonical in registry.fragment_index.items():
        if len(frag) >= 4 and frag in lower and len(frag) > best_len:
            best, best_len = canonical, len(frag)
    return best


def _slug_tail_fallback(clean: str, lower: str) -> str:
    """Step 5 — nothing matched.

    For a raw slug, returning the whole path-encoded string pollutes
    domain ids, so the canonical ``-Users-…-Developments-`` /
    ``-Documents-`` prefix is stripped and the trailing meaningful
    segment returned instead.
    """
    if not clean.startswith("-"):
        return lower
    stripped = lower
    for prefix in (
        "-users-cdeust-developments-",
        "-users-cdeust-documents-",
        "-users-cdeust-",
    ):
        if stripped.startswith(prefix):
            stripped = stripped[len(prefix) :]
            break
    # Strip worktree suffixes that survived (no slug match found above).
    if "-worktrees-" in stripped:
        stripped = stripped[: stripped.index("-worktrees-")]
    # First hyphen-segment is the most meaningful tail (e.g. "jarvis" from
    # "-Users-cdeust-Developments-jarvis"). Multi-segment tails (e.g.
    # "ai-architect-prd-builder") collapse via the earlier slug match.
    return stripped.split("-", 1)[0] if stripped else lower


def resolve_cwd(cwd: str) -> str:
    """Resolve a working directory to a canonical domain.

    This is the primary domain resolution path (Shannon: cwd is the
    minimum sufficient statistic for domain identity).

    Returns '' if the cwd does not belong to a *known* repo — callers
    rely on empty-string to fall through to explicit domain hints.
    """
    if not cwd:
        return ""
    root = _git_root(cwd)
    if root:
        registry = _build_registry()
        repo = registry.path_to_repo.get(root)
        if repo:
            return repo.canonical
    # If not in a known git repo, return '' so callers can fall through
    # to explicit domain hints.  The old behaviour delegated to
    # resolve_domain(cwd) which *always* returns non-empty (it falls
    # back to the lowercased input), silently overriding any explicit
    # domain the caller intended to use.
    return ""
