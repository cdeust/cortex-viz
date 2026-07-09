"""Unit tests for the D10 checkout-scope discriminant.

``resolve_instance_scope`` distinguishes two viz server processes serving
different checkouts of this repo (e.g. the main clone vs a git worktree) —
the discriminant ``workflow_graph_snapshot`` scoping depends on.
"""

from __future__ import annotations

from pathlib import Path

import cortex_viz
from cortex_viz.shared.instance_scope import FALLBACK_SCOPE, resolve_instance_scope


def test_resolves_to_the_checkout_root_owning_the_running_package():
    """The scope is the directory one level above the ``cortex_viz`` package
    — the git checkout root in this dev layout."""
    expected = str(Path(cortex_viz.__file__).resolve().parent.parent)
    assert resolve_instance_scope() == expected


def test_stable_across_repeated_calls():
    """Pure function: same process, same answer every time."""
    assert resolve_instance_scope() == resolve_instance_scope()


def test_falls_back_when_package_cannot_self_locate():
    """A ``cortex_viz`` module without ``__file__`` (e.g. a frozen import)
    degrades to the documented fallback instead of raising.

    ``resolve_instance_scope`` does its own ``import cortex_viz`` internally
    (module-local, not a top-level binding), so the substitution has to go
    through ``sys.modules`` — patching the cached module object itself.
    """
    import sys

    real_module = sys.modules["cortex_viz"]
    try:
        broken = type(sys)("cortex_viz")  # a module object with no __file__
        sys.modules["cortex_viz"] = broken
        assert resolve_instance_scope() == FALLBACK_SCOPE
    finally:
        sys.modules["cortex_viz"] = real_module
