from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class VideoFragment:
    start_at: float
    end_at: float
    scene_summary: str


@dataclass
class AudioFragment:
    start_at: float
    end_at: float
    transcript: str


@dataclass
class SemanticFragment:
    start_at: float
    end_at: float
    subject: str
    tags: list[str] = field(default_factory=list)
    summary: str = ""
    narrative_role: str = "activity"


@dataclass
class FragmentAlignment:
    video: VideoFragment
    audio: AudioFragment
    semantic: SemanticFragment
    confidence: float = 0.0


def align_fragments(
    video_fragments: list[VideoFragment],
    audio_fragments: list[AudioFragment],
    semantic_fragments: list[SemanticFragment],
) -> list[FragmentAlignment]:
    """時間帯の重なりをもとに、映像・音声・意味断片の対応を作る。

    ここでは最小実装として、重なる時間範囲の中心を使って対応を結ぶ。
    """
    alignments: list[FragmentAlignment] = []
    for video in video_fragments:
        for audio in audio_fragments:
            overlap_start = max(video.start_at, audio.start_at)
            overlap_end = min(video.end_at, audio.end_at)
            if overlap_end <= overlap_start:
                continue
            for semantic in semantic_fragments:
                semantic_overlap_start = max(semantic.start_at, overlap_start)
                semantic_overlap_end = min(semantic.end_at, overlap_end)
                if semantic_overlap_end <= semantic_overlap_start:
                    continue
                score = (semantic_overlap_end - semantic_overlap_start) / max(
                    (video.end_at - video.start_at), 1e-6
                )
                alignments.append(
                    FragmentAlignment(
                        video=video,
                        audio=audio,
                        semantic=semantic,
                        confidence=round(score, 3),
                    )
                )
    return alignments


def build_demo_alignment() -> list[FragmentAlignment]:
    video = [
        VideoFragment(0.0, 5.0, "3人の子供が野球をしている"),
        VideoFragment(5.0, 10.0, "ボールが飛び、笑顔が広がる"),
    ]
    audio = [
        AudioFragment(0.0, 5.0, "今日は野球をしているよ。"),
        AudioFragment(5.0, 10.0, "ボールが飛んで、みんな笑っている。"),
    ]
    semantics = [
        SemanticFragment(0.0, 5.0, "子供", ["野球", "遊び"], "子供たちが野球で遊んでいる", "opening"),
        SemanticFragment(5.0, 10.0, "子供", ["笑顔", "楽しさ"], "ボールの飛ぶ瞬間に喜びが広がる", "activity"),
    ]
    return align_fragments(video, audio, semantics)
