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

Pure — one filesystem read of the running package's own location, no I/O
beyond that, no network, no subprocess.
"""

from __future__ import annotations

from pathlib import Path

# Returned when the running process cannot resolve its own package location
# (e.g. a frozen/zipped import with no ``__file__``). Matches the pre-D10
# behaviour (one implicit global scope) so a process that cannot self-locate
# degrades to the old shared-row semantics rather than failing the build.
FALLBACK_SCOPE = "default"


def resolve_instance_scope() -> str:
    """Return the checkout root owning the running ``cortex_viz`` package.

    Precondition: none — always callable.
    Postcondition: returns the absolute, symlink-resolved path of the
    directory one level above the ``cortex_viz`` package directory (the git
    checkout root in a dev/worktree/plugin-cache layout, or the site-packages
    parent in a pip install — either way, a stable per-checkout string that
    two independent installs of this code never share). Returns
    ``FALLBACK_SCOPE`` only if ``cortex_viz`` cannot self-locate.
    """
    try:
        import cortex_viz

        pkg_dir = Path(cortex_viz.__file__).resolve().parent
        return str(pkg_dir.parent)
    except Exception:
        return FALLBACK_SCOPE
