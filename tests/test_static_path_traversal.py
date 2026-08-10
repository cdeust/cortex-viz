"""Adversarial path-traversal tests for the sandboxed static readers.

CodeQL flags ``serve_shared_asset`` with ``py/path-injection`` (10 open alerts
across the static/diff readers). A finding is not a bug until it is
reproduced, so these tests attack the guards directly with the payloads the
rule implies: dot-dot segments, absolute paths, null bytes, symlink escapes,
and the encodings the HTTP layer has already decoded by the time these
functions see them.

Each test asserts the OBSERVABLE effect — the status code sent and whether
any body was written — never merely that no exception was raised.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from cortex_viz.server.http_standalone_static import serve_shared_asset, serve_static


class FakeHandler:
    """Records what a reader wrote, so a leak is observable rather than implied."""

    def __init__(self) -> None:
        self.status: int | None = None
        self.headers: dict[str, str] = {}
        self.body = b""
        self.wfile = self

    def send_response(self, code: int) -> None:
        self.status = code

    def send_header(self, k: str, v: str) -> None:
        self.headers[k] = v

    def end_headers(self) -> None:
        pass

    def write(self, data: bytes) -> None:
        self.body += data

    # send_plain_error is what the guards call on rejection.
    def send_error(self, code: int, message: str | None = None) -> None:
        self.status = code


@pytest.fixture
def sandbox(tmp_path: Path) -> Path:
    """A shared/ foundation dir with one legitimate nested asset."""
    shared = tmp_path / "ui" / "shared"
    (shared / "tokens").mkdir(parents=True)
    (shared / "ds.css").write_text("/* ds */", encoding="utf-8")
    (shared / "tokens" / "colors.css").write_text("/* colors */", encoding="utf-8")
    # The secret that must never be served.
    (tmp_path / "secret.txt").write_text("TOP-SECRET", encoding="utf-8")
    return shared


# Payloads as the reader receives them — the HTTP layer has already
# percent-decoded, so %2e%2e arrives as '..' and %00 as a real null byte.
TRAVERSALS = [
    "../secret.txt",
    "../../secret.txt",
    "tokens/../../secret.txt",
    "./../secret.txt",
    "/etc/passwd",
    "//etc/passwd",
    "tokens/../../../../../../etc/passwd",
    "..",
    "../",
    ".hidden",
    "tokens/.hidden",
    "ds.css\x00.png",
    "\x00",
    "",
]


@pytest.mark.parametrize("payload", TRAVERSALS)
def test_shared_asset_refuses_traversal(sandbox: Path, payload: str) -> None:
    h = FakeHandler()
    serve_shared_asset(h, sandbox, payload)
    assert h.status in (403, 404), f"{payload!r} was not refused (status {h.status})"
    assert h.body == b"", f"{payload!r} leaked {h.body!r}"
    assert b"TOP-SECRET" not in h.body


def test_shared_asset_serves_a_legitimate_nested_asset(sandbox: Path) -> None:
    """The guard must not be so tight that the real @import tree stops working."""
    h = FakeHandler()
    serve_shared_asset(h, sandbox, "tokens/colors.css")
    assert h.status == 200
    assert h.body == b"/* colors */"
    assert h.headers["Content-Type"].startswith("text/css")


def test_shared_asset_refuses_a_symlink_escaping_the_sandbox(sandbox: Path) -> None:
    """A symlink INSIDE the foundation pointing outside it must not be followed
    to a served body — the containment check is on the RESOLVED path."""
    link = sandbox / "escape.css"
    link.symlink_to(sandbox.parent.parent / "secret.txt")
    h = FakeHandler()
    serve_shared_asset(h, sandbox, "escape.css")
    assert h.status == 403, f"symlink escape returned {h.status}"
    assert b"TOP-SECRET" not in h.body


def test_shared_asset_refuses_a_symlinked_directory_escape(sandbox: Path) -> None:
    """Same, one level up: a symlinked SUBDIRECTORY must not open the parent."""
    (sandbox / "out").symlink_to(sandbox.parent.parent, target_is_directory=True)
    h = FakeHandler()
    serve_shared_asset(h, sandbox, "out/secret.txt")
    assert h.status == 403
    assert b"TOP-SECRET" not in h.body


@pytest.fixture
def flat_dir(tmp_path: Path) -> Path:
    d = tmp_path / "js"
    d.mkdir()
    (d / "config.js").write_text("// cfg", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("TOP-SECRET", encoding="utf-8")
    return d


@pytest.mark.parametrize("payload", TRAVERSALS)
def test_static_refuses_traversal(flat_dir: Path, payload: str) -> None:
    h = FakeHandler()
    serve_static(h, flat_dir, payload, "application/javascript")
    assert h.status in (403, 404), f"{payload!r} was not refused (status {h.status})"
    assert h.body == b""


def test_static_serves_a_whitelisted_file(flat_dir: Path) -> None:
    h = FakeHandler()
    serve_static(h, flat_dir, "config.js", "application/javascript")
    assert h.status == 200
    assert h.body == b"// cfg"


def test_static_refuses_a_symlink_escaping_the_directory(flat_dir: Path) -> None:
    """serve_static whitelists by directory listing, so a symlink IS listed —
    the read must still not escape."""
    (flat_dir / "evil.js").symlink_to(flat_dir.parent / "secret.txt")
    h = FakeHandler()
    serve_static(h, flat_dir, "evil.js", "application/javascript")
    assert b"TOP-SECRET" not in h.body, "symlink in a whitelisted dir leaked the target"
    assert h.status in (403, 404)


def test_shared_asset_rejects_absolute_windows_style_path(sandbox: Path) -> None:
    """Backslash segments must not slip past a '/'-only component split."""
    h = FakeHandler()
    serve_shared_asset(h, sandbox, "..\\..\\secret.txt")
    assert h.status in (403, 404)
    assert b"TOP-SECRET" not in h.body


def test_no_reader_follows_a_dangling_or_special_target(sandbox: Path) -> None:
    """A non-regular file (fifo/dir) must 404, never stream."""
    h = FakeHandler()
    serve_shared_asset(h, sandbox, "tokens")  # a directory, not a file
    assert h.status == 404
    assert h.body == b""


def test_sandbox_root_itself_is_not_served(sandbox: Path) -> None:
    h = FakeHandler()
    serve_shared_asset(h, sandbox, ".")
    assert h.status in (403, 404)
    assert h.body == b""


def test_environment_has_no_symlinked_tmp_false_negative(sandbox: Path) -> None:
    """Guard against a vacuous suite: if tmp_path were itself inside the
    sandbox, every escape test would pass for the wrong reason."""
    assert not str(sandbox.parent.parent).startswith(str(sandbox.resolve()))
    assert os.path.exists(sandbox.parent.parent / "secret.txt")


def test_http_server_static_reader_shares_the_hardened_guard(flat_dir: Path) -> None:
    """``http_server._serve_static`` used to be a byte-for-byte duplicate of the
    vulnerable reader, so fixing one left the other exploitable. It must now
    delegate — same symlink escape, same refusal."""
    from cortex_viz.server.http_server import _serve_static

    (flat_dir / "evil.js").symlink_to(flat_dir.parent / "secret.txt")
    h = FakeHandler()
    _serve_static(h, flat_dir, "evil.js", "application/javascript")
    assert b"TOP-SECRET" not in h.body, "the duplicate reader still leaks"
    assert h.status in (403, 404)


def test_http_server_static_reader_still_serves_legitimate_files(
    flat_dir: Path,
) -> None:
    """Delegation must not break the methodology UI's own asset serving."""
    from cortex_viz.server.http_server import _serve_static

    h = FakeHandler()
    _serve_static(h, flat_dir, "config.js", "application/javascript")
    assert h.status == 200
    assert h.body == b"// cfg"


