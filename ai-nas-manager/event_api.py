"""HTTP event API used directly by Windows views.

The API is intentionally independent from MCP stdio.  Multiple ai-nas-manager
processes may exist; one process owns the fixed port while SQLite keeps events
available to every process and across restarts.
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

import event_queue

EVENT_API_HOST = os.environ.get("AI_NAS_EVENT_HOST", "0.0.0.0")
EVENT_API_PORT = int(os.environ.get("AI_NAS_EVENT_PORT", "39232"))
EVENT_API_TOKEN = os.environ.get("AI_NAS_EVENT_TOKEN")
INSTANCE_ID = uuid.uuid4().hex
MAX_BODY_BYTES = 1024 * 1024
LEADER_RETRY_SEC = 1.0

is_leader = False
_leadership_lock = threading.Lock()
_control_server: ThreadingHTTPServer | None = None
_control_thread: threading.Thread | None = None
_watchdog_started = False


class _Handler(BaseHTTPRequestHandler):
    def _cors_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header(
            "Access-Control-Allow-Headers",
            "Content-Type, Authorization, X-AI-NAS-Token",
        )
        self.send_header("Access-Control-Allow-Private-Network", "true")

    def _send_json(self, obj: Any, status: int = 200) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self._cors_headers()
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self) -> bool:
        if not EVENT_API_TOKEN:
            return True
        auth = self.headers.get("Authorization", "")
        supplied = self.headers.get("X-AI-NAS-Token", "")
        return auth == f"Bearer {EVENT_API_TOKEN}" or supplied == EVENT_API_TOKEN

    def _require_auth(self) -> bool:
        if self._authorized():
            return True
        self._send_json({"error": "unauthorized"}, 401)
        return False

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length < 0 or length > MAX_BODY_BYTES:
            raise ValueError("request body is too large")
        raw = self.rfile.read(length) if length else b"{}"
        value = json.loads(raw or b"{}")
        if not isinstance(value, dict):
            raise ValueError("JSON body must be an object")
        return value

    @staticmethod
    def _int_param(
        query: dict[str, list[str]],
        name: str,
        default: int,
    ) -> int:
        try:
            return int(query.get(name, [str(default)])[0])
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{name} must be an integer") from exc

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        self._cors_headers()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        if not self._require_auth():
            return
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        try:
            if parsed.path == "/health":
                self._send_json(
                    {
                        "ok": True,
                        "instance_id": INSTANCE_ID,
                        "latest_nas_event_id": event_queue.latest_event_id("nas"),
                        "latest_view_event_id": event_queue.latest_event_id("view"),
                    }
                )
                return
            if parsed.path == "/events":
                target = query.get("target", [""])[0]
                after_id = self._int_param(query, "after_id", 0)
                limit = self._int_param(query, "limit", 100)
                events = event_queue.list_events(target, after_id, limit)
                self._send_json(
                    {
                        "events": [event.as_dict() for event in events],
                        "last_event_id": events[-1].id if events else after_id,
                    }
                )
                return
            if parsed.path == "/cursor":
                consumer = query.get("consumer", [""])[0]
                self._send_json(
                    {
                        "consumer": consumer,
                        "last_event_id": event_queue.get_cursor(consumer),
                    }
                )
                return
            self._send_json({"error": "not found"}, 404)
        except (ValueError, json.JSONDecodeError) as exc:
            self._send_json({"error": str(exc)}, 400)

    def do_POST(self) -> None:  # noqa: N802
        if not self._require_auth():
            return
        parsed = urlparse(self.path)
        try:
            body = self._read_json()
            if parsed.path == "/events":
                event = event_queue.publish(
                    source=str(body.get("source", "")),
                    target=str(body.get("target", "")),
                    event_type=str(body.get("event_type", "")),
                    payload=body.get("payload", {}),
                    dedupe_key=body.get("dedupe_key"),
                )
                self._send_json({"event": event.as_dict()}, 201)
                return
            if parsed.path == "/acks":
                cursor = event_queue.acknowledge(
                    str(body.get("consumer", "")),
                    int(body.get("last_event_id", -1)),
                )
                self._send_json({"last_event_id": cursor})
                return
            self._send_json({"error": "not found"}, 404)
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            self._send_json({"error": str(exc)}, 400)

    def log_message(self, fmt: str, *args: Any) -> None:
        pass


class _ExclusiveThreadingHTTPServer(ThreadingHTTPServer):
    # Linuxでは終了した接続のTIME_WAIT中も後継プロセスが即座に同じポートを
    # 取得できるようSO_REUSEADDRが必要。稼働中listenerとの二重bindは
    # SO_REUSEPORTを使っていないため引き続きOSが拒否する。
    allow_reuse_address = True
    daemon_threads = True


def _try_become_leader() -> bool:
    global is_leader, _control_server, _control_thread
    with _leadership_lock:
        if is_leader and _control_thread is not None and _control_thread.is_alive():
            return True
        if _control_server is not None:
            try:
                _control_server.server_close()
            except OSError:
                pass
        is_leader = False
        _control_server = None
        _control_thread = None
        try:
            httpd = _ExclusiveThreadingHTTPServer(
                (EVENT_API_HOST, EVENT_API_PORT),
                _Handler,
            )
        except OSError:
            return False

        def _serve() -> None:
            global is_leader, _control_server, _control_thread
            try:
                httpd.serve_forever()
            finally:
                with _leadership_lock:
                    if _control_server is httpd:
                        is_leader = False
                        _control_server = None
                        _control_thread = None
                httpd.server_close()

        thread = threading.Thread(
            target=_serve,
            daemon=True,
            name="ai-nas-event-api",
        )
        _control_server = httpd
        _control_thread = thread
        is_leader = True
        thread.start()
        return True


def _watchdog_step() -> bool:
    if is_leader and _control_thread is not None and _control_thread.is_alive():
        return True
    return _try_become_leader()


def _watchdog() -> None:
    while True:
        _watchdog_step()
        time.sleep(LEADER_RETRY_SEC)


def start_event_api() -> bool:
    global _watchdog_started
    leader = _watchdog_step()
    with _leadership_lock:
        if not _watchdog_started:
            threading.Thread(
                target=_watchdog,
                daemon=True,
                name="ai-nas-event-api-watchdog",
            ).start()
            _watchdog_started = True
    return leader


if __name__ == "__main__":
    start_event_api()
    while True:
        time.sleep(3600)
