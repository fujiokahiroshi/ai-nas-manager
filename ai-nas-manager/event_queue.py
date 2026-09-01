"""Persistent event queue shared by ai-nas-manager and Windows views."""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DB_PATH = Path(
    os.environ.get(
        "AI_NAS_EVENT_DB",
        str(Path(__file__).resolve().parent / "event_queue.sqlite3"),
    )
)
VALID_TARGETS = {"nas", "view"}
MAX_LIMIT = 500


@dataclass(frozen=True)
class Event:
    id: int
    created_at: str
    source: str
    target: str
    event_type: str
    payload: dict[str, Any]
    dedupe_key: str | None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            source TEXT NOT NULL,
            target TEXT NOT NULL,
            event_type TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            dedupe_key TEXT,
            UNIQUE(source, dedupe_key)
        );
        CREATE INDEX IF NOT EXISTS idx_events_target_id
            ON events(target, id);
        CREATE TABLE IF NOT EXISTS consumer_offsets (
            consumer TEXT PRIMARY KEY,
            last_event_id INTEGER NOT NULL,
            updated_at TEXT NOT NULL
        );
        """
    )
    return conn


def _validate_text(name: str, value: str, max_length: int = 200) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{name} must not be empty")
    if len(normalized) > max_length:
        raise ValueError(f"{name} is too long (max {max_length})")
    return normalized


def _event_from_row(row: sqlite3.Row) -> Event:
    payload = json.loads(row["payload_json"])
    if not isinstance(payload, dict):
        payload = {"value": payload}
    return Event(
        id=int(row["id"]),
        created_at=str(row["created_at"]),
        source=str(row["source"]),
        target=str(row["target"]),
        event_type=str(row["event_type"]),
        payload=payload,
        dedupe_key=row["dedupe_key"],
    )


def publish(
    source: str,
    target: str,
    event_type: str,
    payload: dict[str, Any] | None = None,
    dedupe_key: str | None = None,
) -> Event:
    source = _validate_text("source", source)
    target = _validate_text("target", target)
    event_type = _validate_text("event_type", event_type)
    if target not in VALID_TARGETS:
        raise ValueError(f"target must be one of {sorted(VALID_TARGETS)}")
    if payload is not None and not isinstance(payload, dict):
        raise ValueError("payload must be an object")
    if dedupe_key is not None:
        dedupe_key = _validate_text("dedupe_key", dedupe_key, 500)

    created_at = datetime.now(timezone.utc).isoformat()
    payload_json = json.dumps(payload or {}, ensure_ascii=False, separators=(",", ":"))
    with _connect() as conn:
        try:
            cursor = conn.execute(
                """
                INSERT INTO events
                    (created_at, source, target, event_type, payload_json, dedupe_key)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (created_at, source, target, event_type, payload_json, dedupe_key),
            )
            event_id = int(cursor.lastrowid)
        except sqlite3.IntegrityError:
            if dedupe_key is None:
                raise
            row = conn.execute(
                "SELECT * FROM events WHERE source = ? AND dedupe_key = ?",
                (source, dedupe_key),
            ).fetchone()
            if row is None:
                raise
            return _event_from_row(row)
        row = conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
        assert row is not None
        return _event_from_row(row)


def list_events(target: str, after_id: int = 0, limit: int = 100) -> list[Event]:
    target = _validate_text("target", target)
    if target not in VALID_TARGETS:
        raise ValueError(f"target must be one of {sorted(VALID_TARGETS)}")
    if after_id < 0:
        raise ValueError("after_id must be >= 0")
    limit = max(1, min(int(limit), MAX_LIMIT))
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM events
            WHERE target = ? AND id > ?
            ORDER BY id ASC
            LIMIT ?
            """,
            (target, after_id, limit),
        ).fetchall()
    return [_event_from_row(row) for row in rows]


def acknowledge(consumer: str, last_event_id: int) -> int:
    consumer = _validate_text("consumer", consumer)
    if last_event_id < 0:
        raise ValueError("last_event_id must be >= 0")
    now = datetime.now(timezone.utc).isoformat()
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO consumer_offsets(consumer, last_event_id, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(consumer) DO UPDATE SET
                last_event_id = MAX(last_event_id, excluded.last_event_id),
                updated_at = excluded.updated_at
            """,
            (consumer, last_event_id, now),
        )
        row = conn.execute(
            "SELECT last_event_id FROM consumer_offsets WHERE consumer = ?",
            (consumer,),
        ).fetchone()
    assert row is not None
    return int(row["last_event_id"])


def get_cursor(consumer: str) -> int:
    consumer = _validate_text("consumer", consumer)
    with _connect() as conn:
        row = conn.execute(
            "SELECT last_event_id FROM consumer_offsets WHERE consumer = ?",
            (consumer,),
        ).fetchone()
    return int(row["last_event_id"]) if row else 0


def latest_event_id(target: str) -> int:
    target = _validate_text("target", target)
    if target not in VALID_TARGETS:
        raise ValueError(f"target must be one of {sorted(VALID_TARGETS)}")
    with _connect() as conn:
        row = conn.execute(
            "SELECT COALESCE(MAX(id), 0) AS latest FROM events WHERE target = ?",
            (target,),
        ).fetchone()
    return int(row["latest"])
