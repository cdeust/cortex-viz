#!/usr/bin/env bash
# Source: .github/workflows/ci.yml and tests/js/wiki_export_artifact.test.mjs.
set -euo pipefail
cd "$(dirname "$0")/../.."
export PATH="$PWD/.venv/bin:$PATH"
# Source: tests/test_optional_extras_present.py; missing CI extras must fail.
export CORTEX_VIZ_REQUIRE_EXTRAS=1
case "${1:?phase required}" in
  setup)
    uv sync --locked --all-extras --python 3.12
    npm ci
    ;;
  unit)
    # Preserve the existing 80% floor with one complete Python suite run.
    python -m coverage run -m pytest
    python -m coverage report
    ;;
  regression)
    npm test -- --exclude tests/js/wiki_export_artifact.test.mjs
    ;;
  e2e)
    # Real export generation and JSDOM execution, not a native browser test.
    npm test -- tests/js/wiki_export_artifact.test.mjs
    python .github/ci/mcp_probe.py "$PWD/.venv/bin/hypermnesia-mcp-viz"
    ;;
  environment)
    python -m pytest -q tests/test_optional_extras_present.py tests/test_no_db_mode.py tests/test_windows_launch_path.py
    ;;
  installation)
    python .github/ci/install_probe.py
    ;;
  release-version)
    python -c 'import tomllib; v=tomllib.load(open("pyproject.toml", "rb"))["project"]["version"]; print("version="+v); print("tag=v"+v)'
    ;;
  *) echo "Unknown phase: $1" >&2; exit 2 ;;
esac
