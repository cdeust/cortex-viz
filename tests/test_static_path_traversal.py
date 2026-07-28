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
