from pc_app import FragmentStore, fragment_records


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
