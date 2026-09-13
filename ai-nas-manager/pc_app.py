"""Local PC prototype for browsing video fragments and Gemma text."""

from __future__ import annotations

import argparse
import json
import mimetypes
import re
import sqlite3
import subprocess
import sys
import threading
import uuid
import webbrowser
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from fragment_admission import confirmed_records, evaluate_admission


ROOT = Path(__file__).resolve().parent
DEFAULT_RESULT = ROOT / "docs" / "live-gemma-pedestrian-2026-09-10.json"
DEFAULT_UI = ROOT / "pc-app" / "index.html"
DEFAULT_DB = ROOT / "pc_app.sqlite3"
DEFAULT_IMPORT_DIR = ROOT / "media" / "pc-app-imports"
DEFAULT_ANALYSIS_DIR = ROOT / "media" / "pc-app-analysis"
VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v", ".ts", ".m2ts"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}


def imported_video_path(directory: Path, filename: str) -> Path:
    """Return a unique path inside the import directory for a browser upload."""

    basename = Path(unquote(filename)).name
    suffix = Path(basename).suffix.casefold()
    if suffix not in VIDEO_EXTENSIONS:
        raise ValueError("unsupported video extension")
    stem = re.sub(r"[^\w .()-]", "_", Path(basename).stem, flags=re.UNICODE).strip(" .")
    if not stem:
        stem = "video"
    return directory / f"{uuid.uuid4().hex[:10]}-{stem[:100]}{suffix}"


def browse_local_video(initial_directory: Path) -> Path | None:
    """Open the Windows file dialog without passing through the browser picker."""

    import tkinter as tk
    from tkinter import filedialog

    root: tk.Tk | None = None
    try:
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        root.update()
        selected = filedialog.askopenfilename(
            parent=root,
            title="解析する映像を選択",
            initialdir=str(initial_directory),
            filetypes=[
                ("映像ファイル", "*.mp4 *.mov *.mkv *.avi *.webm *.m4v *.ts *.m2ts"),
                ("すべてのファイル", "*.*"),
            ],
        )
    except tk.TclError as exc:
        raise RuntimeError("Windowsファイル選択画面を開けません") from exc
    finally:
        if root is not None:
            root.destroy()
    if not selected:
        return None
    source = Path(selected).resolve()
    if not source.is_file() or source.suffix.casefold() not in VIDEO_EXTENSIONS:
        raise ValueError("対応していない映像ファイルです")
    return source


def browse_local_image(initial_directory: Path) -> Path | None:
    """Open the Windows file dialog for a still image."""

    import tkinter as tk
    from tkinter import filedialog

    root: tk.Tk | None = None
    try:
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        root.update()
        selected = filedialog.askopenfilename(
            parent=root,
            title="解析する画像を選択",
            initialdir=str(initial_directory),
            filetypes=[
                ("画像ファイル", "*.jpg *.jpeg *.png *.webp *.bmp"),
                ("すべてのファイル", "*.*"),
            ],
        )
    except tk.TclError as exc:
        raise RuntimeError("Windowsファイル選択画面を開けません") from exc
    finally:
        if root is not None:
            root.destroy()
    if not selected:
        return None
    source = Path(selected).resolve()
    if not source.is_file() or source.suffix.casefold() not in IMAGE_EXTENSIONS:
        raise ValueError("対応していない画像ファイルです")
    return source


def media_kind_for_path(source: Path) -> str:
    suffix = source.suffix.casefold()
    if suffix in VIDEO_EXTENSIONS:
        return "video"
    if suffix in IMAGE_EXTENSIONS:
        return "image"
    raise ValueError("対応していないメディアファイルです")


def fragment_records(payload: dict) -> list[dict]:
    source = str(payload["source"])
    records = []
    for item in payload.get("inferences", []):
        fragment_id = str(item["fragment_id"])
        revision = int(item["revision"])
        admission = evaluate_admission(item)
        records.append({
            "id": f"{fragment_id}:r{revision}",
            "fragment_id": fragment_id,
            "revision": revision,
            "timestamp_ms": int(item["source_timestamp_ms"]),
            "observation": str(item.get("observation_ja", "")),
            "action": str(item.get("action_ja", "")),
            "change_text": str(item.get("change_from_previous_ja", "")),
            "objects": [str(value) for value in item.get("objects", [])],
            "confidence": float(item.get("confidence", 0.0)),
            "admission_state": admission.state,
            "admission_score": admission.score,
            "completed_during_stream": bool(item.get("completed_during_stream", False)),
            "source_path": source,
        })
    return records


