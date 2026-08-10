# Mutation testing — Python scoped-run notes (issue #74)

Per coding-standards §12, mutation testing (not line coverage) is the strength
gate on changed logic. This documents the scoped `mutmut` runs closing
issue #74's pre-existing backlog (136 survivors, discovered while wiring
`py/path-injection` fixes — see the issue for the original breakdown) in
the three path-guard modules, and triages every surviving mutant: killed, or
documented-equivalent. No survivor is left un-triaged. Format follows the
existing JS precedent (`tests/js/MUTATION_NOTES.md`).

Run: `scripts/mutation_check.sh <tests> <module>` (mutmut 3.x, capped at
`--max-children 3` for these runs — the machine was shared with another
session's measurement campaign; see the PR for load/disk snapshots per run).

## `cortex_viz/infrastructure/wiki_read.py`

Tests: `tests/test_wiki_read.py`. **362 mutants generated, 355 killed, 7
equivalent, 0 unexplained survivors** (was 67 survivors before this change).

### Equivalent mutants (documented, not ignored)

- **`x__page_item__mutmut_11`, `x_read_page__mutmut_25`,
  `x_list_bibliography__mutmut_23`, `x_read_bibliography__mutmut_25`,
  `x_save_page__mutmut_24` — `encoding="utf-8"` → `encoding="UTF-8"`.**
  Python's codec registry normalises encoding names case-insensitively
  (confirmed: `codecs.lookup("UTF-8") is codecs.lookup("utf-8")` →
  `True`). The mutated call resolves to the exact same codec object as the
  original; no input can distinguish them. Equivalent for every observer.

- **`x_list_bibliography__mutmut_18`, `x_list_bibliography__mutmut_20` —
  `encoding="utf-8"` dropped / set to `None` on the `.bib` read.** Unlike
  the other four functions in this module, `list_bibliography`'s decoded
  `text` is never returned to the caller — only `text.count("\n@")` and
  `text.lstrip().startswith("@")` are ever inspected, and both operations
  only look for `'@'`, `'\n'`, and whitespace, which are single-byte ASCII
  bytes (0x40, 0x0A, ...) that decode identically to the same characters
  under every ASCII-compatible encoding this codebase runs on (UTF-8,
  Latin-1, the platform-default fallback, ...). Combined with
  `errors="replace"` (untouched by these two mutants, so no exception is
  possible), a wrong encoding can only corrupt non-ASCII byte sequences
  elsewhere in the string — which affects neither the entry count nor the
  leading-entry check nor `size` (`bib.stat().st_size`, a filesystem
  property independent of how the bytes are later decoded). Verified this
  is not merely "hard to trigger" but structurally unobservable: every
  consumer of the decoded value is ASCII-only.

### Notable kills — the *why*, not just the assertion

- **`x__page_item__mutmut_6/7/8/9/12/13`, `x_read_page__mutmut_20/21/22/23/26/27`,
  `x_list_bibliography__mutmut_19/21/24/25`, `x_read_bibliography__mutmut_20/21/22/23/26/27`,
  `x_save_page__mutmut_20/22`
  — the `errors="replace"` / `encoding="utf-8"` keyword pair on every
  `read_text`/`write_text` call, dropped, set to `None`, or given an
  invalid handler name (`"REPLACE"`, mutmut's `"XX...XX"` marker
  wrapping).** Two distinct probes, chosen because the failure modes
  differ:
  - *Invalid/absent error handler*: a fixture file containing a raw
    invalid UTF-8 byte (`\xff`) forces the handler to actually engage.
    `errors=None` (≡ `strict`) raises `UnicodeDecodeError`; an unknown
    handler name (`"REPLACE"`, `"XXreplaceXX"` — verified via
    `codecs.lookup_error`, both fail lookup) raises `LookupError`.
    Neither is an `OSError`, so neither is caught by the function's
    `except OSError:` — the mutant crashes instead of gracefully
    replacing the byte. One fixture file kills every variant of this
    mutation simultaneously.
  - *Wrong/absent encoding*: `locale.setlocale(locale.LC_ALL, "C")`
    called **in-process** (not via a subprocess — see below) forces the
    C library's preferred encoding to US-ASCII for the duration of the
    test, then a page/bib file with a real non-ASCII character (`café`)
    is read or written. The pinned `encoding="utf-8"` round-trips it
    correctly regardless; `encoding=None` (or the kwarg dropped, which
    defaults to the same `None`) decodes/encodes it wrong (`read_text`
    replaces the multi-byte sequence with `U+FFFD` twice; `write_text`
    raises `UnicodeEncodeError`, again uncaught by `except OSError:`).

- **A subprocess-with-`LC_ALL=C` approach was tried first and abandoned.**
  It reproduces the real divergence (verified manually: a forced
  `LC_ALL=C` subprocess genuinely garbles `café` without the pin), but
  mutmut selects which tests to run against a given mutant by tracing
  which lines a test's *own process* executes during its coverage/stats
  pass — a spawned child process's execution is invisible to that trace.
  A subprocess-based test is therefore silently never scheduled against
  the mutant it exists to kill: it passes in isolation, yet the mutant
  it targets still reports "survived" in the real run (reproduced this
  exact symptom before switching approaches). `locale.setlocale` changes
  the *same* process's C-library locale state at runtime — verified that
  `Path.read_text`/`Path.write_text` genuinely consult it, whereas
  monkeypatching the higher-level `locale.getencoding()` /
  `locale.getpreferredencoding()` Python functions does **not** reach
  them (both tried and shown ineffective) — so it is both a real
  behavioural probe and one mutmut's coverage tracer can see.

- **`x__page_item__mutmut_70/71/72` (`meta.get("updated")` →
  `meta.get(None)` / `"updated"` mutmut-marker-wrapped / `"UPDATED"`)** —
  killed by a fixture with distinct `updated` and `tended` values,
  asserting the `updated` one wins (the same precedence pattern already
  covered for `created`-over-`date`).

- **`x_read_page__mutmut_14–39` and the `x_save_page__mutmut_31/32` /
  `x_read_bibliography__mutmut_28–30` "dict key" mutants** (`"error"` →
  `"ERROR"`, `"path"` → `"PATH"`, etc.) — the individual-field assertions
  already in the suite (`got["meta"]["tags"]`, …) don't pin the *exact*
  key set; added full-dict-equality assertions on the success and
  not-found/OSError response shapes, which pin every key name and value
  simultaneously.

- **`x_list_bibliography__mutmut_32` (`text.lstrip()` → `text.rstrip()`
  in the leading-entry check)** — killed with a `.bib` fixture whose
  first entry is preceded by *leading whitespace* (not a `\n@` pattern):
  `lstrip()` strips it and the entry is counted; `rstrip()` (which only
  touches the trailing end) leaves it and the entry is missed.

- **`x_list_bibliography__mutmut_34` (`else 0` → `else 1` in the leading-
  entry check)** — killed with a `.bib` fixture containing zero `@`
  entries at all; the mutant unconditionally reports one anyway.

- **`x_list_bibliography__mutmut_44` (`except OSError: continue` →
  `break`)** — killed with two `.bib` files where the *first* (in sorted
  order) raises `OSError` on read; `continue` reaches the second file,
  `break` silently drops it from the result.

- **`x_save_page__mutmut_3/5`, `x_read_bibliography__mutmut_3/5`
  (`_safe_path(..., suffix=".md"/".bib")` → `suffix=None`, or the kwarg
  dropped)** — killed by asserting the wrong-suffix refusal directly
  (`save_page("notes.txt", ...)`, `read_bibliography(".../notes.txt")`),
  which the suite exercised for `read_page` but not for these two
  siblings.

## `cortex_viz/server/http_standalone_static.py`

Tests: `tests/test_static_path_traversal.py`. *(recorded after that
module's run — see PR for the exact counts.)*

## `cortex_viz/server/http_file_diff.py`

Tests: `tests/test_git_diff_engine.py,tests/test_file_diff.py`. *(recorded
after that module's run — see PR for the exact counts.)*
