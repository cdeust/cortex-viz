"""Claude hook endpoint discovery and fail-safe delivery contracts."""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from cortex_viz.hooks import activity_capture
from cortex_viz.server import viz_instance


def _write_registry(tmp_path: Path, raw: str) -> None:
    path = tmp_path / ".cache" / "cortex" / "viz-server.json"
    path.parent.mkdir(parents=True)
    path.write_text(raw, encoding="utf-8")


def _registry(tmp_path: Path, port: int) -> None:
    _write_registry(tmp_path, json.dumps({"pid": 1, "port": port}))


def _isolate(tmp_path: Path, monkeypatch) -> None:
    """Point discovery at tmp_path and clear both env overrides."""
    monkeypatch.setattr(activity_capture.Path, "home", lambda: tmp_path)
    monkeypatch.delenv("CORTEX_VIZ_URL", raising=False)
    monkeypatch.delenv("CORTEX_VIZ_PORT", raising=False)


def test_candidates_fall_through_stale_registry_to_launcher_default(
    tmp_path, monkeypatch
):
    _registry(tmp_path, 9999)
    _isolate(tmp_path, monkeypatch)

    assert activity_capture._candidate_urls() == [
        "http://127.0.0.1:9999/api/activity",
        "http://127.0.0.1:3458/api/activity",
    ]


def test_reader_and_writer_agree_on_the_registry_location():
    """The hook is stdlib-only, so it re-declares the writer's path by hand.

    Nothing but this assertion keeps the two copies in agreement; a drift in
    either would make discovery silently fall through to the default port.
    """
    assert activity_capture._registry_path() == viz_instance.instance_path()


def test_explicit_url_is_authoritative(monkeypatch):
    monkeypatch.setenv("CORTEX_VIZ_URL", "http://127.0.0.1:4000/")

    assert activity_capture._candidate_urls() == ["http://127.0.0.1:4000/api/activity"]


def test_explicit_url_loses_only_trailing_slashes(monkeypatch):
    monkeypatch.setenv("CORTEX_VIZ_URL", "http://127.0.0.1:4000/proxyX//")

    assert activity_capture._candidate_urls() == [
        "http://127.0.0.1:4000/proxyX/api/activity"
    ]


@pytest.mark.parametrize("port", [1, 65535])
def test_boundary_ports_are_usable(tmp_path, monkeypatch, port):
    _registry(tmp_path, port)
    _isolate(tmp_path, monkeypatch)

    assert activity_capture._candidate_urls()[0] == (
        f"http://127.0.0.1:{port}/api/activity"
    )


def test_configured_port_is_tried_between_registry_and_default(tmp_path, monkeypatch):
    _registry(tmp_path, 9999)
    _isolate(tmp_path, monkeypatch)
    monkeypatch.setenv("CORTEX_VIZ_PORT", "4100")

    assert activity_capture._candidate_urls() == [
        "http://127.0.0.1:9999/api/activity",
        "http://127.0.0.1:4100/api/activity",
        "http://127.0.0.1:3458/api/activity",
    ]


def test_duplicate_sources_collapse_to_one_endpoint(tmp_path, monkeypatch):
    _registry(tmp_path, activity_capture._DEFAULT_PORT)
    _isolate(tmp_path, monkeypatch)
    monkeypatch.setenv("CORTEX_VIZ_PORT", str(activity_capture._DEFAULT_PORT))

    assert activity_capture._candidate_urls() == [
        f"http://127.0.0.1:{activity_capture._DEFAULT_PORT}/api/activity"
    ]


@pytest.mark.parametrize(
    ("raw", "failure_mode"),
    [
        ("[]", "JSON list has no .get -> AttributeError"),
        ('"3458"', "JSON string has no .get -> AttributeError"),
        ("{oops", "invalid JSON -> ValueError"),
        ('{"port": "not-a-port"}', "non-numeric port -> ValueError"),
        ('{"port": {}}', "unconvertible port -> TypeError"),
        ('{"port": 0}', "zero is not a usable port"),
        ('{"port": -1}', "negative is not a usable port"),
        ("{}", "no port key at all"),
    ],
)
def test_unusable_registry_never_raises_and_yields_the_default(
    tmp_path, monkeypatch, raw, failure_mode
):
    _write_registry(tmp_path, raw)
    _isolate(tmp_path, monkeypatch)

    assert activity_capture._candidate_urls() == [
        "http://127.0.0.1:3458/api/activity"
    ], failure_mode


def test_absent_registry_yields_the_default(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)

    assert activity_capture._candidate_urls() == ["http://127.0.0.1:3458/api/activity"]


@pytest.mark.parametrize("raw", ["not-a-port", "", "0", "-1"])
def test_unusable_configured_port_never_raises_and_yields_the_default(
    tmp_path, monkeypatch, raw
):
    _isolate(tmp_path, monkeypatch)
    monkeypatch.setenv("CORTEX_VIZ_PORT", raw)

    assert activity_capture._candidate_urls() == ["http://127.0.0.1:3458/api/activity"]


class _Response:
    def read(self):
        return b""


def _capture_posts(monkeypatch, refuse=()) -> list:
    """Record every request ``main`` posts; refuse the given URL suffixes."""
    posted = []

    def _open(request, *, timeout):
        posted.append((request, timeout))
        if any(request.full_url.endswith(suffix) for suffix in refuse):
            raise ConnectionRefusedError
        return _Response()

    monkeypatch.setattr(activity_capture.urllib.request, "urlopen", _open)
    return posted


