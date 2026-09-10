from __future__ import annotations

from online_fragmentation import (
    EventKind,
    GrayFrame,
    OnlineFragmentConfig,
    OnlineMultiSignalFragmenter,
    visual_signals,
)


def frame(timestamp_ms: int, value: int, width: int = 16, height: int = 8) -> GrayFrame:
    return GrayFrame(timestamp_ms, width, height, bytes([value]) * (width * height))


def test_visual_signals_detect_hard_change() -> None:
    luma, histogram, edge, changed = visual_signals(frame(0, 0), frame(500, 255), stride=1)
    assert luma == 1.0
    assert histogram == 1.0
    assert edge == 0.0
    assert changed == 1.0


def test_stream_emits_open_update_and_close_before_finish() -> None:
    config = OnlineFragmentConfig(
        open_threshold=0.20,
        update_threshold=0.12,
        quiet_threshold=0.05,
        hard_cut_threshold=1.0,
        quiet_ms=1_000,
        min_update_ms=500,
        min_fragment_ms=500,
    )
    fragmenter = OnlineMultiSignalFragmenter("camera", config)
    events = []
    events += fragmenter.process(frame(0, 0))
    events += fragmenter.process(frame(500, 120))
    events += fragmenter.process(frame(1_000, 240))
    events += fragmenter.process(frame(1_500, 240))
    events += fragmenter.process(frame(2_000, 240))
    assert [event.kind for event in events] == [
        EventKind.OPEN,
        EventKind.UPDATE,
        EventKind.UPDATE,
        EventKind.CLOSE,
    ]
    assert events[-1].observed_ms == 2_000


def test_subtitle_can_open_fragment_without_visual_change() -> None:
    fragmenter = OnlineMultiSignalFragmenter(
        "tv", OnlineFragmentConfig(open_on_start=False)
    )
    assert fragmenter.process(frame(0, 30)) == []
    events = fragmenter.process(frame(500, 30), subtitle="ニュースです")
    assert len(events) == 1
    assert events[0].kind is EventKind.OPEN
    assert events[0].reason == "subtitle"


def test_representatives_are_bounded() -> None:
    config = OnlineFragmentConfig(
        open_threshold=0.10,
        update_threshold=0.05,
        quiet_threshold=0.01,
        hard_cut_threshold=1.0,
        representative_limit=2,
        min_update_ms=100,
        min_fragment_ms=100,
    )
    fragmenter = OnlineMultiSignalFragmenter("camera", config)
    fragmenter.process(frame(0, 0))
    fragmenter.process(frame(100, 50))
    fragmenter.process(frame(200, 100))
    fragmenter.process(frame(300, 150))
    events = fragmenter.finish()
    assert len(events[0].representative_times_ms) == 2


def test_object_change_can_open_fragment() -> None:
    fragmenter = OnlineMultiSignalFragmenter(
        "camera", OnlineFragmentConfig(open_on_start=False)
    )
    assert fragmenter.process(frame(0, 20)) == []
    events = fragmenter.process(frame(500, 20), object_change=1.0)
    assert len(events) == 1
    assert events[0].kind is EventKind.OPEN
    assert events[0].signals.object_change == 1.0


def test_audio_change_can_open_fragment() -> None:
    fragmenter = OnlineMultiSignalFragmenter(
        "audio", OnlineFragmentConfig(open_on_start=False)
    )
    assert fragmenter.process(frame(0, 20)) == []
    events = fragmenter.process(frame(500, 20), audio_change=0.9)
    assert len(events) == 1
    assert events[0].kind is EventKind.OPEN
    assert events[0].reason == "audio_change"
    assert events[0].signals.audio_change == 0.9


def test_discontinuity_closes_and_next_frame_reopens() -> None:
    fragmenter = OnlineMultiSignalFragmenter("camera")
    opened = fragmenter.process(frame(0, 20))
    closed = fragmenter.discontinuity(750)
    reopened = fragmenter.process(frame(1_000, 20))
    assert opened[0].kind is EventKind.OPEN
    assert closed[0].kind is EventKind.CLOSE
    assert closed[0].reason == "source_disconnected"
    assert closed[0].observed_ms == 750
    assert reopened[0].kind is EventKind.OPEN
    assert reopened[0].fragment_id != opened[0].fragment_id
