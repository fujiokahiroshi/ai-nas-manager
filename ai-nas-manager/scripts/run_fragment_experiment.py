"""Run the online fragmenter over one or more files and write JSON results."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from online_fragmentation import GrayFrame, OnlineFragmentConfig, OnlineMultiSignalFragmenter


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
    fragmenter = OnlineMultiSignalFragmenter(source.stem, OnlineFragmentConfig())
    events = []
    samples: dict[str, list[float]] = {}
    frame_count = 0
    maxima = Counter()
    for frame in iter_frames(source, args.ffmpeg, args.width, args.height, args.fps):
        frame_count += 1
        produced = fragmenter.process(frame)
        for name, value in fragmenter.last_signals.as_dict().items():
            samples.setdefault(name, []).append(value)
        for event in produced:
            events.append(event.as_dict())
            maxima[event.reason] += 1
    for event in fragmenter.finish():
        events.append(event.as_dict())
        maxima[event.reason] += 1
    opens = [event for event in events if event["kind"] == "open"]
    updates = [event for event in events if event["kind"] == "update"]
    closes = [event for event in events if event["kind"] == "close"]
    def percentiles(values: list[float]) -> dict[str, float]:
        ordered = sorted(values)
        if not ordered:
            return {"p50": 0.0, "p90": 0.0, "p95": 0.0, "max": 0.0}
        at = lambda fraction: ordered[min(len(ordered) - 1, round((len(ordered) - 1) * fraction))]
        return {"p50": at(0.50), "p90": at(0.90), "p95": at(0.95), "max": ordered[-1]}
    return {
        "source": str(source.resolve()),
        "analysis_size": [args.width, args.height],
        "sample_fps": args.fps,
        "frames": frame_count,
        "fragments": len(opens),
        "updates": len(updates),
        "closes": len(closes),
        "reasons": dict(maxima),
        "signal_distribution": {name: percentiles(values) for name, values in samples.items()},
        "events": events,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("sources", nargs="+", type=Path)
    parser.add_argument("--ffmpeg", default="ffmpeg")
    parser.add_argument("--width", type=int, default=320)
    parser.add_argument("--height", type=int, default=180)
    parser.add_argument("--fps", type=float, default=2.0)
    parser.add_argument("--output", type=Path, default=Path("fragment-experiment.json"))
    args = parser.parse_args()
    results = [analyze(source, args) for source in args.sources]
    payload = {"algorithm": "online-multisignal-v1", "results": results}
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
