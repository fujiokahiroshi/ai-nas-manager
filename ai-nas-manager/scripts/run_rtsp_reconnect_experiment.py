"""Publish a file as RTSP, interrupt it, and verify pipeline reconnection."""

from __future__ import annotations

import argparse
import json
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from object_detection import YoloXOnnxDetector, object_change_score
from online_fragmentation import GrayFrame, OnlineMultiSignalFragmenter
from stream_input import ReconnectableFFmpegStream, RTSPInputConfig, StreamEventKind


def wait_port(port: int, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return
        except OSError:
            time.sleep(0.05)
    raise RuntimeError(f"port {port} did not open")


def stop_process(process: subprocess.Popen | None) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=3)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("--ffmpeg", required=True, type=Path)
    parser.add_argument("--mediamtx", required=True, type=Path)
    parser.add_argument("--detector", type=Path, default=Path("models/yolox_tiny.onnx"))
    parser.add_argument("--port", type=int, default=18554)
    parser.add_argument("--duration", type=float, default=20.0)
    parser.add_argument("--interrupt-at", type=float, default=7.0)
    parser.add_argument("--outage", type=float, default=3.0)
    parser.add_argument("--output", type=Path, default=Path("docs/rtsp-reconnect-experiment.json"))
    args = parser.parse_args()
    if args.interrupt_at <= 0 or args.interrupt_at + args.outage >= args.duration:
        raise ValueError("interruption must fit inside experiment duration")

    runtime = args.output.parent / ".rtsp-runtime"
    runtime.mkdir(parents=True, exist_ok=True)
    config = runtime / "mediamtx.yml"
    config.write_text(
        "\n".join([
            "logLevel: warn",
            f"rtspAddress: :{args.port}",
            "rtmp: false",
            "hls: false",
            "webrtc: false",
            "srt: false",
            "paths:",
            "  all_others:",
        ]),
        encoding="utf-8",
    )
    url = f"rtsp://127.0.0.1:{args.port}/ai-nas-test"
    mediamtx_path = args.mediamtx.resolve()
    ffmpeg_path = args.ffmpeg.resolve()
    server = subprocess.Popen(
        [str(mediamtx_path), str(config.resolve())],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        cwd=runtime,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )
    publisher_lock = threading.Lock()
    publisher: subprocess.Popen | None = None

    def start_publisher() -> subprocess.Popen:
        return subprocess.Popen(
            [
                str(ffmpeg_path), "-hide_banner", "-loglevel", "error", "-nostdin",
                "-re", "-stream_loop", "-1", "-i", str(args.source), "-an",
                "-vf", "scale=640:-2,fps=25",
                "-c:v", "libx264", "-preset", "ultrafast", "-tune", "zerolatency",
                "-pix_fmt", "yuv420p", "-g", "25", "-keyint_min", "25",
                "-sc_threshold", "0", "-bf", "0",
                "-f", "rtsp", "-rtsp_transport", "tcp", url,
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )

    try:
        wait_port(args.port)
        publisher = start_publisher()
        experiment_start = time.monotonic()

        def interrupt_publisher() -> None:
            nonlocal publisher
            time.sleep(max(0.0, experiment_start + args.interrupt_at - time.monotonic()))
            with publisher_lock:
                stop_process(publisher)
                publisher = None
            time.sleep(args.outage)
            with publisher_lock:
                publisher = start_publisher()

        interrupter = threading.Thread(target=interrupt_publisher, name="publisher-interrupt")
        interrupter.start()
        detector = YoloXOnnxDetector(args.detector, class_ids={0, 1, 2, 3, 5, 7})
        fragmenter = OnlineMultiSignalFragmenter("rtsp-ai-nas-test")
        stream = ReconnectableFFmpegStream(
            url,
            str(ffmpeg_path),
            RTSPInputConfig(
                sample_fps=2.0,
                reconnect_delay_seconds=0.25,
                read_timeout_ms=2_000,
            ),
        )
        previous_detections = []
        state_events: list[dict[str, object]] = []
        fragment_events: list[dict[str, object]] = []
        frames = 0
        frames_per_connection: dict[int, int] = {}
        for event in stream.events(args.duration):
            if event.kind is not StreamEventKind.FRAME:
                state_events.append(event.as_dict())
                if event.kind is StreamEventKind.DISCONNECTED:
                    fragment_events.extend(
                        item.as_dict() for item in fragmenter.discontinuity(event.timeline_ms)
                    )
                    previous_detections = []
                continue
            import cv2

            image = event.image
            detections = detector.detect(image)
            change = object_change_score(previous_detections, detections)
            previous_detections = detections
            gray = cv2.resize(cv2.cvtColor(image, cv2.COLOR_BGR2GRAY), (320, 180))
            produced = fragmenter.process(
                GrayFrame(event.timeline_ms, 320, 180, gray.tobytes()),
                object_change=change,
            )
            fragment_events.extend(item.as_dict() for item in produced)
            frames += 1
            frames_per_connection[event.connection] = frames_per_connection.get(event.connection, 0) + 1
        fragment_events.extend(item.as_dict() for item in fragmenter.finish())
        interrupter.join(timeout=args.outage + 5)
        connected = [item for item in state_events if item["kind"] == "connected"]
        disconnected = [item for item in state_events if item["kind"] == "disconnected"]
        first_disconnect = disconnected[0]["timeline_ms"] if disconnected else None
        later_connections = [
            item for item in connected
            if first_disconnect is not None and item["timeline_ms"] > first_disconnect
        ]
        reconnect_ms = (
            later_connections[0]["timeline_ms"] - first_disconnect
            if later_connections else None
        )
        usable_connections = {
            connection: count
            for connection, count in frames_per_connection.items()
            if count >= 3
        }
        payload = {
            "rtsp_url": url,
            "duration_seconds": args.duration,
            "interrupt_at_seconds": args.interrupt_at,
            "requested_outage_seconds": args.outage,
            "sampled_frames": frames,
            "frames_per_connection": frames_per_connection,
            "connections": len(connected),
            "disconnects": len(disconnected),
            "reconnect_gap_ms": reconnect_ms,
            "usable_connections": usable_connections,
            "state_events": state_events,
            "fragment_events": fragment_events,
            "passed": (
                len(connected) >= 2
                and bool(disconnected)
                and len(usable_connections) >= 2
            ),
        }
        args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({
            key: payload[key]
            for key in ("sampled_frames", "frames_per_connection", "connections", "disconnects", "reconnect_gap_ms", "passed")
        }, ensure_ascii=False))
        print(args.output.resolve())
        if not payload["passed"]:
            raise SystemExit(1)
    finally:
        with publisher_lock:
            stop_process(publisher)
        stop_process(server)


if __name__ == "__main__":
    main()
