from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import media_index
import server


@pytest.fixture(autouse=True)
def isolated_media_index(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """register_media/list_pending_mediaのテストが、実際のmedia_index.jsonを
    汚さないようにする。"""
    monkeypatch.setattr(media_index, "INDEX_PATH", tmp_path / "media_index.json")


def test_search_media_finds_matching_channel() -> None:
    results = server.search_media("GyroBoy")
    assert len(results) == 1
    assert results[0]["channel"] == 1
    assert results[0]["title"] == "GyroBoy"


def test_search_media_no_match_returns_empty_list() -> None:
    assert server.search_media("存在しないキーワードxyz") == []


def test_get_fragment_details_returns_seekable_timestamps() -> None:
    details = server.get_fragment_details(2)
    assert details["channel"] == 2
    assert details["title"] == "ColorSorter"
    assert len(details["fragments"]) == 5
    assert details["fragments"][0]["start"] == 0.0
    for fragment in details["fragments"]:
        assert fragment["start"] < fragment["end"]
        assert fragment["description"]


def test_get_fragment_details_includes_thumbnail_path() -> None:
    details = server.get_fragment_details(1)
    for fragment in details["fragments"]:
        assert fragment["thumbnail_path"] is not None
        assert fragment["thumbnail_path"].endswith(".png")


def test_get_fragment_details_invalid_channel_raises() -> None:
    with pytest.raises(ValueError):
        server.get_fragment_details(99)


@pytest.fixture(scope="module")
def two_scene_video(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out_dir = tmp_path_factory.mktemp("two_scene_video")
    out_path = out_dir / "two_scene.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", "color=red:size=320x240:rate=10:duration=2",
            "-f", "lavfi", "-i", "color=blue:size=320x240:rate=10:duration=2",
            "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v]",
            "-map", "[v]",
            str(out_path),
        ],
        check=True,
    )
    return out_path


def test_analyze_video_returns_fragments_for_arbitrary_file(two_scene_video: Path) -> None:
    result = server.analyze_video(str(two_scene_video), scene_threshold=0.3)

    assert result["path"] == str(two_scene_video)
    assert result["duration"] == pytest.approx(4.0, abs=0.05)
    assert len(result["fragments"]) == 2
    for fragment in result["fragments"]:
        assert Path(fragment["frame_path"]).exists()


def test_analyze_video_missing_file_raises() -> None:
    with pytest.raises(ValueError):
        server.analyze_video("/no/such/file.mp4")


def test_register_media_generates_thumbnail_for_real_file(
    two_scene_video: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(server, "THUMBNAILS_DIR", tmp_path / "thumbnails")

    result = server.register_media(
        path=str(two_scene_video),
        title="two scene",
        tag="テスト映像",
        fragments=[
            {"start": 0.0, "end": 2.0, "description": "赤色の場面"},
            {"start": 2.0, "end": 4.0, "description": "青色の場面"},
        ],
    )

    assert len(result["fragments"]) == 2
    for i, fragment in enumerate(result["fragments"]):
        thumb = Path(fragment["thumbnail_path"])
        assert thumb.exists()
        assert thumb.name == f"two_scene_f{i}.png"

    # search_mediaの結果にもthumbnail_pathがそのまま乗る。
    results = server.search_media("テスト映像")
    assert results[0]["fragments"][0]["thumbnail_path"] == result["fragments"][0]["thumbnail_path"]


def test_register_media_missing_file_leaves_thumbnail_none() -> None:
    result = server.register_media(
        path="/no/such/video.mp4",
        title="存在しない",
        tag="tag",
        fragments=[{"start": 0.0, "end": 1.0, "description": "場面"}],
    )
    assert result["fragments"][0]["thumbnail_path"] is None


def test_register_media_then_search_media_finds_it() -> None:
    server.register_media(
        path="/videos/dog.mp4",
        title="犬の散歩",
        tag="公園で犬が散歩している様子",
        fragments=[{"start": 0.0, "end": 3.0, "description": "犬が走り出す場面"}],
    )

    results = server.search_media("散歩")

    assert len(results) == 1
    assert results[0]["origin"] == "library"
    assert results[0]["path"] == "/videos/dog.mp4"
    assert results[0]["fragments"][0]["description"] == "犬が走り出す場面"


def test_search_media_combines_channel_and_library_results() -> None:
    server.register_media("/videos/dog.mp4", "犬の散歩", "散歩の様子", [])

    # 既存のCH1(GyroBoy)とライブラリ両方にヒットしうる、共通しない語で確認する。
    channel_only = server.search_media("GyroBoy")
    library_only = server.search_media("散歩")

    assert [r["origin"] for r in channel_only] == ["channel"]
    assert [r["origin"] for r in library_only] == ["library"]


def test_list_pending_media_excludes_registered(tmp_path: Path) -> None:
    scan_dir = tmp_path / "incoming"
    scan_dir.mkdir()
    (scan_dir / "new.mp4").write_bytes(b"")
    (scan_dir / "already_done.mp4").write_bytes(b"")

    server.register_media(str(scan_dir / "already_done.mp4"), "済み", "tag", [])

    pending = server.list_pending_media(str(scan_dir))

    assert pending == [str(scan_dir / "new.mp4")]


def test_list_pending_media_excludes_media_catalog_channels(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import media_catalog

    scan_dir = tmp_path / "incoming"
    scan_dir.mkdir()
    already_tagged = scan_dir / "already_a_channel.mp4"
    already_tagged.write_bytes(b"")
    (scan_dir / "new.mp4").write_bytes(b"")

    fake_channel = media_catalog.MediaChannel(
        channel=1, title="Fake", path=already_tagged, tag="dummy", fragments=[]
    )
    monkeypatch.setattr(media_catalog, "CHANNELS", [fake_channel])

    pending = server.list_pending_media(str(scan_dir))

    assert pending == [str(scan_dir / "new.mp4")]
