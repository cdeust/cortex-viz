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

Tests: `tests/test_static_path_traversal.py`. **152 mutants generated, 147
killed, 5 equivalent, 0 unexplained survivors** (was 53 survivors before
this change).

### Equivalent mutants (documented, not ignored)

- **`x_serve_static__mutmut_4/5/8/9` — the `or`-chain in `serve_static`'s
  filename guard** (`startswith(".") or "\x00" in name` fused into `and`,
  or either literal turned into a mutmut marker string that can never
  match a real filename). `serve_static`'s guard ends with
  `re.match(r"^[\w][\w.\-]*$", safe_name)`, and this regex **already
  independently implies** every property the earlier clauses check:
  empty (`^[\w]` requires one char), dot-prefixed (`.` is never `\w`),
  and null-byte-containing (`\x00` is in none of `\w`, `.`, `-`) names
  all fail the regex on their own. Verified directly —
  `re.compile(r"^[\w][\w.\-]*$").match(x)` returns `False` for `''`,
  `'.'`, `'.hidden'`, and every string containing `'\x00'`, tested
  exhaustively for the relevant classes. Weakening or disabling the
  earlier clauses changes nothing observable: the regex is the load-
  bearing check; the clauses ahead of it are early-exit optimizations
  over an already-total condition, not independently-observable guards.

- **`x_serve_shared_asset__mutmut_10` — `part in ("", "..")` with `".."`
  replaced by a mutmut marker string that can never match a real path
  segment.** The same segment-rejection `any(...)` also checks
  `part.startswith(".")`, and `".."` trivially satisfies that (every
  string starting with two dots starts with one). Any segment equal to
  literal `".."` is therefore always also caught by the dot-prefix
  clause; the explicit `".."` membership check is redundant for that
  specific value. (Distinct from the OR→AND fusion mutant on this same
  line, which killed cleanly — that one disables both clauses
  simultaneously for a plain empty segment, which does NOT start with
  `"."` and has no other catching clause.)

### Notable kills — the *why*, not just the assertion

- **`x_serve_static__mutmut_36`, `x_serve_shared_asset__mutmut_19` (403 →
  404 on the containment/segment-rejection refusal paths)** — the existing
  traversal-payload tests asserted `status in (403, 404)` (both are "safe"
  outcomes), which cannot distinguish a deliberate refusal from a plain
  not-found. Added exact-403 assertions for a symlink escape (`serve_static`)
  and an empty `rel_path` (`serve_shared_asset`) specifically.

- **`x_serve_shared_asset__mutmut_7/9/12/13/14`
  (the `any(part in ("", "..") or part.startswith(".") for part in
  rel_path.split("/"))` segment-rejection predicate — OR fused to AND,
  each literal replaced by a dead marker string, and the split delimiter
  itself replaced by `None` or a dead marker)** — killed by three
  payloads chosen so each nets to a *legitimately contained* file if the
  pre-check is bypassed, proving the pre-check is genuine defense-in-depth
  and not redundant with `resolve_under`'s containment check alone: a
  dot-prefixed file that actually exists in the sandbox
  (`.hidden-but-real.css`), a doubled separator that POSIX would collapse
  to a real file (`tokens//colors.css`), and a literal `..` segment that
  nets back inside the sandbox (`tokens/../ds.css`). All three must be
  refused with exactly 403 even though `resolve_under` alone would have
  accepted the resolved path.

- **`x_serve_file_diff__mutmut_1/2/3/4` (`_serve(None, store)`,
  `_serve(handler, None)`, `_serve(store)` — wrong argument, dropped
  argument, wrong arity) — 0 tests were associated with this function at
  all before this change.** `serve_file_diff` here is a documented thin
  delegate to `http_file_diff.serve_file_diff`; its entire observable
  contract is "forwards both arguments intact." Killed with two tests
  that route a bare-basename query through the delegate: one with
  `store=None` (yields `"unresolved basename: activity store
  unavailable"`), one with a real store object (`store=object()`, yields
  the *different* `"unresolved basename: activity store lookup failed"`
  reason via `_resolve_by_basename`'s real lookup-exception arm). Reaching
  either reason at all rules out `handler=None` (which crashes resolving
  `handler.path` before any JSON is written); the reason *differing*
  between the two calls rules out `store` being dropped or defaulted.

## `cortex_viz/server/http_file_diff.py`

Tests: `tests/test_git_diff_engine.py,tests/test_file_diff.py`. **120
mutants generated, 120 killed, 0 equivalent, 0 survivors** (was 16
survivors before this change).

### Notable kills — the *why*, not just the assertion

- **`x__resolve_by_basename__mutmut_5`, `x__resolve_by_relative_fragment__mutmut_23`,
  `x__resolve_name__mutmut_7`, `x_serve_file_diff__mutmut_33`
  (`store` swapped for `None` at four distinct call sites along the
  resolution chain: `_resolve_by_basename` -> `find_abs_path_by_label`,
  `_resolve_by_relative_fragment` -> `find_abs_path_by_suffix`,
  `_resolve_name` -> `_resolve_by_relative_fragment`, and
  `serve_file_diff` -> `_resolve_name`)** — the existing store-forwarding
  tests monkeypatched the lookup functions with lambdas that *ignored*
  their `store` parameter, so a dropped/`None`-swapped store was
  invisible to them. Each of the four call sites needed its own
  independent proof: a lambda/closure that records every `store` value it
  actually received and asserts the list equals `[sentinel_store]` — a
  passed-through `None` swap shows up as `[None]` or a length mismatch
  instead.

- **`x__resolve_by_basename__mutmut_11` (`"unresolved basename: not found
  in activity index"` wrapped in mutmut's `"XX...XX"` marker)** — the
  existing test asserted `"unresolved basename" in reason` (substring),
  which the wrapped string still contains. Tightened to exact string
  equality, which the JS notes precedent already established as the
  right default for reason/error strings in this codebase.

- **`x_serve_file_diff__mutmut_49/50/51/52/53` (the "unresolved name"
  response dict's `"lines"`/`"truncated"` keys and the `False` -> `True`
  value mutant)** — the existing test asserted three of the five keys
  individually (`available`, `diff_type`, `reason`), missing `lines` and
  `truncated` entirely. Switched to full-dict equality against the
  literal five-key response, matching the pattern already used for the
  "no file given" branch's equivalent full-dict test.

No equivalent mutants in this module — every survivor from the original
run was a genuine test gap, not an unobservable difference.
