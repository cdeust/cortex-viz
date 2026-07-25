// ui/brain/js/trigram.js — pg_trgm conformance (issue #35, criterion 3).
//
// This coverage already existed, but only as a side-channel: a standalone
// harness (tests/js/run_trigram_conformance.mjs) invoked from a PYTHON test
// (tests/test_trigram_conformance.py). That works, yet it means the repo's only
// JS assertions are invisible to anyone running the JS suite, and a JS
// contributor gets no signal at all. Absorbed here so the JS suite is the one
// place JS behaviour is checked.
//
// The harness is REUSED rather than reimplemented: it owns the fixture
// comparison against real PostgreSQL 17.9 pg_trgm output, and duplicating that
// logic would create two definitions of "correct" that can disagree. The Python
// test keeps working unchanged — it consumes the same harness.
import { describe, it, expect } from "vitest";
import { execFileSync } from "node:child_process";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const REPO_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..", "..");
const HARNESS = resolve(REPO_ROOT, "tests/js/run_trigram_conformance.mjs");

function runHarness() {
  const out = execFileSync(process.execPath, [HARNESS], {
    cwd: REPO_ROOT,
    encoding: "utf8",
    maxBuffer: 32 * 1024 * 1024,
  });
  return JSON.parse(out);
}

describe("trigram.js vs real pg_trgm output", () => {
  const result = runHarness();

  it("reproduces every reference pair within tolerance", () => {
    const failures = result.details.filter((d) => !d.ok);
    expect(
      failures,
      `pairs diverging from PostgreSQL pg_trgm: ${JSON.stringify(failures)}`,
    ).toEqual([]);
    expect(result.failed).toBe(0);
  });

  it("actually compared a non-trivial number of pairs", () => {
    // Guards the guard: an empty fixture would make the assertion above pass
    // vacuously. The committed reference set has 30 pairs.
    expect(result.passed).toBeGreaterThanOrEqual(30);
    expect(result.details.length).toBe(result.passed + result.failed);
  });

  it("reports a scale benchmark over the synthetic corpus", () => {
    // Not a perf GATE — wall-clock in CI is not a deterministic property
    // (see cdeust/automatised-pipeline#74 for what that costs). This only
    // asserts the benchmark ran and produced sane shape, so a silently
    // broken harness cannot masquerade as a pass.
    expect(result.bench.n).toBe(300000);
    expect(result.bench.index_build_ms).toBeGreaterThan(0);
    expect(result.bench.result_count).toBeGreaterThan(0);
  });
});
