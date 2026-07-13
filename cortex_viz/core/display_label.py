"""Derive a short display label from a raw identifier (Gap: entity/wiki
labels showing full paths or qualified names instead of a human-scannable
short form).

Pure core logic. Stdlib only, no I/O.
"""

from __future__ import annotations


def derive_display_label(raw: str, entity_type: str | None = None) -> str:
    """Derive a short display label from ``raw``.

    Precondition: ``raw`` is any string (possibly empty/None-ish already
    coerced to ``""`` by the caller); ``entity_type`` is the optional
    knowledge-graph entity type used to strip a ``"<type>:"`` prefix.
    Postcondition: returns a non-empty string when ``raw`` is non-empty
    (never returns ``""`` for non-empty input); returns ``raw`` unchanged
    when no rule applies.

    Rules, applied in order:
      1. Strip surrounding whitespace. Empty input returns ``raw`` as-is.
      2. If ``entity_type`` is given and ``s`` starts with
         ``f"{entity_type}:"``, drop that prefix and continue with the
         remainder (e.g. ``"import:SwiftUI"`` + ``entity_type="import"``
         -> ``"SwiftUI"``).
      3. If ``"::"`` appears in ``s``, take the last non-empty segment of
         the ``"::"`` split (e.g. ``"video/generate.py::Particle::alive"``
         -> ``"alive"``; ``"Process — process::tests_py/a.py::test_x"``
         -> ``"test_x"``).
      4. Else, if ``"/"`` appears in ``s`` AND ``s`` contains no
         whitespace, take the last non-empty segment of the ``"/"`` split
         (e.g. ``"~/x/plugins/"`` -> ``"plugins"``, ``"/tmp/c1.out"`` ->
         ``"c1.out"``). The whitespace guard keeps prose/titles that
         happen to contain a ``/`` (e.g. "Decision: migrate from MySQL /
         PostgreSQL") intact.
      5. Else, return ``s`` unchanged.

    Never returns an empty string for non-empty input: each segment-pick
    falls back to the pre-split string, and the whole function falls back
    to ``raw`` if somehow ``s`` ended up empty.
    """
    s = (raw or "").strip()
    if not s:
        return raw

    if entity_type and s.startswith(f"{entity_type}:"):
        s = s[len(entity_type) + 1 :]

    if "::" in s:
        parts = [p for p in s.split("::") if p]
        if parts:
            s = parts[-1]
    elif "/" in s and not any(ch.isspace() for ch in s):
        parts = [p for p in s.split("/") if p]
        if parts:
            s = parts[-1]

    return s or raw


__all__ = ["derive_display_label"]
