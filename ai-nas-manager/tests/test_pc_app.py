from pathlib import Path

import pytest

from pc_app import (
    AppState,
    DEFAULT_UI,
    FragmentStore,
    fragment_records,
    imported_video_path,
    media_kind_for_path,
    scene_boundaries,
    scene_boundary_markers,
    scene_records,
)


def sample_payload() -> dict:
    return {
        "source": "sample.mp4",
        "inferences": [{
            "fragment_id": "scene-1",
            "revision": 2,
            "source_timestamp_ms": 1500,
            "observation_ja": "歩道を人が歩いている",
            "action_ja": "歩行",
            "change_from_previous_ja": "人物が増えた",
            "objects": ["人物", "自転車"],
            "confidence": 0.9,
            "completed_during_stream": True,
        }],
    }


def test_fragment_records_preserve_video_text_link() -> None:
    records = fragment_records(sample_payload())
    assert records[0]["id"] == "scene-1:r2"
    assert records[0]["timestamp_ms"] == 1500
    assert records[0]["objects"] == ["人物", "自転車"]


def test_store_search_and_user_edits_persist(tmp_path) -> None:
    database = tmp_path / "pc.sqlite3"
    store = FragmentStore(database)
    store.import_records(fragment_records(sample_payload()))
    assert len(store.list("自転車")) == 1

    updated = store.update("scene-1:r2", user_text="修正した説明", favorite=True)
    assert updated is not None
    assert updated["user_text"] == "修正した説明"
    assert updated["favorite"] is True

    reopened = FragmentStore(database)
    assert reopened.list("修正した")[0]["favorite"] is True


def test_reimport_does_not_overwrite_user_text(tmp_path) -> None:
    store = FragmentStore(tmp_path / "pc.sqlite3")
    records = fragment_records(sample_payload())
    store.import_records(records)
    store.update("scene-1:r2", user_text="人が横切る", favorite=True)
    store.import_records(records)
    assert store.list()[0]["user_text"] == "人が横切る"


def test_store_can_filter_fragments_by_source(tmp_path) -> None:
    store = FragmentStore(tmp_path / "pc.sqlite3")
    first = fragment_records(sample_payload())[0]
    second = dict(first, id="other:r1", fragment_id="other", source_path="other.mp4")
    store.import_records([first, second])
    assert [item["id"] for item in store.list(source_path="sample.mp4")] == [first["id"]]


def test_scene_records_group_fragments_at_pelt_boundaries() -> None:
    fragments = fragment_records(sample_payload())
    second = dict(fragments[0], id="fragment-2:r1", fragment_id="fragment-2", timestamp_ms=8_000)
    scenes = scene_records(fragments + [second], [5_000], "PELT final")
    assert len(scenes) == 2
    assert scenes[0]["start_ms"] == 1_500
    assert scenes[1]["start_ms"] == 8_000
    assert scenes[1]["boundary_method"] == "PELT final"


def test_scene_records_fallback_keeps_nearby_revisions_together() -> None:
    fragments = fragment_records(sample_payload())
    later = dict(fragments[0], id="fragment-2:r1", fragment_id="fragment-2", timestamp_ms=10_000)
    assert len(scene_records(fragments + [later], [], "time-gap fallback")) == 1


def test_scene_boundaries_prefers_pelt() -> None:
    payload = {"scene_segmentation": {
        "pelt_boundaries_ms": [9_000],
        "online_boundaries": [{"boundary_ms": 8_000}],
    }}
    assert scene_boundaries(payload) == ([9_000], "PELT final")


def test_scene_boundaries_prefers_manual_review() -> None:
    payload = {"scene_segmentation": {
        "manual_boundaries_ms": [4_000, 8_000],
        "pelt_boundaries_ms": [9_000],
    }}
    assert scene_boundaries(payload) == ([4_000, 8_000], "Manual review")


def test_scene_boundary_markers_keeps_comparison_series_separate() -> None:
    payload = {
        "scene_segmentation": {
            "online_boundaries": [{"boundary_ms": 1_000}],
            "shadow_algorithms": {
                "adaptive_memory_v1": {"boundaries": [{"boundary_ms": 1_500}]},
                "adaptive_memory_tuned_v1": {"boundaries": [{"boundary_ms": 2_000}]},
            },
        },
        "scene_ground_truth": {"boundaries_ms": [2_100]},
    }
    assert scene_boundary_markers(payload) == {
        "current": [1_000],
        "adaptive": [1_500],
        "tuned": [2_000],
        "ground_truth": [2_100],
    }


