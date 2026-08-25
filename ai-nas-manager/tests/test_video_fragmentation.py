from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from video_fragmentation import detect_scene_boundaries, fragment_video, get_duration


@pytest.fixture(scope="module")
def two_scene_video(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """2秒の赤 + 2秒の青を、t=2.0秒でハードカットした4秒のテスト映像。

    各半分は単色の静止画相当(内部変化ゼロ)なので、シーン変化検出器が
    カット点だけを検出し、それ以外で誤検出しないかを確実に検証できる。
    """
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


def test_get_duration(two_scene_video: Path) -> None:
    assert get_duration(two_scene_video) == pytest.approx(4.0, abs=0.05)


def test_detect_scene_boundaries_finds_the_hard_cut_only(two_scene_video: Path) -> None:
    boundaries = detect_scene_boundaries(two_scene_video, threshold=0.3)

    assert len(boundaries) == 1
    assert boundaries[0] == pytest.approx(2.0, abs=0.15)


def test_fragment_video_produces_two_fragments_with_frames(
    two_scene_video: Path, tmp_path: Path
) -> None:
    fragments = fragment_video(two_scene_video, tmp_path, scene_threshold=0.3)

    assert len(fragments) == 2
    assert fragments[0].start == 0.0
    assert fragments[0].end == pytest.approx(2.0, abs=0.15)
    assert fragments[1].start == pytest.approx(2.0, abs=0.15)
    assert fragments[1].end == pytest.approx(4.0, abs=0.05)
    for fragment in fragments:
        assert fragment.frame_path.exists()
        assert fragment.frame_path.stat().st_size > 0
