"""
SQLite-backed persistence with a PostgreSQL-compatible schema.

Uses parameterized SQL and standard types so the same DDL can run on PostgreSQL
by swapping the connection factory.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Generator, List, Optional, Tuple

from app.exceptions import DatabaseUnavailable

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = Path(os.getenv("STORE_INTEL_DB", "data/store_intelligence.db"))

SCHEMA_STATEMENTS: Tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS events (
        event_id TEXT PRIMARY KEY,
        store_id TEXT NOT NULL,
        camera_id TEXT NOT NULL,
        visitor_id TEXT NOT NULL,
        event_type TEXT NOT NULL,
        timestamp TEXT NOT NULL,
        zone_id TEXT,
        dwell_ms INTEGER NOT NULL DEFAULT 0,
        is_staff INTEGER NOT NULL DEFAULT 0,
        confidence REAL NOT NULL DEFAULT 0.0,
        metadata_json TEXT NOT NULL,
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS visitor_sessions (
        session_id TEXT PRIMARY KEY,
        store_id TEXT NOT NULL,
        visitor_id TEXT NOT NULL,
        session_seq INTEGER NOT NULL DEFAULT 0,
        started_at TEXT NOT NULL,
        ended_at TEXT,
        is_staff INTEGER NOT NULL DEFAULT 0,
        has_zone_visit INTEGER NOT NULL DEFAULT 0,
        has_billing_queue INTEGER NOT NULL DEFAULT 0,
        has_purchase INTEGER NOT NULL DEFAULT 0,
        is_abandoned INTEGER NOT NULL DEFAULT 0
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS store_metrics_cache (
        store_id TEXT NOT NULL,
        metric_key TEXT NOT NULL,
        metric_value REAL NOT NULL,
        computed_at TEXT NOT NULL,
        PRIMARY KEY (store_id, metric_key)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_events_store_ts ON events (store_id, timestamp)",
    "CREATE INDEX IF NOT EXISTS idx_events_visitor ON events (store_id, visitor_id)",
    "CREATE INDEX IF NOT EXISTS idx_events_type ON events (store_id, event_type)",
    "CREATE INDEX IF NOT EXISTS idx_sessions_store ON visitor_sessions (store_id, started_at)",
    "CREATE INDEX IF NOT EXISTS idx_sessions_visitor ON visitor_sessions (store_id, visitor_id)",
)


def utc_now_iso() -> str:
    """Return current UTC time as ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


@dataclass
class EventRow:
    """Row representation of a persisted event."""

    event_id: str
    store_id: str
    camera_id: str
    visitor_id: str
    event_type: str
    timestamp: str
    zone_id: Optional[str]
    dwell_ms: int
    is_staff: bool
    confidence: float
    metadata_json: str

    @property
    def metadata(self) -> dict[str, Any]:
        return json.loads(self.metadata_json)


@dataclass
class SessionRow:
    """Row representation of a visitor session."""

    session_id: str
    store_id: str
    visitor_id: str
    session_seq: int
    started_at: str
    ended_at: Optional[str]
    is_staff: bool
    has_zone_visit: bool
    has_billing_queue: bool
    has_purchase: bool
    is_abandoned: bool


