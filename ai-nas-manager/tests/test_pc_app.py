from pc_app import FragmentStore, fragment_records, scene_boundaries, scene_records


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
