from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

import event_api
import event_queue


@pytest.fixture
def api(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(event_queue, "DB_PATH", tmp_path / "events.sqlite3")
    monkeypatch.setattr(event_api, "EVENT_API_TOKEN", None)
    server = event_api._ExclusiveThreadingHTTPServer(
        ("127.0.0.1", 0),
        event_api._Handler,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _post(url: str, body: dict) -> tuple[int, dict]:
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=2) as response:
        return response.status, json.loads(response.read())


def _get(url: str) -> tuple[int, dict]:
    with urllib.request.urlopen(url, timeout=2) as response:
        return response.status, json.loads(response.read())


def test_view_to_nas_http_round_trip(api: str) -> None:
    status, created = _post(
        f"{api}/events",
        {
            "source": "windows-view",
            "target": "nas",
            "event_type": "selection",
            "payload": {"index": 2, "label": "候補C"},
        },
    )
    event_id = created["event"]["id"]

    get_status, listed = _get(f"{api}/events?target=nas&after_id=0")

    assert status == 201
    assert get_status == 200
    assert listed["events"][0]["id"] == event_id
    assert listed["events"][0]["payload"]["label"] == "候補C"


def test_nas_to_view_and_ack(api: str) -> None:
    _, created = _post(
        f"{api}/events",
        {
            "source": "ai-nas-manager",
            "target": "view",
            "event_type": "playback_command",
            "payload": {"command": "seek", "seconds": 8.5},
        },
    )
    event_id = created["event"]["id"]

    _, listed = _get(f"{api}/events?target=view&after_id=0")
    _, acked = _post(
        f"{api}/acks",
        {"consumer": "windows-view", "last_event_id": event_id},
    )

    assert listed["events"][0]["payload"]["seconds"] == 8.5
    assert acked["last_event_id"] == event_id


def test_invalid_target_returns_400(api: str) -> None:
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        _get(f"{api}/events?target=invalid")
    assert exc_info.value.code == 400


def test_options_supports_file_view_private_network_requests(api: str) -> None:
    request = urllib.request.Request(f"{api}/events", method="OPTIONS")
    with urllib.request.urlopen(request, timeout=2) as response:
        assert response.status == 204
        assert response.headers["Access-Control-Allow-Private-Network"] == "true"


def test_token_protects_event_api(
    api: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(event_api, "EVENT_API_TOKEN", "test-secret")

    with pytest.raises(urllib.error.HTTPError) as exc_info:
        _get(f"{api}/health")
    assert exc_info.value.code == 401

    request = urllib.request.Request(
        f"{api}/health",
        headers={"Authorization": "Bearer test-secret"},
    )
    with urllib.request.urlopen(request, timeout=2) as response:
        assert response.status == 200


def test_server_allows_immediate_rebind_after_leader_exit() -> None:
    assert event_api._ExclusiveThreadingHTTPServer.allow_reuse_address is True
