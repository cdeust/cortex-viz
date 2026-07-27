# Contributing to cortex-viz

cortex-viz is the read-only visualization layer for
[Cortex](https://github.com/cdeust/Cortex). It is a small project with a
single maintainer, so this document states the process plainly rather than
describing a larger organisation than exists. Roles and decision rights are in
[GOVERNANCE.md](GOVERNANCE.md).

## The contribution process

Every change lands through a **pull request**. There is no direct push to
`main`.

1. **Open an issue first** for anything larger than a typo. Describe the
   behaviour you observed and the behaviour you expected. If you are proposing
   a feature, say which of the six views it changes and what a user reads
   differently afterwards.
2. **Fork and branch.** Branch names follow the type of work:
   `fix/...`, `feat/...`, `test/...`, `refactor/...`, `docs/...`,
   `supply-chain/...`.
3. **Open the pull request** against `main`, referencing the issue number.
   Describe what changed, how you verified it, and what you did not cover.
4. **CI must be green.** The `test` job (pytest) and the `js-test` job
   (vitest) are both required. A failing JS test fails the build exactly like
   a failing Python test.
5. **The maintainer reviews and merges.** Expect a first response within 14
   days. If a change is refused, the reason is written in the pull request, not
   left implicit.

Issues and pull requests are public and searchable:
<https://github.com/cdeust/cortex-viz/issues>.

Security problems do **not** go through this process. See
[SECURITY.md](SECURITY.md): open a private advisory instead.

## Requirements for an acceptable contribution

### Coding style

| Language | Style guide | Tool |
|---|---|---|
| Python | [PEP 8](https://peps.python.org/pep-0008/), with the project's `[tool.ruff]` settings in `pyproject.toml` | [`ruff`](https://docs.astral.sh/ruff/) |
| JavaScript (`ui/`) | Vanilla browser ESM and IIFE, no bundler, no framework. Match the file you are editing. | none enforced yet (see below) |

Ruff is the selected Python linter and its configuration lives in
`pyproject.toml`. It is **not yet a required CI gate**: the existing tree does
not pass a clean run, and wiring the gate before the cleanup would block every
contributor on debt they did not create. Tracking issue:
[#45](https://github.com/cdeust/cortex-viz/issues/45). Until that gate lands,
run `ruff check <the files you changed>` and leave your files no worse than you
found them.

### Structural limits

These are enforced by review, and are why issues like
[#41](https://github.com/cdeust/cortex-viz/issues/41) and
[#17](https://github.com/cdeust/cortex-viz/issues/17) exist:

- **500 lines** per file
- **50 lines** per function
- **4 parameters** per function
- **3 levels** of nesting

A file that outgrows the cap is split along a concern boundary, not reformatted
to hide the size.

### Tests are mandatory for new functionality

**As major new functionality is added, tests for that functionality MUST be
added to the automated test suite in the same pull request.** This is a
requirement, not a preference, and a pull request that adds behaviour without
tests will be sent back.

- **Python:** `pytest` suites under `tests/`. Run `python -m pytest`.
- **Browser UI:** `vitest` + `jsdom` suites under `tests/js/`. Run `npm test`.
- **Bug fixes:** a regression test that fails on the pre-fix code is required.
  This is how the fix is shown to be a fix.

Test **strength** is judged by mutation, not by line coverage. The scoped
Stryker gate is configured in `stryker.conf.json`, and surviving mutants are
triaged in `tests/js/MUTATION_NOTES.md`. A suite that executes a line but would
not fail if the line were wrong does not count as covering it.

### Running the suites

```bash
# Python
python -m pip install -e ".[dev]"
python -m pytest

# Browser UI
npm ci
npm test
npm run test:mutation   # scoped Stryker gate
```

### Documentation

A change a user can observe updates the `CHANGELOG.md` `[Unreleased]` section
in the same pull request. An interface change updates the README in the same
pull request.

### Copy

Published copy in this repository carries **no em dashes**. Use a colon, a
comma, a full stop, or parentheses. This is a house rule applied in
[#31](https://github.com/cdeust/cortex-viz/issues/31) and
[#32](https://github.com/cdeust/cortex-viz/issues/32), and it applies to
README, docs, panel strings, and anything else a reader sees.

## Sign your work

There is no DCO bot and no CLA. By opening a pull request you affirm that you
wrote the contribution, or otherwise have the right to submit it under the
repository's [MIT license](LICENSE).
