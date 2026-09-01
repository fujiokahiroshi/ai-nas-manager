from pathlib import Path

import pytest

import event_queue
import server


@pytest.fixture(autouse=True)
def isolated_queue(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(event_queue, "DB_PATH", tmp_path / "events.sqlite3")


def test_receive_view_events_and_ack() -> None:
    first = event_queue.publish(
        "windows-view",
        "nas",
        "playback_state",
        {"command": "stop"},
    )
    second = event_queue.publish(
        "windows-view",
        "nas",
        "selection",
        {"index": 1},
    )

    result = server.receive_view_events(acknowledge=True)

    assert [event["id"] for event in result["events"]] == [first.id, second.id]
    assert event_queue.get_cursor("ai-nas-manager") == second.id


def test_receive_uses_consumer_cursor_by_default() -> None:
    first = event_queue.publish("view", "nas", "one")
    second = event_queue.publish("view", "nas", "two")
    event_queue.acknowledge("agent-a", first.id)

    result = server.receive_view_events(consumer="agent-a")

    assert [event["id"] for event in result["events"]] == [second.id]


def test_acknowledge_view_events_does_not_move_backwards() -> None:
    assert server.acknowledge_view_events(8)["last_event_id"] == 8
    assert server.acknowledge_view_events(2)["last_event_id"] == 8


def test_publish_view_event_is_visible_to_view_target() -> None:
    published = server.publish_view_event(
        "playback_command",
        {"command": "seek", "seconds": 9.5},
        dedupe_key="seek-1",
    )

    queued = event_queue.list_events("view")

    assert queued[0].id == published["id"]
    assert queued[0].payload["seconds"] == 9.5


def test_event_status_reports_queue_positions() -> None:
    inbound = event_queue.publish("view", "nas", "connected")
    outbound = event_queue.publish("nas", "view", "refresh")
    event_queue.acknowledge("test-consumer", inbound.id)

    status = server.get_view_event_status("test-consumer")

    assert status["nas_latest_event_id"] == inbound.id
    assert status["view_latest_event_id"] == outbound.id
    assert status["consumer_last_event_id"] == inbound.id
    assert status["event_api"]["port"] == server.event_api.EVENT_API_PORT


def test_publish_play_event_adds_windows_file_uris() -> None:
    published = server.publish_view_event(
        "playback_command",
        {
            "command": "play",
            "source_value": "/home/hiroshi/video sample.mp4",
            "thumbnail_path": "/home/hiroshi/thumb 日本語.png",
        },
    )

    assert (
        published["payload"]["source_uri"]
        == "file://wsl.localhost/Ubuntu/home/hiroshi/video%20sample.mp4"
    )
    assert published["payload"]["thumbnail_uri"].startswith(
        "file://wsl.localhost/Ubuntu/home/hiroshi/"
    )
