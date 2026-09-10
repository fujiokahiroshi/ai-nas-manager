"""Local PC prototype for browsing video fragments and Gemma text."""

from __future__ import annotations

import argparse
import json
import mimetypes
import sqlite3
import threading
import webbrowser
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from fragment_admission import confirmed_records, evaluate_admission


ROOT = Path(__file__).resolve().parent
DEFAULT_RESULT = ROOT / "docs" / "live-gemma-pedestrian-2026-09-10.json"
DEFAULT_UI = ROOT / "pc-app" / "index.html"
DEFAULT_DB = ROOT / "pc_app.sqlite3"


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
    source: Path
    ui: bytes
    thumbnails: dict[str, bytes]
    boundaries_ms: list[int]
    boundary_method: str
    scene_summaries: list[dict]


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


class AppHandler(BaseHTTPRequestHandler):
    server_version = "AINASPC/0.1"

    @property
    def state(self) -> AppState:
        return self.server.app_state  # type: ignore[attr-defined]

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self._bytes(self.state.ui, "text/html; charset=utf-8")
        elif parsed.path == "/api/fragments":
            values = parse_qs(parsed.query)
            query = values.get("q", [""])[0]
            favorites = values.get("favorites", ["0"])[0] == "1"
            self._json({
                "source_name": self.state.source.name,
                "fragments": self.state.store.list(
                    query,
                    favorites,
                    str(self.state.source.resolve()),
                ),
            })
        elif parsed.path == "/api/scenes":
            values = parse_qs(parsed.query)
            query = values.get("q", [""])[0].casefold()
            favorites = values.get("favorites", ["0"])[0] == "1"
            scenes = scene_records(
                self.state.store.list(source_path=str(self.state.source.resolve())),
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
            self._json({"source_name": self.state.source.name, "scenes": scenes})
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
        else:
            self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
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

    def _video(self) -> None:
        source = self.state.source
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
    parser.add_argument("--result", type=Path, default=DEFAULT_RESULT)
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

    payload = json.loads(args.result.read_text(encoding="utf-8"))
    records = fragment_records(payload)
    records_to_store = records if args.include_unconfirmed else confirmed_records(records)
    source = Path(payload["source"])
    if not source.is_file():
        raise FileNotFoundError(source)
    store = FragmentStore(args.database)
    store.import_records(records_to_store)
    boundaries_ms, boundary_method = scene_boundaries(payload)
    state = AppState(
        store,
        source,
        DEFAULT_UI.read_bytes(),
        build_thumbnails(source, records_to_store),
        boundaries_ms,
        boundary_method,
        list(payload.get("scene_summaries", [])),
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