def test_scene_summary_replaces_latest_fragment_text() -> None:
    fragments = fragment_records(sample_payload())
    scenes = scene_records(fragments, [], "time-gap fallback", summaries=[{
        "scene_id": "scene-0001",
        "start_ms": 1_500,
        "summary_ja": "人物が歩道を移動するScene",
        "objects": ["歩道"],
        "activities": ["歩行"],
        "important_change_ja": "人物が増えた",
        "method": "gemma_multi_fragment",
    }])
    assert scenes[0]["summary"] == "人物が歩道を移動するScene"
    assert scenes[0]["summary_method"] == "gemma_multi_fragment"
    assert "歩道" in scenes[0]["objects"]


def test_imported_video_path_is_unique_and_stays_in_directory(tmp_path) -> None:
    first = imported_video_path(tmp_path, "../家族 動画.mp4")
    second = imported_video_path(tmp_path, "../家族 動画.mp4")
    assert first.parent == tmp_path
    assert first.suffix == ".mp4"
    assert first != second
    with pytest.raises(ValueError):
        imported_video_path(tmp_path, "notes.txt")


def test_media_kind_for_video_and_image() -> None:
    assert media_kind_for_path(Path("clip.mp4")) == "video"
    assert media_kind_for_path(Path("photo.PNG")) == "image"
    with pytest.raises(ValueError):
        media_kind_for_path(Path("notes.txt"))


def test_fragment_list_auto_scrolls_when_live_count_increases() -> None:
    html = DEFAULT_UI.read_text(encoding="utf-8")
    assert "fragmentAdded" in html
    assert "scrollToLatest" in html
    assert "cards.scrollTo({top:cards.scrollHeight" in html


def test_image_source_uses_static_analysis_only(tmp_path) -> None:
    source = tmp_path / "photo.jpg"
    source.write_bytes(b"image")
    state = AppState(
        FragmentStore(tmp_path / "pc.sqlite3"),
        source,
        b"ui",
        {},
        [],
        "not analyzed",
        [],
        [],
        {},
    )
    state.select_source(source)
    assert state.media_kind == "image"
    with pytest.raises(ValueError, match="静的解析"):
        state.begin_analysis("realtime")
    assert state.begin_analysis("static") == source.resolve()


def test_select_source_clears_previous_analysis(tmp_path) -> None:
    source = tmp_path / "new.mp4"
    source.write_bytes(b"video")
    store = FragmentStore(tmp_path / "pc.sqlite3")
    payload = sample_payload()
    payload["source"] = str(source.resolve())
    store.import_records(fragment_records(payload))
    state = AppState(
        store,
        Path("old.mp4"),
        b"ui",
        {"old": b"image"},
        [1_000],
        "PELT final",
        [{"summary_ja": "old"}],
        [{"boundary_ms": 1_000}],
        {"current": [1_000]},
        tmp_path / "imports",
    )
    state.select_source(source)
    assert state.source == source.resolve()
    assert state.thumbnails == {}
    assert state.boundaries_ms == []
    assert state.scene_summaries == []
    assert state.boundary_markers["current"] == []
    assert state.analysis_snapshot()["state"] == "not_analyzed"
    assert store.list(source_path=str(source.resolve())) == []


def test_begin_analysis_sets_mode_and_rejects_parallel_run(tmp_path) -> None:
    source = tmp_path / "new.mp4"
    source.write_bytes(b"video")
    state = AppState(
        FragmentStore(tmp_path / "pc.sqlite3"),
        source,
        b"ui",
        {},
        [],
        "not analyzed",
        [],
        [],
        {},
    )
    assert state.begin_analysis("static") == source
    status = state.analysis_snapshot()
    assert status["state"] == "running"
    assert status["mode"] == "static"
    assert status["phase"] == "fragment"
    assert state.begin_analysis("realtime") is None
    with pytest.raises(ValueError):
        state.analysis_state = "not_analyzed"
        state.begin_analysis("unknown")