# ── response framing — headers are part of the contract, not incidental ──
# A body-only assertion cannot distinguish "sent the right Content-Type" from
# "sent an empty/wrong one" -- these pin the framing headers both readers
# promise on a 200.


def test_static_success_sends_correct_framing_headers(flat_dir: Path) -> None:
    h = FakeHandler()
    serve_static(h, flat_dir, "config.js", "application/javascript")
    assert h.status == 200
    assert h.headers["Content-Type"] == "application/javascript; charset=utf-8"
    assert h.headers["Content-Length"] == str(len(b"// cfg"))
    assert h.headers["Cache-Control"] == "no-cache"


def test_shared_asset_success_sends_correct_framing_headers(sandbox: Path) -> None:
    h = FakeHandler()
    serve_shared_asset(h, sandbox, "tokens/colors.css")
    assert h.status == 200
    body = b"/* colors */"
    assert h.headers["Content-Type"] == "text/css; charset=utf-8"
    assert h.headers["Content-Length"] == str(len(body))
    assert h.headers["Cache-Control"] == "no-cache"


# ── shared-asset content-type table — every mapped extension, plus the
#    unmapped fallback, pinned individually (a swapped dict value produces
#    a wrong-but-still-served response no status/body assertion catches) ──


@pytest.mark.parametrize(
    ("filename", "expected_type"),
    [
        ("f.css", "text/css"),
        ("f.js", "application/javascript"),
        ("f.mjs", "application/javascript"),
        ("f.json", "application/json"),
        ("f.woff2", "font/woff2"),
        ("f.woff", "font/woff"),
        ("f.ttf", "font/ttf"),
        ("f.svg", "image/svg+xml"),
        ("f.unknownext", "text/plain"),
    ],
)
def test_shared_asset_content_type_table(
    tmp_path: Path, filename: str, expected_type: str
) -> None:
    shared = tmp_path / "shared"
    shared.mkdir()
    (shared / filename).write_bytes(b"data")
    h = FakeHandler()
    serve_shared_asset(h, shared, filename)
    assert h.status == 200
    assert h.headers["Content-Type"] == f"{expected_type}; charset=utf-8"


