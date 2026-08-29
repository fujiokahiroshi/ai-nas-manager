from __future__ import annotations

from pathlib import Path

import pytest

import media_index


@pytest.fixture(autouse=True)
def isolated_index(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """各テストごとに、実際のmedia_index.jsonを触らない隔離されたインデックスを使う。"""
    index_path = tmp_path / "media_index.json"
    monkeypatch.setattr(media_index, "INDEX_PATH", index_path)
    return index_path


def test_list_registered_empty_when_no_index_file() -> None:
    assert media_index.list_registered() == []


def test_register_then_list_registered() -> None:
    entry = media_index.register(
        path="/videos/a.mp4",
        title="A",
        tag="テストタグ",
        fragments=[{"start": 0.0, "end": 5.0, "description": "最初の場面"}],
    )

    assert entry.path == "/videos/a.mp4"
    registered = media_index.list_registered()
    assert len(registered) == 1
    assert registered[0].title == "A"
    assert registered[0].fragments[0].description == "最初の場面"


def test_register_overwrites_existing_entry_with_same_path() -> None:
    media_index.register("/videos/a.mp4", "A", "old tag", [])
    media_index.register("/videos/a.mp4", "A updated", "new tag", [])

    registered = media_index.list_registered()
    assert len(registered) == 1
    assert registered[0].title == "A updated"
    assert registered[0].tag == "new tag"


def test_search_matches_title_tag_and_fragment_description() -> None:
    media_index.register("/videos/a.mp4", "犬の映像", "散歩の様子", [])
    media_index.register("/videos/b.mp4", "猫の映像", "昼寝の様子", [{"start": 0.0, "end": 1.0, "description": "あくびをしている"}])

    assert [e.path for e in media_index.search("犬")] == ["/videos/a.mp4"]
    assert [e.path for e in media_index.search("あくび")] == ["/videos/b.mp4"]
    assert media_index.search("存在しないキーワードxyz") == []


def test_list_pending_excludes_registered_files(tmp_path: Path) -> None:
    scan_dir = tmp_path / "scan"
    scan_dir.mkdir()
    (scan_dir / "a.mp4").write_bytes(b"")
    (scan_dir / "b.webm").write_bytes(b"")
    (scan_dir / "notes.txt").write_bytes(b"")

    media_index.register(str(scan_dir / "a.mp4"), "A", "tag", [])

    pending = media_index.list_pending(str(scan_dir))

    assert pending == [str(scan_dir / "b.webm")]


def test_list_pending_invalid_dir_raises() -> None:
    with pytest.raises(ValueError):
        media_index.list_pending("/no/such/directory")
