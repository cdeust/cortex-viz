# Agent guidance for cortex-viz

This is the former body of `CLAUDE.md`, moved here on 2026-09-08 so that it is read on
demand instead of being re-sent to the model on every turn (owner correction: CLAUDE.md
stays nearly empty; the host harness loads what it needs when it needs it). Nothing was
removed except the dangling "Global rules are imported, not restated" sentence, whose
import lines were already removed in an earlier commit — with no lines left to import, the
sentence had nothing to point at.

# cortex-viz

Standalone read-only visualization MCP for Cortex (published as hypermnesia-mcp-viz). Python + JavaScript.

## Repo-specific constraints

- Read-only over Cortex's store: this server never writes memories.
- The plugin install is live-mounted onto this clone; respawn the standalone HTTP server after editing Python or the running process keeps the old code.
- Layers: core / server / infrastructure / handlers / hooks / shared / errors.
- **`uv sync --frozen` alone does not install `pytest` or `mutmut`** — they live in the `dev` optional-dependency group (`pyproject.toml`'s `[project.optional-dependencies] dev`), and a bare `--frozen` sync skips every extra. Always run `uv sync --frozen --extra dev` in a fresh worktree/clone before testing.
  - **Symptom, not just the fix**: this fails *silently*, not loudly. `uv run pytest` still runs — it just falls through PATH to a system `pytest` (e.g. `/opt/homebrew/bin/pytest`, a different Python than `.venv`'s) instead of erroring "pytest not found". The tell is `uv run which pytest` resolving outside `.venv/bin/`, or DB-backed test modules (`test_memory_read.py`, `test_no_db_mode.py`, ...) failing collection with `ModuleNotFoundError: No module named 'psycopg'` even though `uv run python -c "import psycopg"` succeeds — the module-level import works fine standalone; only the wrong pytest interpreter can't see it. A scoped run against a couple of test files can look completely green while the full suite silently loses its collection of every DB-touching module — the whole point of `--frozen` (a reproducible, verifiable environment) is defeated by exactly the case it's meant to prevent.

## Etiquette

Conventional commits, staged file-by-file. One PR per concern. A pull request merges when
CI is green and a review verdict is posted on it; the owner does not gate merges by hand.
CI is the authority: it exists to catch regressions and enforce the engineering standards.

## CI contract for agents and routines

This section binds every agent that works on this repository: an interactive session,
a scheduled routine, the local manager. It follows the contract of `cdeust/Cortex`, the
source of truth for CI. Where the prompt of a routine is less restrictive, this section
wins: the prompt was written before the contract, and the repository's own standards
apply. It never widens what a prompt allows.

1. **The review verdict decides the merge.** The `PR Review` workflow posts a comment by
   `claude[bot]` that ends with `Verdict: approve` or `Verdict: block`. The resolver merges
   a pull request whose last verdict covers its head, reads approve, and whose required
   checks are green. Two cases differ: a Dependabot update the resolver classes as low risk
   (one commit signed by GitHub, minor or patch only, dependency manifests and lock
   files only, nothing under `.github/`) merges on green required checks without a
   review, and a title that contains "do not merge" is never merged. No
   agent merges by hand or enables auto-merge.
2. **Three reviews per pull request.** The budget counts the successful review runs of
   the pull request, so a push to an open pull request spends one. The `claude-rereview`
   label grants a fourth; it is the owner's to set, and the next push uses it. Push once
   per verdict, with every blocker of that verdict fixed, and never while a `PR Review`
   run is in progress on the pull request: the review would land on a commit that is no
   longer the head.
3. **After the third review the pull request is not reviewed again.** What the last
   review accepted merges; what it did not goes to a refinement pull request on a new
   branch, which has a budget of its own. When the head never got an approve, the same
   commits go to `<branch>-r<N>`: open a pull request with the same title and body plus a
   "Continuation" section naming the old one, leave one "Continued in #n" line on the old
   one, close it. A forced push of any kind, `--force-with-lease` included, is never
   used: it does not reset the budget and it rewrites a branch another agent may hold.
4. **No comparison with the main branch.** Do not measure how far a branch is behind, do
   not rebase a pull request branch onto the main branch, do not use the branch-update
   button or its API, and do not write that a pull request is late: a late pull request
   merges. The only exception is a real conflict (`mergeable_state` is `dirty`): merge the
   main branch into the branch with a merge commit, resolve it, rerun the touched tests,
   check that the merge did not change the meaning of either side, and push normally.
5. **One pull request in flight.** A pull request is in flight while a check runs on it, a
   review runs on it, or an approve waits for the merge. While one is in flight, open no
   other pull request (issue, relaunch or next slice) and push to no other branch. A pull
   request under rule 7 is not in flight.
6. **No comment after an approve.** No reply to the notes, no summary, no acknowledgement.
   Notes marked "no action before merge" go to a follow-up pull request after the merge.
   Before an approve, comment only with a pushed fix, a hold, or the continuation line
   of rule 3. Never write a line that begins with `Verdict:`: that line is the review's.
7. **Owner decisions are left alone:** a pull request on the branch
   `ci/budget-counts-contract-reviews`, anything labelled `hold` or `do-not-merge`, a draft.
