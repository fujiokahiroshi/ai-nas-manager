from pathlib import Path

import pytest

import event_queue


@pytest.fixture(autouse=True)
def isolated_queue(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(event_queue, "DB_PATH", tmp_path / "events.sqlite3")


def test_publish_and_list_round_trip() -> None:
    event = event_queue.publish(
        "windows-view",
        "nas",
        "playback_state",
        {"command": "stop", "title": "日本語"},
    )

    events = event_queue.list_events("nas")

    assert [item.id for item in events] == [event.id]
    assert events[0].payload == {"command": "stop", "title": "日本語"}


def test_list_after_id_preserves_order() -> None:
    first = event_queue.publish("view", "nas", "one")
    second = event_queue.publish("view", "nas", "two")

    assert event_queue.list_events("nas", after_id=first.id) == [second]


def test_dedupe_key_returns_original_event() -> None:
    first = event_queue.publish(
        "view", "nas", "selection", {"index": 1}, dedupe_key="click-1"
    )
    duplicate = event_queue.publish(
        "view", "nas", "selection", {"index": 2}, dedupe_key="click-1"
    )

    assert duplicate == first
    assert len(event_queue.list_events("nas")) == 1


def test_ack_cursor_never_moves_backwards() -> None:
    assert event_queue.acknowledge("nas-consumer", 12) == 12
    assert event_queue.acknowledge("nas-consumer", 4) == 12
    assert event_queue.get_cursor("nas-consumer") == 12


def test_targets_are_isolated() -> None:
    event_queue.publish("view", "nas", "from-view")
    event_queue.publish("nas", "view", "from-nas")

    assert [event.event_type for event in event_queue.list_events("nas")] == [
        "from-view"
    ]
    assert [event.event_type for event in event_queue.list_events("view")] == [
        "from-nas"
    ]


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"source": "", "target": "nas", "event_type": "x"}, "source"),
        ({"source": "view", "target": "invalid", "event_type": "x"}, "target"),
        ({"source": "view", "target": "nas", "event_type": ""}, "event_type"),
    ],
)
def test_publish_validates_envelope(kwargs: dict, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        event_queue.publish(**kwargs)
