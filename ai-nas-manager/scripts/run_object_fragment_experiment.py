"""Evaluate object-state-assisted online fragmentation on a real video."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from object_detection import YoloXOnnxDetector, object_change_score
from online_fragmentation import GrayFrame, OnlineMultiSignalFragmenter


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("--model", type=Path, default=Path("models/yolox_tiny.onnx"))
    parser.add_argument("--fps", type=float, default=2.0)
    parser.add_argument("--output", type=Path, default=Path("docs/object-fragment-experiment.json"))
    parser.add_argument("--score-threshold", type=float, default=0.25)
    args = parser.parse_args()

    import cv2

    detector = YoloXOnnxDetector(
        args.model,
        score_threshold=args.score_threshold,
        class_ids={0, 1, 2, 3, 5, 7},
    )
    capture = cv2.VideoCapture(str(args.source))
    source_fps = capture.get(cv2.CAP_PROP_FPS)
    if not capture.isOpened() or source_fps <= 0:
        raise RuntimeError(f"cannot open video: {args.source}")
    every = max(1, round(source_fps / args.fps))
    fragmenter = OnlineMultiSignalFragmenter(args.source.stem)
    previous_detections = []
    frames, events = [], []
    decoded_index = 0
    started = time.perf_counter()
    inference_seconds = 0.0
    while True:
        ok, image = capture.read()
        if not ok:
            break
        if decoded_index % every:
            decoded_index += 1
            continue
        timestamp_ms = round(decoded_index * 1000 / source_fps)
        inference_started = time.perf_counter()
        detections = detector.detect(image)
        inference_seconds += time.perf_counter() - inference_started
        change = object_change_score(previous_detections, detections)
        previous_detections = detections
        gray = cv2.resize(cv2.cvtColor(image, cv2.COLOR_BGR2GRAY), (320, 180))
        frame = GrayFrame(timestamp_ms, 320, 180, gray.tobytes())
        produced = fragmenter.process(frame, object_change=change)
        frames.append({
            "timestamp_ms": timestamp_ms,
            "object_change": round(change, 6),
            "objects": [item.as_dict() for item in detections],
        })
        events.extend(event.as_dict() for event in produced)
        decoded_index += 1
    capture.release()
    events.extend(event.as_dict() for event in fragmenter.finish())
    elapsed = time.perf_counter() - started
    payload = {
        "source": str(args.source.resolve()),
        "model": str(args.model.resolve()),
        "sample_fps": args.fps,
        "sampled_frames": len(frames),
        "elapsed_seconds": round(elapsed, 3),
        "detector_mean_ms": round(1000 * inference_seconds / max(1, len(frames)), 3),
        "frames": frames,
        "events": events,
    }
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: payload[key] for key in (
        "sampled_frames", "elapsed_seconds", "detector_mean_ms"
    )}, ensure_ascii=False))
    print(f"events={len(events)} result={args.output.resolve()}")


if __name__ == "__main__":
    main()
