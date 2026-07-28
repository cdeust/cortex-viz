"""Instance-scope resolution for the shared PostgreSQL snapshot table.

D10 (inc5 design): ``workflow_graph_snapshot`` is a single shared table —
two viz server processes serving DIFFERENT checkouts of this repo (e.g. the
release install on the main clone and a dev server running out of a git
worktree) previously overwrote each other's row (``DELETE`` + single-row
``INSERT``, no discriminant). Demonstrated live 2026-07-09.

The discriminant chosen here is the on-disk checkout root that OWNS the
running ``cortex_viz`` package — not the git-remote canonical domain name
(``cortex_viz.shared.domain_mapping``), which deliberately COLLAPSES a
worktree and its parent clone into the same canonical name (it answers "which
project is this", not "which checkout is running"). The checkout-root path is
the most deterministic signal already present in the code for this exact
distinction: ``http_standalone._get_ui_root`` resolves UI assets the same way
(``Path(__file__).parent.parent`` off a module inside the package), because a
dev checkout and a worktree checkout are, by construction, two different
directories on disk even when they share one git remote.

D12 (2026-07-28): the package path ALONE is not stable for a plugin install,
because the "edition live" dev montage symlinks ``<plugin_install>/cortex_viz``
at the working repo. CPython's FileFinder resolves a package directory symlink
when it sets ``__path__``/``__file__``, so the SAME plugin install reports
``<plugin_install>`` when unmounted and ``<repo>`` when mounted — a scope flip
that silently orphans every snapshot the install already wrote (observed live
2026-07-28: a 101,966-node / 531,252-edge snapshot written under
``.../cache/cortex-plugins/cortex-viz/2.7.1`` became unreadable the moment the
montage was mounted, so ``/api/graph/full`` answered 503 ``no_snapshot`` and
the brain view — whose ONLY sources are ``/api/graph/full[/stream]``, with no
progressive fallback — failed while the galaxy view kept working off the
in-process build cache).

``CLAUDE_PLUGIN_ROOT`` is therefore preferred when it is set AND it actually
owns the running package. That is the host's own identity for the install: it
is invariant under mount/unmount, and two distinct plugin installs never share
it, so the D10 distinction is preserved. It is only trusted after verifying
that the running package really lives under it (a stale or hostile value that
does not own this package is ignored), so it cannot mis-scope a process it
does not describe. A dev server run straight from a checkout or a worktree
sets no ``CLAUDE_PLUGIN_ROOT`` and keeps the original package-path behaviour,
which is what D10 needs to tell those apart.

Pure — filesystem reads of the running package's own location, no network,
no subprocess.
"""

from __future__ import annotations

import os
from pathlib import Path

# Returned when the running process cannot resolve its own package location
# (e.g. a frozen/zipped import with no ``__file__``). Matches the pre-D10
# behaviour (one implicit global scope) so a process that cannot self-locate
# degrades to the old shared-row semantics rather than failing the build.
FALLBACK_SCOPE = "default"

# The host-provided install root. Set by Claude Code for every plugin process.
_PLUGIN_ROOT_ENV = "CLAUDE_PLUGIN_ROOT"


def _owns_package(candidate: Path, pkg_dir: Path) -> bool:
    """True when ``candidate`` is the install root that holds ``pkg_dir``.

    Precondition: ``pkg_dir`` is the resolved ``cortex_viz`` package
    directory. Postcondition: returns True only if ``candidate/cortex_viz``
    exists and resolves to the SAME directory the process is running, so a
    ``CLAUDE_PLUGIN_ROOT`` belonging to a different plugin (or a stale one
    left over from another install) is rejected rather than trusted. Never
    raises — an unreadable candidate is simply not the owner.
    """
    try:
        return (candidate / pkg_dir.name).resolve() == pkg_dir
    except OSError:
        return False


def resolve_instance_scope() -> str:
    """Return the install root owning the running ``cortex_viz`` package.

    Precondition: none — always callable.
    Postcondition: returns ``CLAUDE_PLUGIN_ROOT`` when that variable is set
    and owns the running package (the mount-invariant plugin identity, D12);
    otherwise the absolute, symlink-resolved path of the directory one level
    above the ``cortex_viz`` package directory (the git checkout root in a
    dev/worktree layout, or the site-packages parent in a pip install) —
    either way, a stable per-install string that two independent installs of
    this code never share. Returns ``FALLBACK_SCOPE`` only if ``cortex_viz``
    cannot self-locate.
    """
    try:
        import cortex_viz

        pkg_dir = Path(cortex_viz.__file__).resolve().parent
        env_root = os.environ.get(_PLUGIN_ROOT_ENV, "")
        if env_root:
            candidate = Path(env_root)
            if _owns_package(candidate, pkg_dir):
                return str(candidate)
        return str(pkg_dir.parent)
    except Exception:
        return FALLBACK_SCOPE
