from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import server


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
