"""CI guard: the optional extras must actually be installed in CI.

``importorskip`` is the right idiom for a genuinely optional feature, but
it is silent by construction: if the CI environment stops installing the
extras, every test behind such a skip evaporates and the job still passes
green. That is how ``tests/test_community_detection.py``'s 11 tests never
ran in CI until ``--all-extras`` landed (#89) — the skip was permanent
rather than conditional, and nothing said so (#88).

This module closes that hole from inside the suite rather than by
comparing job counters: when ``CORTEX_VIZ_REQUIRE_EXTRAS`` is set — which
only the CI ``test`` job does — a missing extra is a FAILURE, not a skip.
Locally, without the variable, the extras stay optional and these tests
skip, which is the honest local contract.
"""

from __future__ import annotations

import importlib
import os

import pytest

REQUIRE_EXTRAS_ENV = "CORTEX_VIZ_REQUIRE_EXTRAS"

# One entry per import name the optional extras must provide, with the
# extra that ships it. Import names, not distribution names: ``Pillow``
# imports as ``PIL``, and it is the import that the code paths perform.
_EXTRA_MODULES = [
    ("viz-tile", "igraph"),
    ("viz-tile", "datashader"),
    ("viz-tile", "pyarrow"),
    ("viz-tile", "pandas"),
    ("viz-tile", "cachetools"),
    ("viz-tile", "PIL"),
    ("community", "leidenalg"),
]

requires_extras = pytest.mark.skipif(
    not os.environ.get(REQUIRE_EXTRAS_ENV),
    reason=(
        f"{REQUIRE_EXTRAS_ENV} unset — the extras are optional outside CI. "
        "The CI test job sets it so a missing extra fails instead of skipping."
    ),
)


@requires_extras
@pytest.mark.parametrize(
    "extra,module",
    _EXTRA_MODULES,
    ids=[f"{extra}:{module}" for extra, module in _EXTRA_MODULES],
)
def test_optional_extra_is_installed(extra: str, module: str) -> None:
    """Fail loudly when an extra CI claims to install is absent.

    The failure message names the extra, because the fix is an install
    flag in the workflow (``uv sync --all-extras``), not a code change.
    """
    try:
        importlib.import_module(module)
    except ImportError as exc:  # pragma: no cover - the guard's own failure
        pytest.fail(
            f"{module!r} is missing, so every test behind its importorskip "
            f"silently vanished. It ships in the {extra!r} extra — check that "
            f"the CI job still installs it (uv sync --all-extras). Cause: {exc}"
        )


@requires_extras
def test_community_detection_tests_are_not_skipped() -> None:
    """The named regression from #88: 11 tests that never ran in CI.

    Asserting the import directly, rather than the collected count, keeps
    the guard independent of how many tests that module happens to hold.
    """
    assert importlib.import_module("leidenalg") is not None
