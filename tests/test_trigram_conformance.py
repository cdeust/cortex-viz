"""pg_trgm conformance + scale benchmark for ui/brain/js/trigram.js.

The fixture (tests/fixtures/pg_trgm_reference.json) is the reference
implementation: it was generated from PostgreSQL 17.9's real pg_trgm
extension, active on the cortex DB (verified via ``pg_extension``), with:

    SELECT a, b, similarity(a, b) FROM (VALUES
        ('http','http'), ('interact','interakt'), ('route','routes'),
        ('standalone','standalon'), ('graph','grpah'), ('memory','memoire'),
        ('a','a'), ('a','b'), ('ab','ab'), ('','x'), ('BRAIN','brain'),
        ('focusnode','focus'), ('node','nodes'), ('viz','visualization'),
        ('123','x123'), ('http','http_standalone_routes'),
        ('selection','selektion'), ('cortex','vortex'), ('brain','brian'),
        ('search','serach'), ('layout','layot'), ('detail','detial'),
        ('workflow','work'), ('py','python'), ('trgm','trigram'),
        ('scene','scenes'), ('edge','edges'), ('impact','impakt'),
        ('anatomy','anatomie'), ('x','xylophone')
    ) AS pairs(a, b);

If pg_trgm's behaviour ever needs re-verifying, rerun that query against a
live pg_trgm-enabled Postgres and regenerate the fixture — the fixture is the
ground truth; ui/brain/js/trigram.js must match it, not the other way round.

The scale benchmark (300k synthetic identifier-like labels, run inside the
same Node process by tests/js/run_trigram_conformance.mjs) measures the
index-build and single-query-scan cost the search worker will actually pay,
per tasks/todo.md §2 ("mesurer et rapporter le temps réel").
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_HARNESS = _REPO_ROOT / "tests" / "js" / "run_trigram_conformance.mjs"

# Generous CI bound — the measured number is reported regardless of pass/fail.
# source: tasks/todo.md §2, "budget frame Three.js: 16.7 ms" is the per-frame
# target for the *render* loop; a single off-thread query scan is not on that
# budget, so 500 ms is a coarse regression guard, not a UX target.
_QUERY_SCAN_BOUND_MS = 500.0


def _node_available() -> str | None:
    return shutil.which("node")


def test_trigram_conformance_and_scale():
    node = _node_available()
    if node is None:
        pytest.skip("node executable not found on PATH — required to run trigram.js")

    result = subprocess.run(
        [node, str(_HARNESS)],
        cwd=str(_REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, (
        f"conformance harness failed (exit {result.returncode}):\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )

    data = json.loads(result.stdout)

    failed_pairs = [d for d in data["details"] if not d["ok"]]
    assert data["failed"] == 0, (
        f"{data['failed']} pg_trgm conformance pair(s) failed: "
        f"{json.dumps(failed_pairs, indent=2)}"
    )
    print(f"\npg_trgm conformance: {data['passed']} passed, {data['failed']} failed")

    bench = data["bench"]
    print(
        f"scale benchmark: n={bench['n']} "
        f"index_build_ms={bench['index_build_ms']:.2f} "
        f"query_scan_ms={bench['query_scan_ms']:.2f} "
        f"result_count={bench['result_count']}"
    )
    assert bench["query_scan_ms"] < _QUERY_SCAN_BOUND_MS, (
        f"query scan over {bench['n']} synthetic nodes took "
        f"{bench['query_scan_ms']:.2f} ms, exceeding the {_QUERY_SCAN_BOUND_MS} ms bound"
    )
