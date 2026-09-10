"""Real-time object-assisted fragment-to-Gemma experiment."""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from live_semantics import FragmentEvidence, LatestEvidenceQueue, LMStudioVisionClient
from object_detection import YoloXOnnxDetector, object_change_score
from online_fragmentation import GrayFrame, EventKind, OnlineFragmentConfig, OnlineMultiSignalFragmenter


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("--detector", type=Path, default=Path("models/yolox_tiny.onnx"))
    parser.add_argument("--fps", type=float, default=2.0)
    parser.add_argument("--model", default="gemma4-12b-qat")
    parser.add_argument("--lm-studio-url", default="http://127.0.0.1:1234")
    parser.add_argument("--output", type=Path, default=Path("docs/live-gemma-experiment.json"))
    args = parser.parse_args()

    import cv2

    detector = YoloXOnnxDetector(args.detector, class_ids={0, 1, 2, 3, 5, 7})
    fragmenter = OnlineMultiSignalFragmenter(
        args.source.stem,
        OnlineFragmentConfig(min_update_ms=1_500),
    )
    queue = LatestEvidenceQueue(max_fragments=4)
    client = LMStudioVisionClient(base_url=args.lm_studio_url, model=args.model)
    fragment_events: list[dict[str, object]] = []
    inferences: list[dict[str, object]] = []
    failures: list[dict[str, object]] = []
    previous_scene = ""
    start_wall = time.perf_counter()
    stream_ended_wall: float | None = None

    def worker() -> None:
        nonlocal previous_scene
        while True:
            evidence = queue.get()
            if evidence is None:
                return
            started = time.perf_counter()
            try:
                result = client.analyze(evidence, previous_scene)
                completed = time.perf_counter()
                previous_scene = str(result.get("observation_ja", previous_scene))
                record = {
                    **result,
                    "latency_ms": round((completed - started) * 1000, 3),
                    "completed_wall_ms": round((completed - start_wall) * 1000),
                }
                inferences.append(record)
                print(
                    f"TEXT t={record['completed_wall_ms']/1000:.2f}s "
                    f"source={evidence.source_timestamp_ms/1000:.2f}s "
                    f"r{evidence.revision}: {result.get('observation_ja', '')}",
                    flush=True,
                )
            except Exception as exc:  # keep the live stream running
                failures.append({
                    "fragment_id": evidence.fragment_id,
                    "revision": evidence.revision,
                    "error": f"{type(exc).__name__}: {exc}",
                })

    worker_thread = threading.Thread(target=worker, name="gemma-worker", daemon=True)
    worker_thread.start()
    capture = cv2.VideoCapture(str(args.source))
    source_fps = capture.get(cv2.CAP_PROP_FPS)
    if not capture.isOpened() or source_fps <= 0:
        raise RuntimeError(f"cannot open video: {args.source}")
    every = max(1, round(source_fps / args.fps))
    decoded_index = 0
    previous_detections = []
    sampled_frames = 0
    while True:
        ok, image = capture.read()
        if not ok:
            break
        if decoded_index % every:
            decoded_index += 1
            continue
        timestamp_ms = round(decoded_index * 1000 / source_fps)
        delay = start_wall + timestamp_ms / 1000 - time.perf_counter()
        if delay > 0:
            time.sleep(delay)
        detections = detector.detect(image)
        object_change = object_change_score(previous_detections, detections)
        previous_detections = detections
        gray = cv2.resize(cv2.cvtColor(image, cv2.COLOR_BGR2GRAY), (320, 180))
        events = fragmenter.process(
            GrayFrame(timestamp_ms, 320, 180, gray.tobytes()),
            object_change=object_change,
        )
        for event in events:
            event_record = event.as_dict()
            event_record["detected_objects"] = [item.as_dict() for item in detections]
            fragment_events.append(event_record)
            if event.kind in {EventKind.OPEN, EventKind.UPDATE}:
                evidence_image = cv2.resize(image, (640, 360))
                encoded_ok, encoded = cv2.imencode(".jpg", evidence_image, [cv2.IMWRITE_JPEG_QUALITY, 82])
                if not encoded_ok:
                    raise RuntimeError("JPEG encoding failed")
                queue.put(FragmentEvidence(
                    event.fragment_id,
                    event.revision,
                    timestamp_ms,
                    encoded.tobytes(),
                    {
                        "event": event_record,
                        "detector_group_counts": {
                            group: sum(item.trigger_group == group for item in detections)
                            for group in sorted({item.trigger_group for item in detections})
                        },
                    },
                ))
        sampled_frames += 1
        decoded_index += 1
    capture.release()
    fragment_events.extend(event.as_dict() for event in fragmenter.finish())
    stream_ended_wall = time.perf_counter()
    queue.close()
    worker_thread.join()
    for record in inferences:
        record["completed_during_stream"] = record["completed_wall_ms"] <= round((stream_ended_wall - start_wall) * 1000)
    payload = {
        "source": str(args.source.resolve()),
        "sample_fps": args.fps,
        "sampled_frames": sampled_frames,
        "stream_wall_seconds": round(stream_ended_wall - start_wall, 3),
        "total_wall_seconds": round(time.perf_counter() - start_wall, 3),
        "queue_replaced": queue.replaced,
        "queue_dropped": queue.dropped,
        "fragment_events": fragment_events,
        "inferences": inferences,
        "failures": failures,
    }
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "stream_wall_seconds": payload["stream_wall_seconds"],
        "inferences": len(inferences),
        "during_stream": sum(bool(item["completed_during_stream"]) for item in inferences),
        "queue_replaced": queue.replaced,
        "failures": len(failures),
        "result": str(args.output.resolve()),
    }, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