def _feed(monkeypatch, event: dict) -> None:
    monkeypatch.setattr(activity_capture.sys, "stdin", io.StringIO(json.dumps(event)))


def test_main_retries_immediate_refusal_without_exceeding_shared_budget(
    tmp_path, monkeypatch
):
    _registry(tmp_path, 9999)
    _isolate(tmp_path, monkeypatch)
    _feed(monkeypatch, {"session_id": "s1", "tool_name": "Read"})
    posted = _capture_posts(monkeypatch, refuse=(":9999/api/activity",))

    activity_capture.main()

    assert [request.full_url for request, _timeout in posted] == [
        "http://127.0.0.1:9999/api/activity",
        "http://127.0.0.1:3458/api/activity",
    ]
    assert all(0 < timeout <= activity_capture._TIMEOUT_S for _, timeout in posted)


def test_posted_request_carries_the_json_activity_contract(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    _feed(monkeypatch, {"session_id": "s1", "tool_name": "Read", "ts": 7.0})
    monkeypatch.setattr(activity_capture.sys, "argv", ["activity_capture.py"])
    posted = _capture_posts(monkeypatch)

    activity_capture.main()

    (request, _timeout) = posted[0]
    assert request.method == "POST"
    assert request.get_header("Content-type") == "application/json"
    assert json.loads(request.data.decode("utf-8")) == {
        "session_id": "s1",
        "tool_name": "Read",
        "ts": 7.0,
        "event_type": "PostToolUse",
    }


def test_event_type_comes_from_argv_when_the_host_names_it(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    _feed(monkeypatch, {"prompt": "hi", "ts": 7.0})
    monkeypatch.setattr(
        activity_capture.sys, "argv", ["activity_capture.py", "UserPromptSubmit"]
    )
    posted = _capture_posts(monkeypatch)

    activity_capture.main()

    body = json.loads(posted[0][0].data.decode("utf-8"))
    assert body["event_type"] == "UserPromptSubmit"


def test_event_fields_the_host_already_set_are_preserved(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    _feed(monkeypatch, {"event_type": "PreCompact", "ts": 1.5})
    monkeypatch.setattr(
        activity_capture.sys, "argv", ["activity_capture.py", "PostToolUse"]
    )
    posted = _capture_posts(monkeypatch)

    activity_capture.main()

    body = json.loads(posted[0][0].data.decode("utf-8"))
    assert (body["event_type"], body["ts"]) == ("PreCompact", 1.5)


def test_missing_timestamp_is_stamped_from_the_clock(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    _feed(monkeypatch, {"session_id": "s1"})
    monkeypatch.setattr(activity_capture.time, "time", lambda: 1234.5)
    posted = _capture_posts(monkeypatch)

    activity_capture.main()

    assert json.loads(posted[0][0].data.decode("utf-8"))["ts"] == 1234.5


def test_an_exhausted_budget_posts_nothing(tmp_path, monkeypatch):
    """A budget spent exactly to zero must not buy a zero-timeout request."""
    _isolate(tmp_path, monkeypatch)
    _feed(monkeypatch, {"session_id": "s1"})
    clock = iter([0.0, activity_capture._TIMEOUT_S])
    monkeypatch.setattr(activity_capture.time, "monotonic", lambda: next(clock))
    posted = _capture_posts(monkeypatch)

    activity_capture.main()

    assert posted == []


def test_interactive_run_reports_the_resolved_endpoint(tmp_path, monkeypatch, capsys):
    _isolate(tmp_path, monkeypatch)
    monkeypatch.setattr(activity_capture.sys, "stdin", io.StringIO(""))
    monkeypatch.setattr(activity_capture.sys.stdin, "isatty", lambda: True)
    posted = _capture_posts(monkeypatch)

    activity_capture.main()

    assert posted == []
    assert capsys.readouterr().err.strip() == (
        "cortex-viz activity endpoint: http://127.0.0.1:3458/api/activity"
    )


def test_discovered_url_is_the_head_of_the_candidate_list(tmp_path, monkeypatch):
    _registry(tmp_path, 9999)
    _isolate(tmp_path, monkeypatch)

    assert activity_capture._discover_url() == activity_capture._candidate_urls()[0]


@pytest.mark.parametrize(
    ("raw", "rejected_because"),
    [
        ("", "nothing on stdin"),
        ("   \n", "whitespace only"),
        ("{not json", "undecodable payload"),
        ("[]", "a JSON list is not a hook event"),
        ('"PostToolUse"', "a JSON string is not a hook event"),
        ("null", "a JSON null is not a hook event"),
    ],
)
def test_unusable_stdin_posts_nothing(tmp_path, monkeypatch, raw, rejected_because):
    _isolate(tmp_path, monkeypatch)
    monkeypatch.setattr(activity_capture.sys, "stdin", io.StringIO(raw))
    posted = _capture_posts(monkeypatch)

    activity_capture.main()

    assert posted == [], rejected_because


def test_an_unreadable_stdin_posts_nothing_and_never_raises(tmp_path, monkeypatch):
    """The host contract forbids raising even when stdin itself fails."""

    class _Broken(io.StringIO):
        def read(self, *args):
            raise OSError("stream closed")

    _isolate(tmp_path, monkeypatch)
    monkeypatch.setattr(activity_capture.sys, "stdin", _Broken(""))
    posted = _capture_posts(monkeypatch)

    activity_capture.main()

    assert posted == []