class Database:
    """
    Thin database access layer.

    Designed for dependency injection and straightforward unit testing with
    an in-memory SQLite database.
    """

    def __init__(self, db_path: Path | str = DEFAULT_DB_PATH) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        try:
            conn = sqlite3.connect(self.db_path, check_same_thread=False)
        except sqlite3.Error as exc:
            raise DatabaseUnavailable("Unable to open database connection") from exc
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    @contextmanager
    def connection(self) -> Generator[sqlite3.Connection, None, None]:
        """Yield a connection with automatic commit/rollback."""
        try:
            conn = self._connect()
        except DatabaseUnavailable:
            raise
        except sqlite3.Error as exc:
            raise DatabaseUnavailable("Unable to open database connection") from exc

        try:
            yield conn
            conn.commit()
        except DatabaseUnavailable:
            conn.rollback()
            raise
        except sqlite3.OperationalError as exc:
            conn.rollback()
            raise DatabaseUnavailable("Database operation failed") from exc
        except sqlite3.Error as exc:
            conn.rollback()
            raise DatabaseUnavailable("Database operation failed") from exc
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self.connection() as conn:
            for statement in SCHEMA_STATEMENTS:
                conn.execute(statement)
        logger.info("database initialized path=%s", self.db_path)

    def event_exists(self, event_id: str) -> bool:
        """Return True if event_id is already stored."""
        with self.connection() as conn:
            row = conn.execute(
                "SELECT 1 FROM events WHERE event_id = ? LIMIT 1",
                (event_id,),
            ).fetchone()
        return row is not None

    def insert_event(self, conn: sqlite3.Connection, event: EventRow) -> bool:
        """
        Insert a single event within an existing transaction.

        Returns True when a new row was created. Returns False when event_id
        already exists (idempotent no-op).
        """
        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO events (
                event_id, store_id, camera_id, visitor_id, event_type,
                timestamp, zone_id, dwell_ms, is_staff, confidence,
                metadata_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.event_id,
                event.store_id,
                event.camera_id,
                event.visitor_id,
                event.event_type,
                event.timestamp,
                event.zone_id,
                event.dwell_ms,
                int(event.is_staff),
                event.confidence,
                event.metadata_json,
                utc_now_iso(),
            ),
        )
        return cursor.rowcount > 0

    def fetch_events_for_store(
        self,
        store_id: str,
        *,
        exclude_staff: bool = True,
        since: Optional[str] = None,
        until: Optional[str] = None,
    ) -> List[EventRow]:
        """Load events for a store ordered by timestamp."""
        clauses = ["store_id = ?"]
        params: List[Any] = [store_id]
        if exclude_staff:
            clauses.append("is_staff = 0")
        if since:
            clauses.append("timestamp >= ?")
            params.append(since)
        if until:
            clauses.append("timestamp <= ?")
            params.append(until)

        query = f"""
            SELECT event_id, store_id, camera_id, visitor_id, event_type,
                   timestamp, zone_id, dwell_ms, is_staff, confidence, metadata_json
            FROM events
            WHERE {' AND '.join(clauses)}
            ORDER BY timestamp ASC, event_id ASC
        """
        with self.connection() as conn:
            rows = conn.execute(query, params).fetchall()
        return [_row_to_event(row) for row in rows]

    def fetch_sessions_for_store(
        self,
        store_id: str,
        *,
        exclude_staff: bool = True,
        since: Optional[str] = None,
    ) -> List[SessionRow]:
        """Load visitor sessions for a store."""
        clauses = ["store_id = ?"]
        params: List[Any] = [store_id]
        if exclude_staff:
            clauses.append("is_staff = 0")
        if since:
            clauses.append("started_at >= ?")
            params.append(since)

        query = f"""
            SELECT session_id, store_id, visitor_id, session_seq, started_at,
                   ended_at, is_staff, has_zone_visit, has_billing_queue,
                   has_purchase, is_abandoned
            FROM visitor_sessions
            WHERE {' AND '.join(clauses)}
            ORDER BY started_at ASC
        """
        with self.connection() as conn:
            rows = conn.execute(query, params).fetchall()
        return [_row_to_session(row) for row in rows]

    def get_open_session(
        self,
        conn: sqlite3.Connection,
        store_id: str,
        visitor_id: str,
    ) -> Optional[SessionRow]:
        """Return the active (unclosed) session for a visitor, if any."""
        row = conn.execute(
            """
            SELECT session_id, store_id, visitor_id, session_seq, started_at,
                   ended_at, is_staff, has_zone_visit, has_billing_queue,
                   has_purchase, is_abandoned
            FROM visitor_sessions
            WHERE store_id = ? AND visitor_id = ? AND ended_at IS NULL
            ORDER BY started_at DESC
            LIMIT 1
            """,
            (store_id, visitor_id),
        ).fetchone()
        return _row_to_session(row) if row else None

    def insert_session(self, conn: sqlite3.Connection, session: SessionRow) -> None:
        """Insert a new visitor session."""
        conn.execute(
            """
            INSERT INTO visitor_sessions (
                session_id, store_id, visitor_id, session_seq, started_at,
                ended_at, is_staff, has_zone_visit, has_billing_queue,
                has_purchase, is_abandoned
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session.session_id,
                session.store_id,
                session.visitor_id,
                session.session_seq,
                session.started_at,
                session.ended_at,
                int(session.is_staff),
                int(session.has_zone_visit),
                int(session.has_billing_queue),
                int(session.has_purchase),
                int(session.is_abandoned),
            ),
        )

    def update_session(self, conn: sqlite3.Connection, session: SessionRow) -> None:
        """Update mutable session fields."""
        conn.execute(
            """
            UPDATE visitor_sessions
            SET ended_at = ?, has_zone_visit = ?, has_billing_queue = ?,
                has_purchase = ?, is_abandoned = ?
            WHERE session_id = ?
            """,
            (
                session.ended_at,
                int(session.has_zone_visit),
                int(session.has_billing_queue),
                int(session.has_purchase),
                int(session.is_abandoned),
                session.session_id,
            ),
        )

    def upsert_metric_cache(
        self,
        store_id: str,
        metric_key: str,
        metric_value: float,
    ) -> None:
        """Persist a cached metric snapshot."""
        with self.connection() as conn:
            conn.execute(
                """
                INSERT INTO store_metrics_cache (store_id, metric_key, metric_value, computed_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(store_id, metric_key) DO UPDATE SET
                    metric_value = excluded.metric_value,
                    computed_at = excluded.computed_at
                """,
                (store_id, metric_key, metric_value, utc_now_iso()),
            )

    def get_metric_cache(
        self,
        store_id: str,
        metric_key: str,
    ) -> Optional[float]:
        """Read a cached metric value."""
        with self.connection() as conn:
            row = conn.execute(
                """
                SELECT metric_value FROM store_metrics_cache
                WHERE store_id = ? AND metric_key = ?
                """,
                (store_id, metric_key),
            ).fetchone()
        return float(row["metric_value"]) if row else None

    def list_store_ids(self) -> List[str]:
        """Return distinct store IDs seen in events."""
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT DISTINCT store_id FROM events ORDER BY store_id"
            ).fetchall()
        return [row["store_id"] for row in rows]

    def last_event_timestamp(self) -> Optional[str]:
        """Return the latest event timestamp across all stores."""
        with self.connection() as conn:
            row = conn.execute(
                "SELECT MAX(timestamp) AS latest FROM events"
            ).fetchone()
        return row["latest"] if row and row["latest"] else None


def _row_to_event(row: sqlite3.Row) -> EventRow:
    return EventRow(
        event_id=row["event_id"],
        store_id=row["store_id"],
        camera_id=row["camera_id"],
        visitor_id=row["visitor_id"],
        event_type=row["event_type"],
        timestamp=row["timestamp"],
        zone_id=row["zone_id"],
        dwell_ms=int(row["dwell_ms"]),
        is_staff=bool(row["is_staff"]),
        confidence=float(row["confidence"]),
        metadata_json=row["metadata_json"],
    )


def _row_to_session(row: sqlite3.Row) -> SessionRow:
    return SessionRow(
        session_id=row["session_id"],
        store_id=row["store_id"],
        visitor_id=row["visitor_id"],
        session_seq=int(row["session_seq"]),
        started_at=row["started_at"],
        ended_at=row["ended_at"],
        is_staff=bool(row["is_staff"]),
        has_zone_visit=bool(row["has_zone_visit"]),
        has_billing_queue=bool(row["has_billing_queue"]),
        has_purchase=bool(row["has_purchase"]),
        is_abandoned=bool(row["is_abandoned"]),
    )
