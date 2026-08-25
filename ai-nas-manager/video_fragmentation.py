"""映像を「意味のある少数のfragment」に分割する、段階1(安価な境界検出)の実装。

設計・検証結果: ai-nas-manager/docs/semantic-tagging-experiment.md 6節。

フレームを1枚1枚解析するのは非現実的、という前提のもと、ffmpegの組み込み
シーン変化検出(意味理解を伴わない、安価な信号)だけで候補境界を直接抽出する。
段階2(fragmentの言語化)・段階3(統合)はこのモジュールの範囲外で、
現時点ではClaudeとの対話で行う想定(将来的にAPI呼び出しで自動化する場合は
別ブランチで対応)。
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class VideoFragmentCandidate:
    """段階1の出力: 境界検出だけから得られる、意味づけ前のfragment候補。"""

    index: int
    start: float
    end: float
    frame_path: Path


def get_duration(video_path: Path) -> float:
    """ffprobeで動画の長さ(秒)を取得する。"""
    result = subprocess.run(
        [
            "ffprobe",
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(video_path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return float(result.stdout.strip())


def detect_scene_boundaries(video_path: Path, threshold: float = 0.3) -> list[float]:
    """ffmpegのシーン変化検出フィルタで、候補境界の時刻(秒)一覧を返す。

    全フレームを解析するのではなく、ffmpeg内部の隣接フレーム差分スコアが
    threshold(0〜1)を超えた瞬間だけを検出する。意味理解は一切行わない。
    """
    result = subprocess.run(
        [
            "ffmpeg",
            "-i", str(video_path),
            "-filter:v", f"select='gt(scene,{threshold})',showinfo",
            "-f", "null",
            "-",
        ],
        capture_output=True,
        text=True,
    )
    timestamps: list[float] = []
    for line in result.stderr.splitlines():
        if "pts_time:" not in line:
            continue
        token = line.split("pts_time:", 1)[1].split()[0]
        timestamps.append(float(token))
    return timestamps


def extract_frame(video_path: Path, at_seconds: float, out_path: Path) -> Path:
    """指定時刻の1フレームをPNGとして書き出す。"""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel", "error",
            "-ss", str(at_seconds),
            "-i", str(video_path),
            "-frames:v", "1",
            str(out_path),
        ],
        check=True,
    )
    return out_path


def fragment_video(
    video_path: Path,
    out_dir: Path,
    scene_threshold: float = 0.3,
) -> list[VideoFragmentCandidate]:
    """段階1の一連の処理: 境界検出 → 各fragmentの代表フレーム抽出。

    候補境界 c1 < c2 < ... < cN から、[0, c1), [c1, c2), ..., [cN, duration)
    のN+1個のfragment候補を作る。各fragmentの代表フレームは開始時刻のもの
    (境界検出はまさにその時刻で内容が変わったことを示しているため)。
    候補境界が1つもない場合は、動画全体を1つのfragmentとして扱う。
    """
    duration = get_duration(video_path)
    boundaries = detect_scene_boundaries(video_path, threshold=scene_threshold)
    starts = [0.0, *boundaries]
    ends = [*boundaries, duration]

    fragments: list[VideoFragmentCandidate] = []
    for i, (start, end) in enumerate(zip(starts, ends)):
        frame_path = extract_frame(video_path, start, out_dir / f"fragment_{i:03d}.png")
        fragments.append(VideoFragmentCandidate(index=i, start=start, end=end, frame_path=frame_path))
    return fragments
