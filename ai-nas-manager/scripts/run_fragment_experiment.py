"""Run the online fragmenter over one or more files and write JSON results."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from audio_detection import AudioChangeConfig, AudioFeatureTimeline, iter_audio_features
from online_fragmentation import GrayFrame, OnlineMultiSignalFragmenter, fragment_config
from scene_segmentation import (
    AdaptiveMemoryShadowDetector,
    OnlineHybridSceneSegmenter,
    SceneSample,
    pelt_boundaries,
)


def luma_state_vector(frame: GrayFrame, bins: int = 16) -> tuple[float, ...]:
    """Return an absolute (not frame-difference) state for offline PELT."""

    histogram = [0] * bins
    bin_width = 256 // bins
    for value in frame.pixels[::4]:
        histogram[min(value // bin_width, bins - 1)] += 1
    total = max(1, sum(histogram))
    return tuple(value / total for value in histogram)


def iter_frames(source: Path, ffmpeg: str, width: int, height: int, fps: float):
    command = [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-i", str(source),
        "-an", "-sn", "-vf",
        f"fps={fps},scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,format=gray",
        "-pix_fmt", "gray", "-f", "rawvideo", "pipe:1",
    ]
    process = subprocess.Popen(command, stdout=subprocess.PIPE)
    assert process.stdout is not None
    frame_bytes = width * height
    index = 0
    try:
        while True:
            data = process.stdout.read(frame_bytes)
            if not data:
                break
            if len(data) != frame_bytes:
                raise RuntimeError(f"truncated frame from {source}")
            yield GrayFrame(round(index * 1000 / fps), width, height, data)
            index += 1
    finally:
        process.stdout.close()
        return_code = process.wait()
        if return_code:
            raise RuntimeError(f"ffmpeg exited with {return_code}: {source}")


def analyze(source: Path, args: argparse.Namespace) -> dict[str, object]:
    started = time.perf_counter()
    fragmenter = OnlineMultiSignalFragmenter(
        source.stem,
        fragment_config(args.profile),
    )
    scene_segmenter = OnlineHybridSceneSegmenter(source.stem)
    adaptive_shadow = AdaptiveMemoryShadowDetector()
    audio_features = (
        list(iter_audio_features(
            source,
            args.ffmpeg,
            AudioChangeConfig(window_ms=args.audio_window_ms),
        ))
        if args.audio else []
    )
    audio_timeline = AudioFeatureTimeline(audio_features)
    events = []
    samples: dict[str, list[float]] = {}
    frame_count = 0
    maxima = Counter()
    scene_events = []
    scene_timestamps_ms: list[int] = []
    scene_state_vectors: list[tuple[float, ...]] = []
    adaptive_events: list[dict[str, object]] = []
    adaptive_trace: list[dict[str, object]] = []
    for frame in iter_frames(source, args.ffmpeg, args.width, args.height, args.fps):
        frame_count += 1
        audio = audio_timeline.at(frame.timestamp_ms)
        produced = fragmenter.process(
            frame,
            audio_change=audio.change_score if audio is not None else 0.0,
            audio_label=audio.label if audio is not None else "",
        )
        signals = fragmenter.last_signals
        visual_change = max(
            signals.luma_difference,
            signals.histogram_difference,
            signals.edge_difference,
            signals.changed_pixel_ratio,
            signals.adaptive_novelty,
        )
        scene_events.extend(event.as_dict() for event in scene_segmenter.process(SceneSample(
            frame.timestamp_ms,
            visual_change=visual_change,
            audio_change=audio.change_score if audio is not None else 0.0,
        )))
        state_vector = luma_state_vector(frame)
        scene_timestamps_ms.append(frame.timestamp_ms)
        scene_state_vectors.append(state_vector)
        adaptive_events.extend(
            event.as_dict() for event in adaptive_shadow.process(frame.timestamp_ms, state_vector)
        )
        if adaptive_shadow.last_snapshot is not None:
            adaptive_trace.append(adaptive_shadow.last_snapshot.as_dict())
        for name, value in fragmenter.last_signals.as_dict().items():
            samples.setdefault(name, []).append(value)
        for event in produced:
            events.append(event.as_dict())
            maxima[event.reason] += 1
    for event in fragmenter.finish():
        events.append(event.as_dict())
        maxima[event.reason] += 1
    scene_events.extend(event.as_dict() for event in scene_segmenter.finish())
    pelt_indices = pelt_boundaries(
        scene_state_vectors,
        penalty=args.pelt_penalty,
        min_size=max(2, round(args.fps * args.pelt_min_scene_seconds)),
    )
    opens = [event for event in events if event["kind"] == "open"]
    updates = [event for event in events if event["kind"] == "update"]
    closes = [event for event in events if event["kind"] == "close"]
    def percentiles(values: list[float]) -> dict[str, float]:
        ordered = sorted(values)
        if not ordered:
            return {"p50": 0.0, "p90": 0.0, "p95": 0.0, "max": 0.0}
        at = lambda fraction: ordered[min(len(ordered) - 1, round((len(ordered) - 1) * fraction))]
        return {"p50": at(0.50), "p90": at(0.90), "p95": at(0.95), "max": ordered[-1]}
    duration_ms = max((int(event["observed_ms"]) for event in events), default=0)
    processing_seconds = time.perf_counter() - started
    return {
        "source": str(source.resolve()),
        "analysis_size": [args.width, args.height],
        "sample_fps": args.fps,
        "frames": frame_count,
        "duration_ms": duration_ms,
        "processing_seconds": round(processing_seconds, 6),
        "realtime_factor": round(processing_seconds / (duration_ms / 1000), 6) if duration_ms else None,
        "audio_windows": len(audio_features),
        "audio_events": [
            feature.as_dict() for feature in audio_features
            if feature.change_score >= 0.5 or feature.onset
        ],
        "fragments": len(opens),
        "updates": len(updates),
        "closes": len(closes),
        "reasons": dict(maxima),
        "signal_distribution": {name: percentiles(values) for name, values in samples.items()},
        "scene_segmentation": {
            "online_algorithm": "cusum-bocpd-semantic-veto-v1",
            "online_boundaries": scene_events,
            "offline_algorithm": "pelt-multivariate-luma-histogram-v1",
            "pelt_penalty": args.pelt_penalty,
            "pelt_boundaries_ms": [scene_timestamps_ms[index] for index in pelt_indices],
            "shadow_algorithms": {
                "adaptive_memory_v1": {
                    "authoritative": False,
                    "boundaries": adaptive_events,
                    "trace": adaptive_trace,
                },
            },
        },
        "events": events,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("sources", nargs="+", type=Path)
    parser.add_argument("--ffmpeg", default="ffmpeg")
    parser.add_argument("--width", type=int, default=320)
    parser.add_argument("--height", type=int, default=180)
    parser.add_argument("--fps", type=float, default=2.0)
    parser.add_argument("--audio", action="store_true")
    parser.add_argument("--audio-window-ms", type=int, default=500)
    parser.add_argument("--pelt-penalty", type=float, default=0.35)
    parser.add_argument("--pelt-min-scene-seconds", type=float, default=3.0)
    parser.add_argument(
        "--profile",
        choices=("legacy-v1", "fused-v2", "fused-v2-conservative", "fused-v2-balanced"),
        default="fused-v2-balanced",
    )
    parser.add_argument("--output", type=Path, default=Path("fragment-experiment.json"))
    args = parser.parse_args()
    results = [analyze(source, args) for source in args.sources]
    payload = {"algorithm": f"online-multisignal-{args.profile}", "results": results}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    for result in results:
        print(
            f"{Path(result['source']).name}: frames={result['frames']} "
            f"fragments={result['fragments']} updates={result['updates']}"
        )
    print(f"result={args.output.resolve()}")


if __name__ == "__main__":
    main()