def test_shared_asset_content_type_lookup_is_case_insensitive_on_suffix(
    tmp_path: Path,
) -> None:
    shared = tmp_path / "shared"
    shared.mkdir()
    (shared / "f.CSS").write_bytes(b"data")
    h = FakeHandler()
    serve_shared_asset(h, shared, "f.CSS")
    assert h.status == 200
    assert h.headers["Content-Type"] == "text/css; charset=utf-8"


# ── serve_static filename-whitelist regex — each admitted/rejected class
#    of first character and body character, pinned independently ────────


@pytest.mark.parametrize(
    ("filename", "should_serve"),
    [
        ("123.js", True),  # digit-led name is a legal identifier
        ("_private.js", True),  # underscore-led name is a legal identifier
        ("-leading-dash.js", False),  # '-' is not \w -- rejected as first char
        ("a b.js", False),  # embedded space is not in the allowed class
    ],
)
def test_static_filename_whitelist_boundary(
    tmp_path: Path, filename: str, should_serve: bool
) -> None:
    d = tmp_path / "js"
    d.mkdir()
    (d / filename).write_text("x", encoding="utf-8")
    h = FakeHandler()
    serve_static(h, d, filename, "application/javascript")
    if should_serve:
        assert h.status == 200
        assert h.body == b"x"
    else:
        assert h.status == 403
        assert h.body == b""


def test_static_directory_entry_is_not_served(tmp_path: Path) -> None:
    """A directory sharing a name with a would-be file must 404, not be
    treated as content -- the whitelist is built with ``is_file()`` only."""
    d = tmp_path / "js"
    d.mkdir()
    (d / "sub").mkdir()
    h = FakeHandler()
    serve_static(h, d, "sub", "application/javascript")
    assert h.status == 404
    assert h.body == b""


def test_static_unknown_filename_is_404_not_403(flat_dir: Path) -> None:
    """A name that passes the regex whitelist but names nothing on disk is a
    plain 404 (unknown), distinct from a 403 (rejected shape)."""
    h = FakeHandler()
    serve_static(h, flat_dir, "does-not-exist.js", "application/javascript")
    assert h.status == 404
    assert h.body == b""


def test_static_symlink_escape_is_exactly_403_not_404(flat_dir: Path) -> None:
    # Distinguishes the containment refusal (403, deliberately rejected)
    # from a plain not-found (404) -- both are "safe" outcomes but only one
    # is the correct status for a name that WAS found and refused.
    (flat_dir / "evil.js").symlink_to(flat_dir.parent / "secret.txt")
    h = FakeHandler()
    serve_static(h, flat_dir, "evil.js", "application/javascript")
    assert h.status == 403
    assert h.body == b""


