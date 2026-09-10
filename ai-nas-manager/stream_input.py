"""Reconnectable OpenCV input adapter for RTSP experiments."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from dataclasses import dataclass
from enum import Enum
from typing import Iterator


class StreamEventKind(str, Enum):
    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    RECONNECT_FAILED = "reconnect_failed"
    FRAME = "frame"


@dataclass(frozen=True, slots=True)
class StreamEvent:
    kind: StreamEventKind
    timeline_ms: int
    connection: int
    image: object | None = None
    detail: str = ""

    def as_dict(self) -> dict[str, object]:
        result = {
            "kind": self.kind.value,
            "timeline_ms": self.timeline_ms,
            "connection": self.connection,
        }
        if self.detail:
            result["detail"] = self.detail
        return result


@dataclass(frozen=True, slots=True)
class RTSPInputConfig:
    sample_fps: float = 2.0
    reconnect_delay_seconds: float = 0.35
    open_timeout_ms: int = 1_500
    read_timeout_ms: int = 1_000

    def __post_init__(self) -> None:
        if self.sample_fps <= 0 or self.reconnect_delay_seconds < 0:
            raise ValueError("sampling and reconnect values must be valid")
        if self.open_timeout_ms <= 0 or self.read_timeout_ms <= 0:
            raise ValueError("timeouts must be positive")


class ReconnectableOpenCVStream:
    """Yield low-rate current frames and explicit connection state changes.

    The continuous timeline uses a monotonic clock because RTP timestamps can
    reset after a camera or publisher restarts.  A Rockchip adapter should also
    retain original RTP/PTS as separate source metadata.
    """

    def __init__(self, url: str, config: RTSPInputConfig | None = None) -> None:
        self.url = url
        self.config = config or RTSPInputConfig()

    def _open(self):
        import cv2

        os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp")
        params = [
            cv2.CAP_PROP_OPEN_TIMEOUT_MSEC,
            self.config.open_timeout_ms,
            cv2.CAP_PROP_READ_TIMEOUT_MSEC,
            self.config.read_timeout_ms,
        ]
        try:
            return cv2.VideoCapture(self.url, cv2.CAP_FFMPEG, params)
        except TypeError:
            return cv2.VideoCapture(self.url, cv2.CAP_FFMPEG)

    def events(self, duration_seconds: float) -> Iterator[StreamEvent]:
        if duration_seconds <= 0:
            raise ValueError("duration_seconds must be positive")
        start = time.monotonic()
        deadline = start + duration_seconds
        next_sample = start
        connection = 0
        capture = None
        try:
            while time.monotonic() < deadline:
                if capture is None:
                    candidate = self._open()
                    now = time.monotonic()
                    if not candidate.isOpened():
                        candidate.release()
                        yield StreamEvent(
                            StreamEventKind.RECONNECT_FAILED,
                            round((now - start) * 1000),
                            connection,
                        )
                        time.sleep(min(self.config.reconnect_delay_seconds, max(0.0, deadline - now)))
                        continue
                    capture = candidate
                    connection += 1
                    next_sample = now
                    yield StreamEvent(StreamEventKind.CONNECTED, round((now - start) * 1000), connection)

                ok, image = capture.read()
                now = time.monotonic()
                if not ok:
                    capture.release()
                    capture = None
                    yield StreamEvent(StreamEventKind.DISCONNECTED, round((now - start) * 1000), connection)
                    time.sleep(min(self.config.reconnect_delay_seconds, max(0.0, deadline - now)))
                    continue
                if now < next_sample:
                    continue
                while next_sample <= now:
                    next_sample += 1.0 / self.config.sample_fps
                yield StreamEvent(
                    StreamEventKind.FRAME,
                    round((now - start) * 1000),
                    connection,
                    image,
                )
        finally:
            if capture is not None:
                capture.release()


class ReconnectableFFmpegStream:
    """Decode RTSP into sampled BGR frames and restart FFmpeg after gaps.

    A connection is reported only after a complete decoded frame arrives. This
    prevents an RTSP handshake without usable video from counting as recovery.
    """

    def __init__(
        self,
        url: str,
        ffmpeg: str,
        config: RTSPInputConfig | None = None,
        *,
        width: int = 640,
        height: int = 360,
    ) -> None:
        if width <= 0 or height <= 0:
            raise ValueError("frame dimensions must be positive")
        self.url = url
        self.ffmpeg = ffmpeg
        self.config = config or RTSPInputConfig()
        self.width = width
        self.height = height

    def _start(self) -> subprocess.Popen:
        timeout_us = self.config.read_timeout_ms * 1_000
        return subprocess.Popen(
            [
                self.ffmpeg,
                "-hide_banner", "-loglevel", "error", "-nostdin",
                "-rtsp_transport", "tcp", "-timeout", str(timeout_us),
                "-i", self.url, "-an", "-sn",
                "-vf", f"fps={self.config.sample_fps},scale={self.width}:{self.height}",
                "-pix_fmt", "bgr24", "-f", "rawvideo", "pipe:1",
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )

    def events(self, duration_seconds: float) -> Iterator[StreamEvent]:
        if duration_seconds <= 0:
            raise ValueError("duration_seconds must be positive")
        import numpy as np

        start = time.monotonic()
        deadline = start + duration_seconds
        connection = 0
        process = None
        process_had_frame = False
        frame_bytes = self.width * self.height * 3
        try:
            while time.monotonic() < deadline:
                if process is None:
                    process = self._start()
                    process_had_frame = False
                assert process.stdout is not None
                raw = process.stdout.read(frame_bytes)
                now = time.monotonic()
                if len(raw) != frame_bytes:
                    process.wait(timeout=3)
                    detail = ""
                    if process.stderr is not None:
                        detail = process.stderr.read().decode("utf-8", errors="replace").strip()[-1000:]
                    process = None
                    kind = (
                        StreamEventKind.DISCONNECTED
                        if process_had_frame
                        else StreamEventKind.RECONNECT_FAILED
                    )
                    yield StreamEvent(kind, round((now - start) * 1000), connection, detail=detail)
                    time.sleep(min(self.config.reconnect_delay_seconds, max(0.0, deadline - now)))
                    continue
                if not process_had_frame:
                    connection += 1
                    process_had_frame = True
                    yield StreamEvent(StreamEventKind.CONNECTED, round((now - start) * 1000), connection)
                image = np.frombuffer(raw, dtype=np.uint8).reshape((self.height, self.width, 3))
                yield StreamEvent(
                    StreamEventKind.FRAME,
                    round((now - start) * 1000),
                    connection,
                    image,
                )
        finally:
            if process is not None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3)