def scene_boundaries(payload: dict) -> tuple[list[int], str]:
    """Choose reviewed boundaries, finalized PELT, then provisional online boundaries."""

    segmentation = payload.get("scene_segmentation", {})
    if not isinstance(segmentation, dict):
        return [], "time-gap fallback"
    manual = sorted({
        int(value)
        for value in segmentation.get("manual_boundaries_ms", [])
        if int(value) > 0
    })
    if manual:
        return manual, "Manual review"
    pelt = sorted({int(value) for value in segmentation.get("pelt_boundaries_ms", []) if int(value) > 0})
    if pelt:
        return pelt, "PELT final"
    online = segmentation.get("online_boundaries", [])
    values = sorted({
        int(item.get("boundary_ms", 0))
        for item in online
        if isinstance(item, dict) and int(item.get("boundary_ms", 0)) > 0
    })
    return values, "CUSUM/BOCPD provisional" if values else "time-gap fallback"


def scene_boundary_markers(payload: dict) -> dict[str, list[int]]:
    segmentation = payload.get("scene_segmentation", {})
    if not isinstance(segmentation, dict):
        segmentation = {}
    shadow = segmentation.get("shadow_algorithms", {})
    if not isinstance(shadow, dict):
        shadow = {}

    def event_times(name: str) -> list[int]:
        value = shadow.get(name, {})
        if not isinstance(value, dict):
            return []
        return [int(item["boundary_ms"]) for item in value.get("boundaries", [])]

    truth = payload.get("scene_ground_truth", {})
    if not isinstance(truth, dict):
        truth = {}
    return {
        "current": [
            int(item["boundary_ms"])
            for item in segmentation.get("online_boundaries", [])
        ],
        "adaptive": event_times("adaptive_memory_v1"),
        "tuned": event_times("adaptive_memory_tuned_v1"),
        "ground_truth": [int(value) for value in truth.get("boundaries_ms", [])],
    }


def scene_records(
    fragments: list[dict],
    boundaries_ms: list[int],
    boundary_method: str,
    *,
    fallback_gap_ms: int = 15_000,
    summaries: list[dict] | None = None,
) -> list[dict]:
    """Group ordered Fragment revisions into Scene records."""

    if not fragments:
        return []
    ordered = sorted(fragments, key=lambda item: (int(item["timestamp_ms"]), int(item["revision"])))
    starts = sorted({value for value in boundaries_ms if value > int(ordered[0]["timestamp_ms"])})
    groups: list[list[dict]] = [[]]
    boundary_index = 0
    previous_timestamp = int(ordered[0]["timestamp_ms"])
    for item in ordered:
        timestamp = int(item["timestamp_ms"])
        crossed_boundary = False
        while boundary_index < len(starts) and timestamp >= starts[boundary_index]:
            crossed_boundary = True
            boundary_index += 1
        fallback_split = not starts and groups[-1] and timestamp - previous_timestamp > fallback_gap_ms
        if groups[-1] and (crossed_boundary or fallback_split):
            groups.append([])
        groups[-1].append(item)
        previous_timestamp = timestamp

    scenes = []
    for group_index, group in enumerate(groups):
        first, last = group[0], group[-1]
        objects = list(dict.fromkeys(value for item in group for value in item.get("objects", [])))
        descriptions = [str(item.get("user_text") or item.get("observation") or "") for item in group]
        summary = next((value for value in reversed(descriptions) if value), "説明を生成中")
        start_ms = int(first["timestamp_ms"])
        end_ms = (
            int(groups[group_index + 1][0]["timestamp_ms"])
            if group_index + 1 < len(groups)
            else int(last["timestamp_ms"]) + 3_000
        )
        generated = next((
            item for item in (summaries or [])
            if str(item.get("scene_id", "")) == f"scene-{group_index + 1:04d}"
            or int(item.get("start_ms", -1)) == start_ms
        ), None)
        if generated and str(generated.get("summary_ja", "")).strip():
            summary = str(generated["summary_ja"]).strip()
            objects = list(dict.fromkeys([*objects, *(str(value) for value in generated.get("objects", []))]))
        scenes.append({
            "id": f"scene-{group_index + 1:04d}",
            "index": group_index + 1,
            "start_ms": start_ms,
            "end_ms": max(start_ms + 1, end_ms),
            "summary": summary,
            "objects": objects,
            "confidence": round(sum(float(item.get("confidence", 0.0)) for item in group) / len(group), 4),
            "completed_during_stream": any(bool(item.get("completed_during_stream")) for item in group),
            "favorite": any(bool(item.get("favorite")) for item in group),
            "boundary_method": boundary_method,
            "summary_method": str(generated.get("method", "")) if generated else "fragment_latest",
            "activities": list(generated.get("activities", [])) if generated else [],
            "important_change": str(generated.get("important_change_ja", "")) if generated else "",
            "thumbnail_ids": [item["id"] for item in group[-3:]],
            "fragment_count": len(group),
            "fragments": group,
        })
    return scenes


class FragmentStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS fragments (
                    id TEXT PRIMARY KEY,
                    fragment_id TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    timestamp_ms INTEGER NOT NULL,
                    observation TEXT NOT NULL,
                    user_text TEXT NOT NULL DEFAULT '',
                    action TEXT NOT NULL,
                    change_text TEXT NOT NULL,
                    objects_json TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    admission_state TEXT NOT NULL DEFAULT 'candidate',
                    admission_score REAL NOT NULL DEFAULT 0,
                    completed_during_stream INTEGER NOT NULL,
                    favorite INTEGER NOT NULL DEFAULT 0,
                    source_path TEXT NOT NULL
                )
            """)
            columns = {row[1] for row in db.execute("PRAGMA table_info(fragments)")}
            if "admission_state" not in columns:
                db.execute("ALTER TABLE fragments ADD COLUMN admission_state TEXT NOT NULL DEFAULT 'candidate'")
            if "admission_score" not in columns:
                db.execute("ALTER TABLE fragments ADD COLUMN admission_score REAL NOT NULL DEFAULT 0")

    def import_records(self, records: list[dict]) -> None:
        with self._lock, self._connect() as db:
            db.executemany("""
                INSERT INTO fragments (
                    id, fragment_id, revision, timestamp_ms, observation,
                    action, change_text, objects_json, confidence,
                    admission_state, admission_score,
                    completed_during_stream, source_path
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    timestamp_ms=excluded.timestamp_ms,
                    observation=excluded.observation,
                    action=excluded.action,
                    change_text=excluded.change_text,
                    objects_json=excluded.objects_json,
                    confidence=excluded.confidence,
                    admission_state=excluded.admission_state,
                    admission_score=excluded.admission_score,
                    completed_during_stream=excluded.completed_during_stream,
                    source_path=excluded.source_path
            """, [(
                item["id"], item["fragment_id"], item["revision"], item["timestamp_ms"],
                item["observation"], item["action"], item["change_text"],
                json.dumps(item["objects"], ensure_ascii=False), item["confidence"],
                item["admission_state"], item["admission_score"],
                int(item["completed_during_stream"]), item["source_path"],
            ) for item in records])

    def delete_source(self, source_path: str) -> None:
        with self._lock, self._connect() as db:
            db.execute("DELETE FROM fragments WHERE source_path = ?", (source_path,))

    def list(
        self,
        query: str = "",
        favorites_only: bool = False,
        source_path: str | None = None,
    ) -> list[dict]:
        conditions: list[str] = []
        parameters: list[object] = []
        if source_path:
            conditions.append("source_path = ?")
            parameters.append(source_path)
        if query:
            conditions.append("(observation LIKE ? OR user_text LIKE ? OR action LIKE ? OR objects_json LIKE ?)")
            needle = f"%{query}%"
            parameters.extend([needle] * 4)
        if favorites_only:
            conditions.append("favorite = 1")
        where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
        with self._lock, self._connect() as db:
            rows = db.execute(
                "SELECT * FROM fragments" + where + " ORDER BY timestamp_ms, revision",
                parameters,
            ).fetchall()
        return [self._row(row) for row in rows]

    def update(self, record_id: str, *, user_text: str, favorite: bool) -> dict | None:
        with self._lock, self._connect() as db:
            cursor = db.execute(
                "UPDATE fragments SET user_text = ?, favorite = ? WHERE id = ?",
                (user_text.strip(), int(favorite), record_id),
            )
            if cursor.rowcount == 0:
                return None
            row = db.execute("SELECT * FROM fragments WHERE id = ?", (record_id,)).fetchone()
        return self._row(row)

    @staticmethod
    def _row(row: sqlite3.Row) -> dict:
        result = dict(row)
        result["objects"] = json.loads(result.pop("objects_json"))
        result["favorite"] = bool(result["favorite"])
        result["completed_during_stream"] = bool(result["completed_during_stream"])
        return result


@dataclass
class AppState:
    store: FragmentStore
    source: Path | None
    ui: bytes
    thumbnails: dict[str, bytes]
    boundaries_ms: list[int]
    boundary_method: str
    scene_summaries: list[dict]
    shadow_boundaries: list[dict]
    boundary_markers: dict[str, list[int]]
    import_dir: Path = DEFAULT_IMPORT_DIR
    media_kind: str = "video"
    media_revision: int = 0
    lock: threading.RLock = field(default_factory=threading.RLock, repr=False)
    browse_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    analysis_state: str = "ready"
    analysis_mode: str = ""
    analysis_phase: str = ""
    analysis_message: str = ""
    analysis_progress: float = 1.0
    analysis_fragment_count: int = 0
    analysis_error: str = ""
    analysis_paused: bool = False
    analysis_control_path: Path | None = field(default=None, repr=False)
    analysis_thread: threading.Thread | None = field(default=None, repr=False)
    analysis_process: subprocess.Popen[str] | None = field(default=None, repr=False)
    analysis_cancel_requested: bool = False

    def select_source(self, source: Path, media_kind: str | None = None) -> None:
        """Switch playback to an unanalysed source without mixing old results."""

        self.cancel_running_analysis()
        with self.lock:
            self.source = source.resolve()
            self.media_kind = media_kind or media_kind_for_path(self.source)
            self.media_revision += 1
            self.store.delete_source(str(self.source))
            self.thumbnails = {}
            self.boundaries_ms = []
            self.boundary_method = "not analyzed"
            self.scene_summaries = []
            self.shadow_boundaries = []
            self.boundary_markers = {
                "current": [],
                "adaptive": [],
                "tuned": [],
                "ground_truth": [],
            }
            self.analysis_state = "not_analyzed"
            self.analysis_mode = ""
            self.analysis_phase = ""
            self.analysis_message = "画像解析を開始してください" if self.media_kind == "image" else "解析方法を選択してください"
            self.analysis_progress = 0.0
            self.analysis_fragment_count = 0
            self.analysis_error = ""
            self.analysis_paused = False
            self.analysis_control_path = None
            self.analysis_cancel_requested = False

    def cancel_running_analysis(self) -> None:
        """Stop the current subprocess before switching to another source."""

        with self.lock:
            if self.analysis_state != "running":
                return
            self.analysis_cancel_requested = True
            process = self.analysis_process
            thread = self.analysis_thread
            if self.analysis_control_path is not None:
                self.analysis_control_path.write_text("cancelled", encoding="ascii")

        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)

        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=10)
            if thread.is_alive():
                raise RuntimeError("実行中の解析を停止できませんでした")

        with self.lock:
            if self.analysis_state == "running":
                self.analysis_state = "ready"
                self.analysis_phase = "cancelled"
                self.analysis_message = "別のメディア選択のため解析を中止しました"
                self.analysis_error = ""
            self.analysis_process = None
            self.analysis_thread = None
            self.analysis_paused = False
            self.analysis_control_path = None

    def begin_analysis(self, mode: str) -> Path | None:
        if mode not in {"static", "realtime"}:
            raise ValueError("unknown analysis mode")
        with self.lock:
            if self.analysis_state == "running":
                return None
            if self.source is None:
                raise ValueError("先に画像または映像を選択してください")
            if self.media_kind == "image" and mode != "static":
                raise ValueError("画像は静的解析を使用してください")
            source = self.source
            self.store.delete_source(str(source.resolve()))
            self.thumbnails = {}
            self.boundaries_ms = []
            self.boundary_method = "analyzing"
            self.scene_summaries = []
            self.shadow_boundaries = []
            self.boundary_markers = {key: [] for key in ("current", "adaptive", "tuned", "ground_truth")}
            self.analysis_state = "running"
            self.analysis_mode = mode
            self.analysis_phase = "fragment"
            self.analysis_message = "画像をGemmaで解析しています" if self.media_kind == "image" else "Fragmentを検出しています"
            self.analysis_progress = 0.0
            self.analysis_fragment_count = 0
            self.analysis_error = ""
            self.analysis_paused = False
            self.analysis_control_path = None
            self.analysis_process = None
            self.analysis_cancel_requested = False
            return source

    def bind_analysis_control(self, path: Path) -> None:
        """Attach the realtime worker control file to the current analysis."""

        with self.lock:
            self.analysis_control_path = path
            path.write_text("paused" if self.analysis_paused else "running", encoding="ascii")

    def set_analysis_paused(self, paused: bool) -> None:
        """Pause or resume a running realtime analysis."""

        with self.lock:
            if self.analysis_state != "running" or self.analysis_mode != "realtime":
                raise RuntimeError("realtime analysis is not running")
            self.analysis_paused = paused
            if self.analysis_control_path is not None:
                self.analysis_control_path.write_text(
                    "paused" if paused else "running",
                    encoding="ascii",
                )
            self.analysis_message = (
                "映像の一時停止に合わせて解析を一時停止しています"
                if paused
                else f"Fragment {self.analysis_fragment_count}件を解析しました"
            )

    def analysis_snapshot(self) -> dict[str, object]:
        with self.lock:
            return {
                "state": self.analysis_state,
                "mode": self.analysis_mode,
                "phase": self.analysis_phase,
                "message": self.analysis_message,
                "progress": round(self.analysis_progress, 4),
                "fragment_count": self.analysis_fragment_count,
                "error": self.analysis_error,
                "paused": self.analysis_paused,
            }


def build_thumbnails(source: Path, records: list[dict]) -> dict[str, bytes]:
    import cv2

    capture = cv2.VideoCapture(str(source))
    if not capture.isOpened():
        raise RuntimeError(f"動画を開けません: {source}")
    images: dict[str, bytes] = {}
    for item in records:
        capture.set(cv2.CAP_PROP_POS_MSEC, item["timestamp_ms"])
        ok, frame = capture.read()
        if not ok:
            images[item["id"]] = b""
            continue
        frame = cv2.resize(frame, (480, 270))
        encoded_ok, encoded = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
        images[item["id"]] = encoded.tobytes() if encoded_ok else b""
    capture.release()
    return images


def video_duration_ms(source: Path) -> int:
    import cv2

    capture = cv2.VideoCapture(str(source))
    try:
        fps = float(capture.get(cv2.CAP_PROP_FPS))
        frames = float(capture.get(cv2.CAP_PROP_FRAME_COUNT))
        return max(1, round(frames * 1000 / fps)) if fps > 0 else 1
    finally:
        capture.release()


def apply_analysis_payload(state: AppState, payload: dict) -> None:
    records = fragment_records(payload)
    state.store.import_records(records)
    boundaries_ms, boundary_method = scene_boundaries(payload)
    shadow_boundaries = list(
        payload.get("scene_segmentation", {})
        .get("shadow_algorithms", {})
        .get("adaptive_memory_v1", {})
        .get("boundaries", [])
    )
    thumbnails = build_thumbnails(Path(payload["source"]), records)
    with state.lock:
        state.thumbnails = thumbnails
        state.boundaries_ms = boundaries_ms
        state.boundary_method = boundary_method
        state.scene_summaries = list(payload.get("scene_summaries", []))
        state.shadow_boundaries = shadow_boundaries
        state.boundary_markers = scene_boundary_markers(payload)
        state.analysis_fragment_count = len(records)


def run_pc_analysis(state: AppState, source: Path, mode: str) -> None:
    """Run the existing Fragment/Gemma/Scene pipeline and publish live progress."""

    DEFAULT_ANALYSIS_DIR.mkdir(parents=True, exist_ok=True)
    run_id = uuid.uuid4().hex[:12]
    fragment_output = DEFAULT_ANALYSIS_DIR / f"{run_id}-{source.stem}-{mode}.json"
    scene_output = DEFAULT_ANALYSIS_DIR / f"{run_id}-{source.stem}-{mode}-scenes.json"
    control_path = DEFAULT_ANALYSIS_DIR / f"{run_id}-{source.stem}-{mode}.control"
    duration_ms = video_duration_ms(source)
    command = [
        sys.executable,
        str(ROOT / "scripts" / "run_live_gemma_experiment.py"),
        str(source),
        "--processing-mode",
        mode,
        "--emit-events",
        "--output",
        str(fragment_output),
    ]
    log_tail: list[str] = []
    try:
        if mode == "realtime":
            state.bind_analysis_control(control_path)
            command.extend(["--control-file", str(control_path)])
        process = subprocess.Popen(
            command,
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        with state.lock:
            state.analysis_process = process
            cancel_requested = state.analysis_cancel_requested
        if cancel_requested:
            process.terminate()
        assert process.stdout is not None
        for raw_line in process.stdout:
            line = raw_line.strip()
            if line:
                log_tail.append(line)
                log_tail = log_tail[-12:]
            if not line.startswith("AINAS_EVENT "):
                continue
            inference = json.loads(line[len("AINAS_EVENT "):])
            record = fragment_records({"source": str(source.resolve()), "inferences": [inference]})[0]
            state.store.import_records([record])
            thumbnail = build_thumbnails(source, [record]).get(record["id"], b"")
            with state.lock:
                state.thumbnails[record["id"]] = thumbnail
                state.analysis_fragment_count += 1
                state.analysis_progress = min(0.9, int(record["timestamp_ms"]) / duration_ms * 0.9)
                state.analysis_message = (
                    "映像の一時停止に合わせて解析を一時停止しています"
                    if state.analysis_paused
                    else f"Fragment {state.analysis_fragment_count}件を解析しました"
                )
        return_code = process.wait()
        if return_code != 0 or not fragment_output.is_file():
            raise RuntimeError(log_tail[-1] if log_tail else f"解析処理が終了しました ({return_code})")
        payload = json.loads(fragment_output.read_text(encoding="utf-8"))
        if not payload.get("inferences"):
            failures = payload.get("failures", [])
            detail = str(failures[0].get("error", "Gemmaの解析結果がありません")) if failures else "Gemmaの解析結果がありません"
            raise RuntimeError(detail)
        with state.lock:
            state.analysis_phase = "scene"
            state.analysis_message = "Scene全体をGemmaで要約しています"
            state.analysis_progress = 0.92
        summary = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "summarize_gemma_scenes.py"),
                str(fragment_output),
                "--output",
                str(scene_output),
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if summary.returncode != 0 or not scene_output.is_file():
            raise RuntimeError((summary.stderr or summary.stdout or "Scene要約に失敗しました").strip().splitlines()[-1])
        payload = json.loads(scene_output.read_text(encoding="utf-8"))
        apply_analysis_payload(state, payload)
        with state.lock:
            state.analysis_state = "ready"
            state.analysis_phase = "complete"
            state.analysis_message = "解析が完了しました"
            state.analysis_progress = 1.0
            state.analysis_error = ""
    except Exception as exc:
        with state.lock:
            cancelled = state.analysis_cancel_requested
        if not cancelled and fragment_output.is_file():
            try:
                apply_analysis_payload(state, json.loads(fragment_output.read_text(encoding="utf-8")))
            except Exception:
                pass
        with state.lock:
            if cancelled:
                state.analysis_state = "ready"
                state.analysis_phase = "cancelled"
                state.analysis_message = "別のメディア選択のため解析を中止しました"
                state.analysis_error = ""
            else:
                state.analysis_state = "failed"
                state.analysis_phase = "failed"
                state.analysis_message = "解析に失敗しました"
                state.analysis_error = f"{type(exc).__name__}: {exc}"
    finally:
        if mode == "realtime":
            try:
                control_path.unlink(missing_ok=True)
            except OSError:
                pass
        with state.lock:
            state.analysis_process = None
            state.analysis_paused = False
            state.analysis_control_path = None
            state.analysis_thread = None


def run_image_analysis(state: AppState, source: Path) -> None:
    """Analyze one still image with Gemma and store it as one Fragment/Scene."""

    try:
        import cv2
        import numpy as np

        from live_semantics import FragmentEvidence, LMStudioVisionClient

        image = cv2.imdecode(np.frombuffer(source.read_bytes(), dtype=np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            raise RuntimeError(f"画像を開けません: {source}")
        height, width = image.shape[:2]
        scale = min(1.0, 1280 / max(width, height))
        if scale < 1.0:
            image = cv2.resize(image, (round(width * scale), round(height * scale)))
        encoded_ok, encoded = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 85])
        if not encoded_ok:
            raise RuntimeError("JPEG encoding failed")
        evidence = FragmentEvidence(
            fragment_id=f"image-{uuid.uuid4().hex[:10]}",
            revision=1,
            source_timestamp_ms=0,
            jpeg=encoded.tobytes(),
            metadata={"source_type": "still_image", "filename": source.name},
        )
        result = LMStudioVisionClient().analyze(evidence)
        result["completed_during_stream"] = False
        result["trigger_reason"] = "still_image"
        result["manual_marker"] = True
        record = fragment_records({"source": str(source.resolve()), "inferences": [result]})[0]
        thumb = cv2.resize(image, (480, max(1, round(image.shape[0] * 480 / image.shape[1]))))
        thumb_ok, thumb_encoded = cv2.imencode(".jpg", thumb, [cv2.IMWRITE_JPEG_QUALITY, 80])
        state.store.import_records([record])
        with state.lock:
            state.thumbnails = {record["id"]: thumb_encoded.tobytes() if thumb_ok else encoded.tobytes()}
            state.boundaries_ms = []
            state.boundary_method = "single image"
            state.scene_summaries = []
            state.shadow_boundaries = []
            state.boundary_markers = {key: [] for key in ("current", "adaptive", "tuned", "ground_truth")}
            state.analysis_fragment_count = 1
            state.analysis_state = "ready"
            state.analysis_phase = "complete"
            state.analysis_message = "画像解析が完了しました"
            state.analysis_progress = 1.0
            state.analysis_error = ""
    except Exception as exc:
        with state.lock:
            state.analysis_state = "failed"
            state.analysis_phase = "failed"
            state.analysis_message = "画像解析に失敗しました"
            state.analysis_error = f"{type(exc).__name__}: {exc}"
    finally:
        with state.lock:
            state.analysis_thread = None


class AppHandler(BaseHTTPRequestHandler):
    server_version = "AINASPC/0.1"

    @property
    def state(self) -> AppState:
        return self.server.app_state  # type: ignore[attr-defined]

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self._bytes(self.state.ui, "text/html; charset=utf-8")
        elif parsed.path == "/api/analysis/status":
            self._json(self.state.analysis_snapshot())
        elif parsed.path == "/api/fragments":
            values = parse_qs(parsed.query)
            query = values.get("q", [""])[0]
            favorites = values.get("favorites", ["0"])[0] == "1"
            source = self.state.source
            source_path = str(source.resolve()) if source is not None else None
            fragments = self.state.store.list(query, favorites, source_path) if source_path else []
            self._json({
                "source_name": source.name if source is not None else "",
                "media_kind": self.state.media_kind,
                "media_revision": self.state.media_revision,
                "shadow_boundaries": self.state.shadow_boundaries,
                "boundary_markers": self.state.boundary_markers,
                "analysis": self.state.analysis_snapshot(),
                "fragments": fragments,
            })
        elif parsed.path == "/api/scenes":
            values = parse_qs(parsed.query)
            query = values.get("q", [""])[0].casefold()
            favorites = values.get("favorites", ["0"])[0] == "1"
            source = self.state.source
            source_records = self.state.store.list(source_path=str(source.resolve())) if source is not None else []
            scenes = scene_records(
                source_records,
                self.state.boundaries_ms,
                self.state.boundary_method,
                summaries=self.state.scene_summaries,
            )
            if query:
                scenes = [scene for scene in scenes if query in " ".join(
                    [scene["summary"], *scene["objects"], *(
                        str(item.get("user_text") or item.get("observation") or "")
                        for item in scene["fragments"]
                    )]
                ).casefold()]
            if favorites:
                scenes = [scene for scene in scenes if scene["favorite"]]
            self._json({
                "source_name": source.name if source is not None else "",
                "media_kind": self.state.media_kind,
                "media_revision": self.state.media_revision,
                "scenes": scenes,
                "analysis": self.state.analysis_snapshot(),
                "shadow_boundaries": self.state.shadow_boundaries,
                "boundary_markers": self.state.boundary_markers,
            })
        elif parsed.path.startswith("/api/thumbnail/"):
            try:
                record_id = unquote(parsed.path[len("/api/thumbnail/"):])
                image = self.state.thumbnails[record_id]
            except KeyError:
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            self._bytes(image, "image/jpeg", cache="public, max-age=3600")
        elif parsed.path == "/media/video":
            self._video()
        elif parsed.path == "/media/image":
            self._image()
        else:
            self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/api/analysis/start":
            self._start_analysis()
            return
        if parsed.path == "/api/analysis/playback":
            self._set_analysis_playback()
            return
        if parsed.path == "/api/media/browse":
            self._browse_media("video")
            return
        if parsed.path == "/api/media/browse-image":
            self._browse_media("image")
            return
        if parsed.path == "/api/media/select":
            self._select_media()
            return
        prefix = "/api/fragments/"
        if not parsed.path.startswith(prefix):
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(size))
            updated = self.state.store.update(
                unquote(parsed.path[len(prefix):]),
                user_text=str(payload.get("user_text", "")),
                favorite=bool(payload.get("favorite", False)),
            )
        except (ValueError, json.JSONDecodeError):
            self.send_error(HTTPStatus.BAD_REQUEST)
            return
        if updated is None:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        self._json(updated)

    def _start_analysis(self) -> None:
        if self.headers.get("X-AI-NAS-Action") != "start-analysis":
            self.send_error(HTTPStatus.FORBIDDEN)
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(size))
            mode = str(payload.get("mode", ""))
            source = self.state.begin_analysis(mode)
        except (ValueError, json.JSONDecodeError):
            self.send_error(HTTPStatus.BAD_REQUEST)
            return
        if source is None:
            self.send_error(HTTPStatus.CONFLICT, "analysis already running")
            return
        is_image = self.state.media_kind == "image"
        thread = threading.Thread(
            target=run_image_analysis if is_image else run_pc_analysis,
            args=(self.state, source) if is_image else (self.state, source, mode),
            name="pc-analysis-image" if is_image else f"pc-analysis-{mode}",
            daemon=True,
        )
        with self.state.lock:
            self.state.analysis_thread = thread
        thread.start()
        self._json(self.state.analysis_snapshot())

    def _set_analysis_playback(self) -> None:
        if self.headers.get("X-AI-NAS-Action") != "control-analysis-playback":
            self.send_error(HTTPStatus.FORBIDDEN)
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(size))
            paused = payload.get("paused")
            if not isinstance(paused, bool):
                raise ValueError("paused must be a boolean")
            self.state.set_analysis_paused(paused)
        except (ValueError, json.JSONDecodeError):
            self.send_error(HTTPStatus.BAD_REQUEST)
            return
        except RuntimeError as exc:
            self.send_error(HTTPStatus.CONFLICT, str(exc))
            return
        self._json(self.state.analysis_snapshot())

    def _browse_media(self, media_kind: str) -> None:
        expected_action = "browse-local-image" if media_kind == "image" else "browse-local-video"
        if self.headers.get("X-AI-NAS-Action") != expected_action:
            self.send_error(HTTPStatus.FORBIDDEN)
            return
        try:
            with self.state.browse_lock:
                initial_directory = self.state.source.parent if self.state.source is not None else ROOT
                source = (
                    browse_local_image(initial_directory)
                    if media_kind == "image"
                    else browse_local_video(initial_directory)
                )
            if source is None:
                self.send_response(HTTPStatus.NO_CONTENT)
                self.end_headers()
                return
            self.state.select_source(source, media_kind)
        except (OSError, RuntimeError, ValueError) as exc:
            self.send_error(HTTPStatus.BAD_REQUEST, str(exc))
            return
        self._json({
            "source_name": source.name,
            "media_kind": media_kind,
            "media_revision": self.state.media_revision,
            "analysis_state": "not_analyzed",
            "media_url": "/media/image" if media_kind == "image" else "/media/video",
        })

    def _select_media(self) -> None:
        temporary: Path | None = None
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if size <= 0:
                raise ValueError("empty upload")
            filename = self.headers.get("X-File-Name", "")
            target = imported_video_path(self.state.import_dir, filename)
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.part")
            remaining = size
            with temporary.open("xb") as handle:
                while remaining:
                    chunk = self.rfile.read(min(1024 * 1024, remaining))
                    if not chunk:
                        raise ValueError("incomplete upload")
                    handle.write(chunk)
                    remaining -= len(chunk)
            temporary.replace(target)
            temporary = None
            self.state.select_source(target)
        except (OSError, RuntimeError, ValueError) as exc:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
            self.send_error(HTTPStatus.BAD_REQUEST, str(exc))
            return
        self._json({
            "source_name": target.name,
            "size_bytes": target.stat().st_size,
            "media_revision": self.state.media_revision,
            "analysis_state": "not_analyzed",
            "video_url": "/media/video",
        })

    def _video(self) -> None:
        with self.state.lock:
            source = self.state.source
            media_kind = self.state.media_kind
        if media_kind != "video" or source is None or not source.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        size = source.stat().st_size
        start, end = 0, size - 1
        status = HTTPStatus.OK
        range_header = self.headers.get("Range")
        if range_header and range_header.startswith("bytes="):
            first, _, last = range_header[6:].partition("-")
            start = int(first or 0)
            end = min(int(last) if last else size - 1, size - 1)
            status = HTTPStatus.PARTIAL_CONTENT
        if start < 0 or end < start or start >= size:
            self.send_error(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
            return
        length = end - start + 1
        self.send_response(status)
        self.send_header("Content-Type", mimetypes.guess_type(source.name)[0] or "video/mp4")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        if status == HTTPStatus.PARTIAL_CONTENT:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        with source.open("rb") as handle:
            handle.seek(start)
            remaining = length
            while remaining:
                chunk = handle.read(min(256 * 1024, remaining))
                if not chunk:
                    break
                try:
                    self.wfile.write(chunk)
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    # Browsers cancel an old byte-range request after seeking.
                    break
                remaining -= len(chunk)

    def _image(self) -> None:
        with self.state.lock:
            source = self.state.source
            media_kind = self.state.media_kind
        if media_kind != "image" or source is None or not source.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        self._bytes(
            source.read_bytes(),
            mimetypes.guess_type(source.name)[0] or "image/jpeg",
            cache="no-cache",
        )

    def _json(self, value: object) -> None:
        self._bytes(json.dumps(value, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def _bytes(self, value: bytes, content_type: str, cache: str = "no-store") -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(value)))
        self.send_header("Cache-Control", cache)
        self.end_headers()
        self.wfile.write(value)

    def log_message(self, format: str, *args: object) -> None:
        return


def create_server(host: str, port: int, state: AppState) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((host, port), AppHandler)
    server.app_state = state  # type: ignore[attr-defined]
    return server


def main() -> None:
    parser = argparse.ArgumentParser(description="AI NAS Manager PC prototype")
    parser.add_argument("--result", type=Path)
    parser.add_argument("--database", type=Path, default=DEFAULT_DB)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8788)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument(
        "--include-unconfirmed",
        action="store_true",
        help="Persist review/candidate rows too; the default stores confirmed rows only",
    )
    args = parser.parse_args()

    store = FragmentStore(args.database)
    source: Path | None = None
    thumbnails: dict[str, bytes] = {}
    boundaries_ms: list[int] = []
    boundary_method = "not analyzed"
    scene_summaries: list[dict] = []
    shadow_boundaries: list[dict] = []
    boundary_markers = {key: [] for key in ("current", "adaptive", "tuned", "ground_truth")}
    media_kind = "none"
    if args.result is not None:
        payload = json.loads(args.result.read_text(encoding="utf-8"))
        candidate = Path(payload["source"])
        if candidate.is_file():
            source = candidate
            records = fragment_records(payload)
            records_to_store = records if args.include_unconfirmed else confirmed_records(records)
            store.import_records(records_to_store)
            boundaries_ms, boundary_method = scene_boundaries(payload)
            scene_summaries = list(payload.get("scene_summaries", []))
            shadow_boundaries = list(
                payload.get("scene_segmentation", {})
                .get("shadow_algorithms", {})
                .get("adaptive_memory_v1", {})
                .get("boundaries", [])
            )
            boundary_markers = scene_boundary_markers(payload)
            media_kind = media_kind_for_path(source)
            if media_kind == "video":
                thumbnails = build_thumbnails(source, records_to_store)
        else:
            print(f"Initial media is unavailable; starting with an empty library: {candidate}")
    state = AppState(
        store,
        source,
        DEFAULT_UI.read_bytes(),
        thumbnails,
        boundaries_ms,
        boundary_method,
        scene_summaries,
        shadow_boundaries,
        boundary_markers,
        media_kind=media_kind,
    )
    server = create_server(args.host, args.port, state)
    url = f"http://{args.host}:{args.port}"
    print(f"AI NAS Manager PC: {url}")
    print("終了するには Ctrl+C を押してください。")
    if not args.no_browser:
        threading.Timer(0.4, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