# ── serve_shared_asset segment-rejection pre-check — the ``or``-chain over
#    (empty / ".." / dot-prefixed) is checked PER SEGMENT before containment
#    ever runs. resolve_under alone catches actual escapes, so these
#    assertions must construct a payload that IS legitimately contained yet
#    must still be refused pre-check, as defense-in-depth against ambiguous
#    input -- otherwise the pre-check reads as untestable dead code when it
#    is not. ────────────────────────────────────────────────────────────


def test_shared_asset_refuses_a_dot_prefixed_segment_even_if_the_file_exists(
    sandbox: Path,
) -> None:
    (sandbox / ".hidden-but-real.css").write_text("/* h */", encoding="utf-8")
    h = FakeHandler()
    serve_shared_asset(h, sandbox, ".hidden-but-real.css")
    assert h.status == 403, "a dot-prefixed segment must be refused pre-check"
    assert h.body == b""


def test_shared_asset_refuses_an_empty_segment_even_if_it_resolves_to_a_real_file(
    sandbox: Path,
) -> None:
    # A doubled separator produces an empty path segment; POSIX path
    # resolution would collapse it to the real, legitimately-contained
    # file -- the segment-level pre-check must refuse it before that
    # resolution ever happens.
    h = FakeHandler()
    serve_shared_asset(h, sandbox, "tokens//colors.css")
    assert h.status == 403, "an empty path segment must be refused pre-check"
    assert h.body == b""


def test_shared_asset_refuses_a_dotdot_segment_even_when_it_nets_to_a_contained_file(
    sandbox: Path,
) -> None:
    # "tokens/../ds.css" resolves (via resolve_under) to the legitimately
    # contained ds.css -- the pre-check must still refuse the literal ".."
    # segment outright, never delegating the decision to containment alone.
    h = FakeHandler()
    serve_shared_asset(h, sandbox, "tokens/../ds.css")
    assert h.status == 403, "a literal '..' segment must be refused pre-check"
    assert h.body == b""


def test_shared_asset_segment_refusal_is_exactly_403_not_404(sandbox: Path) -> None:
    h = FakeHandler()
    serve_shared_asset(h, sandbox, "")
    assert h.status == 403


# ── serve_file_diff — the delegate must forward BOTH the handler and the
#    store intact, not drop either or swap positions. A "no name given"
#    smoke call can't distinguish a dropped ``store`` (both produce the
#    same reason); using a bare-basename query routes through the
#    store-dependent resolver arm, whose reason text differs by whether a
#    real store object crossed the delegation boundary. ─────────────────


class _FakeDiffHandler:
    def __init__(self, path: str) -> None:
        self.path = path
        self.status: int | None = None
        self.headers: dict[str, str] = {}
        self.body = b""
        self.wfile = self

    def send_response(self, code: int) -> None:
        self.status = code

    def send_header(self, k: str, v: str) -> None:
        self.headers[k] = v

    def end_headers(self) -> None:
        pass

    def write(self, data: bytes) -> None:
        self.body += data


def _diff_reason(h: _FakeDiffHandler) -> str:
    import json

    return json.loads(h.body)["reason"]


def test_serve_file_diff_forwards_the_real_handler_not_none() -> None:
    from cortex_viz.server.http_standalone_static import serve_file_diff

    # handler=None would crash resolving handler.path before any JSON is
    # ever written -- reaching a clean JSON response at all proves the
    # real handler crossed the delegation boundary.
    h = _FakeDiffHandler("/api/file-diff?name=foo.py")
    serve_file_diff(h, store=None)
    assert h.status == 200
    assert _diff_reason(h) == "unresolved basename: activity store unavailable"


def test_serve_file_diff_forwards_the_real_store_not_none() -> None:
    from cortex_viz.server.http_standalone_static import serve_file_diff

    # A non-None store routes _resolve_by_basename into the lookup-attempt
    # arm (a different reason string than the store-absent arm above) --
    # this only happens if `store` genuinely crossed the boundary rather
    # than being dropped or defaulted.
    h = _FakeDiffHandler("/api/file-diff?name=foo.py")
    serve_file_diff(h, store=object())
    assert h.status == 200
    assert _diff_reason(h) == "unresolved basename: activity store lookup failed"
