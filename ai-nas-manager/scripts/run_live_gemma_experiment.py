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
from online_fragmentation import GrayFrame, EventKind, OnlineMultiSignalFragmenter, fragment_config
from scene_segmentation import (
    AdaptiveMemoryShadowDetector,
    OnlineHybridSceneSegmenter,
    SceneSample,
    pelt_boundaries,
)


def read_control_state(path: Path | None) -> str:
    """Read the optional PC App playback state without failing standalone runs."""

    if path is None:
        return "running"
    try:
        return path.read_text(encoding="ascii").strip().casefold()
    except OSError:
        return "running"


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("--detector", type=Path, default=Path("models/yolox_tiny.onnx"))
    parser.add_argument("--fps", type=float, default=2.0)
    parser.add_argument("--model", default="gemma4-12b-qat")
    parser.add_argument("--lm-studio-url", default="http://127.0.0.1:1234")
    parser.add_argument("--pelt-penalty", type=float, default=0.35)
    parser.add_argument("--pelt-min-scene-seconds", type=float, default=3.0)
    parser.add_argument(
        "--processing-mode",
        choices=("realtime", "static"),
        default="realtime",
        help="realtime follows source timestamps; static processes as fast as possible",
    )
    parser.add_argument(
        "--emit-events",
        action="store_true",
        help="emit machine-readable inference events for the PC App",
    )
    parser.add_argument("--output", type=Path, default=Path("docs/live-gemma-experiment.json"))
    parser.add_argument(
        "--control-file",
        type=Path,
        help="optional file containing 'running' or 'paused' for PC App playback sync",
    )
    args = parser.parse_args()

    import cv2

    detector = YoloXOnnxDetector(args.detector, class_ids={0, 1, 2, 3, 5, 7})
    fragmenter = OnlineMultiSignalFragmenter(
        args.source.stem,
        fragment_config("fused-v2-balanced"),
    )
    scene_segmenter = OnlineHybridSceneSegmenter(args.source.stem)
    adaptive_shadow = AdaptiveMemoryShadowDetector()
    queue = LatestEvidenceQueue(max_fragments=4 if args.processing_mode == "realtime" else 128)
    client = LMStudioVisionClient(base_url=args.lm_studio_url, model=args.model)
    fragment_events: list[dict[str, object]] = []
    inferences: list[dict[str, object]] = []
    failures: list[dict[str, object]] = []
    scene_events: list[dict[str, object]] = []
    scene_timestamps_ms: list[int] = []
    scene_state_vectors: list[tuple[float, ...]] = []
    adaptive_events: list[dict[str, object]] = []
    adaptive_trace: list[dict[str, object]] = []
    adaptive_state_trace: list[dict[str, object]] = []
    previous_scene = ""
    start_wall = time.perf_counter()
    stream_ended_wall: float | None = None

    def wait_for_worker_resume() -> None:
        while read_control_state(args.control_file) == "paused":
            time.sleep(0.1)

    def wait_for_playback_resume() -> None:
        nonlocal start_wall
        paused_at: float | None = None
        while read_control_state(args.control_file) == "paused":
            if paused_at is None:
                paused_at = time.perf_counter()
            time.sleep(0.1)
        if paused_at is not None:
            start_wall += time.perf_counter() - paused_at

    def worker() -> None:
        nonlocal previous_scene
        while True:
            evidence = queue.get()
            if evidence is None:
                return
            wait_for_worker_resume()
            started = time.perf_counter()
            try:
                result = client.analyze(evidence, previous_scene)
                completed = time.perf_counter()
                previous_scene = str(result.get("observation_ja", previous_scene))
                record = {
                    **result,
                    "trigger_reason": str(evidence.metadata.get("event", {}).get("reason", "")),
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
                if args.emit_events:
                    print(
                        "AINAS_EVENT " + json.dumps(record, ensure_ascii=False, separators=(",", ":")),
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
        if args.processing_mode == "realtime":
            wait_for_playback_resume()
        ok, image = capture.read()
        if not ok:
            break
        if decoded_index % every:
            decoded_index += 1
            continue
        timestamp_ms = round(decoded_index * 1000 / source_fps)
        if args.processing_mode == "realtime":
            while True:
                wait_for_playback_resume()
                delay = start_wall + timestamp_ms / 1000 - time.perf_counter()
                if delay <= 0:
                    break
                time.sleep(min(delay, 0.1))
        detections = detector.detect(image)
        object_change = object_change_score(previous_detections, detections)
        previous_detections = detections
        gray = cv2.resize(cv2.cvtColor(image, cv2.COLOR_BGR2GRAY), (320, 180))
        gray_frame = GrayFrame(timestamp_ms, 320, 180, gray.tobytes())
        events = fragmenter.process(
            gray_frame,
            object_change=object_change,
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
            timestamp_ms,
            visual_change=visual_change,
            object_change=object_change,
        )))
        histogram = cv2.calcHist([gray], [0], None, [16], [0, 256]).reshape(-1)
        histogram_total = max(1.0, float(histogram.sum()))
        object_groups = ("person", "two_wheeler", "road_vehicle")
        state_vector = tuple(float(value) / histogram_total for value in histogram) + tuple(
            min(1.0, sum(item.trigger_group == group for item in detections) / 5.0)
            for group in object_groups
        )
        scene_state_vectors.append(state_vector)
        scene_timestamps_ms.append(timestamp_ms)
        adaptive_events.extend(
            event.as_dict() for event in adaptive_shadow.process(timestamp_ms, state_vector)
        )
        adaptive_state_trace.append({
            "timestamp_ms": timestamp_ms,
            "vector": [round(value, 7) for value in state_vector],
        })
        if adaptive_shadow.last_snapshot is not None:
            adaptive_trace.append(adaptive_shadow.last_snapshot.as_dict())
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
    scene_events.extend(event.as_dict() for event in scene_segmenter.finish())
    pelt_indices = pelt_boundaries(
        scene_state_vectors,
        penalty=args.pelt_penalty,
        min_size=max(2, round(args.fps * args.pelt_min_scene_seconds)),
    )
    stream_ended_wall = time.perf_counter()
    queue.close()
    worker_thread.join()
    for record in inferences:
        record["completed_during_stream"] = record["completed_wall_ms"] <= round((stream_ended_wall - start_wall) * 1000)
    payload = {
        "source": str(args.source.resolve()),
        "processing_mode": args.processing_mode,
        "sample_fps": args.fps,
        "sampled_frames": sampled_frames,
        "stream_wall_seconds": round(stream_ended_wall - start_wall, 3),
        "total_wall_seconds": round(time.perf_counter() - start_wall, 3),
        "queue_replaced": queue.replaced,
        "queue_dropped": queue.dropped,
        "fragment_events": fragment_events,
        "scene_segmentation": {
            "online_algorithm": "cusum-bocpd-semantic-veto-v1",
            "online_boundaries": scene_events,
            "offline_algorithm": "pelt-luma-object-state-v1",
            "pelt_penalty": args.pelt_penalty,
            "pelt_boundaries_ms": [scene_timestamps_ms[index] for index in pelt_indices],
            "shadow_algorithms": {
                "adaptive_memory_v1": {
                    "authoritative": False,
                    "boundaries": adaptive_events,
                    "trace": adaptive_trace,
                    "state_trace": adaptive_state_trace,
                },
            },
        },
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
        "online_scene_boundaries": len(scene_events),
        "pelt_scene_boundaries": len(pelt_indices),
        "result": str(args.output.resolve()),
    }, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
