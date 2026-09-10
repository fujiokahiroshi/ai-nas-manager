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
            "completed_during_stream": bool(item.get("completed_during_stream", False)),
            "source_path": source,
        })
    return records


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
                    completed_during_stream INTEGER NOT NULL,
                    favorite INTEGER NOT NULL DEFAULT 0,
                    source_path TEXT NOT NULL
                )
            """)

    def import_records(self, records: list[dict]) -> None:
        with self._lock, self._connect() as db:
            db.executemany("""
                INSERT INTO fragments (
                    id, fragment_id, revision, timestamp_ms, observation,
                    action, change_text, objects_json, confidence,
                    completed_during_stream, source_path
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    timestamp_ms=excluded.timestamp_ms,
                    observation=excluded.observation,
                    action=excluded.action,
                    change_text=excluded.change_text,
                    objects_json=excluded.objects_json,
                    confidence=excluded.confidence,
                    completed_during_stream=excluded.completed_during_stream,
                    source_path=excluded.source_path
            """, [(
                item["id"], item["fragment_id"], item["revision"], item["timestamp_ms"],
                item["observation"], item["action"], item["change_text"],
                json.dumps(item["objects"], ensure_ascii=False), item["confidence"],
                int(item["completed_during_stream"]), item["source_path"],
            ) for item in records])

    def list(self, query: str = "", favorites_only: bool = False) -> list[dict]:
        conditions: list[str] = []
        parameters: list[object] = []
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
                "fragments": self.state.store.list(query, favorites),
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
                except (BrokenPipeError, ConnectionResetError):
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
    args = parser.parse_args()

    payload = json.loads(args.result.read_text(encoding="utf-8"))
    records = fragment_records(payload)
    source = Path(payload["source"])
    if not source.is_file():
        raise FileNotFoundError(source)
    store = FragmentStore(args.database)
    store.import_records(records)
    state = AppState(store, source, DEFAULT_UI.read_bytes(), build_thumbnails(source, records))
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
