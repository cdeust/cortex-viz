"""Claude hook endpoint discovery and fail-safe delivery contracts."""

from __future__ import annotations

import io
import json
from pathlib import Path

from cortex_viz.hooks import activity_capture


def _registry(tmp_path: Path, port: int) -> None:
    path = tmp_path / ".cache" / "cortex" / "viz-server.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"pid": 1, "port": port}), encoding="utf-8")


def test_candidates_fall_through_stale_registry_to_launcher_default(
    tmp_path, monkeypatch
):
    _registry(tmp_path, 9999)
    monkeypatch.setattr(activity_capture.Path, "home", lambda: tmp_path)
    monkeypatch.delenv("CORTEX_VIZ_URL", raising=False)
    monkeypatch.delenv("CORTEX_VIZ_PORT", raising=False)

    assert activity_capture._candidate_urls() == [
        "http://127.0.0.1:9999/api/activity",
        "http://127.0.0.1:3458/api/activity",
    ]


def test_explicit_url_is_authoritative(monkeypatch):
    monkeypatch.setenv("CORTEX_VIZ_URL", "http://127.0.0.1:4000/")

    assert activity_capture._candidate_urls() == [
        "http://127.0.0.1:4000/api/activity"
    ]


def test_main_retries_immediate_refusal_without_exceeding_shared_budget(
    tmp_path, monkeypatch
):
    _registry(tmp_path, 9999)
    monkeypatch.setattr(activity_capture.Path, "home", lambda: tmp_path)
    monkeypatch.delenv("CORTEX_VIZ_URL", raising=False)
    monkeypatch.delenv("CORTEX_VIZ_PORT", raising=False)
    monkeypatch.setattr(
        activity_capture.sys,
        "stdin",
        io.StringIO(json.dumps({"session_id": "s1", "tool_name": "Read"})),
    )
    attempts = []

    class _Response:
        def read(self):
            return b""

    def _open(request, *, timeout):
        attempts.append((request.full_url, timeout))
        if request.full_url.endswith(":9999/api/activity"):
            raise ConnectionRefusedError
        return _Response()

    monkeypatch.setattr(activity_capture.urllib.request, "urlopen", _open)

    activity_capture.main()

    assert [url for url, _timeout in attempts] == [
        "http://127.0.0.1:9999/api/activity",
        "http://127.0.0.1:3458/api/activity",
    ]
    assert all(0 < timeout <= activity_capture._TIMEOUT_S for _, timeout in attempts)
