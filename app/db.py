"""SQLite storage. One table, WAL mode, short transactions.

A wedding is a burst-y workload: nothing for ten minutes, then forty people
upload at once during the toasts. WAL lets readers (gallery, slideshow) keep
working while writers land new rows.
"""
from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Iterable

from .config import settings

_local = threading.local()

SCHEMA = """
CREATE TABLE IF NOT EXISTS media (
    seq           INTEGER PRIMARY KEY AUTOINCREMENT,
    id            TEXT NOT NULL UNIQUE,
    stored_name   TEXT NOT NULL,
    original_name TEXT,
    kind          TEXT NOT NULL,              -- 'image' | 'video'
    mime          TEXT,
    bytes         INTEGER NOT NULL DEFAULT 0,
    width         INTEGER,
    height        INTEGER,
    duration      REAL,
    guest_name    TEXT,
    message       TEXT,
    table_id      TEXT,
    uploaded_at   REAL NOT NULL,
    taken_at      REAL,
    status        TEXT NOT NULL DEFAULT 'processing',   -- processing|ready|failed
    approved      INTEGER NOT NULL DEFAULT 1,
    hidden        INTEGER NOT NULL DEFAULT 0,
    featured      INTEGER NOT NULL DEFAULT 0,
    thumb_name    TEXT,
    display_name  TEXT,
    uploader_ip   TEXT,
    error         TEXT
);
CREATE INDEX IF NOT EXISTS idx_media_uploaded ON media(uploaded_at DESC);
CREATE INDEX IF NOT EXISTS idx_media_visible  ON media(hidden, approved, status);
CREATE INDEX IF NOT EXISTS idx_media_table    ON media(table_id);

CREATE TABLE IF NOT EXISTS guestbook (
    seq         INTEGER PRIMARY KEY AUTOINCREMENT,
    id          TEXT NOT NULL UNIQUE,
    guest_name  TEXT,
    message     TEXT NOT NULL,
    table_id    TEXT,
    created_at  REAL NOT NULL,
    approved    INTEGER NOT NULL DEFAULT 1,
    hidden      INTEGER NOT NULL DEFAULT 0,
    uploader_ip TEXT
);
"""


def connect() -> sqlite3.Connection:
    """One connection per thread; FastAPI runs sync handlers in a threadpool."""
    conn = getattr(_local, "conn", None)
    if conn is None:
        settings.ensure_dirs()
        conn = sqlite3.connect(settings.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=30000")
        conn.execute("PRAGMA foreign_keys=ON")
        _local.conn = conn
    return conn


def init_db() -> None:
    conn = connect()
    conn.executescript(SCHEMA)
    conn.commit()


def query(sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
    return connect().execute(sql, tuple(params)).fetchall()


def query_one(sql: str, params: Iterable[Any] = ()) -> sqlite3.Row | None:
    return connect().execute(sql, tuple(params)).fetchone()


def execute(sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
    conn = connect()
    cur = conn.execute(sql, tuple(params))
    conn.commit()
    return cur


# --------------------------------------------------------------------------
# Visibility helpers
# --------------------------------------------------------------------------

def visible_clause(context: str) -> str:
    """SQL fragment limiting rows to what a given audience may see.

    context is 'gallery' or 'slideshow'. Admin never uses this.
    """
    base = "status = 'ready' AND hidden = 0"
    mode = settings.moderation
    if mode == "all":
        return base + " AND approved = 1"
    if mode == "slideshow" and context == "slideshow":
        return base + " AND approved = 1"
    return base


def row_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["uploaded_ago"] = max(0.0, time.time() - (d.get("uploaded_at") or 0))
    return d


def counts() -> dict:
    row = query_one(
        """
        SELECT
          COUNT(*)                                              AS total,
          SUM(kind = 'image')                                   AS images,
          SUM(kind = 'video')                                   AS videos,
          SUM(status = 'processing')                            AS processing,
          SUM(status = 'failed')                                AS failed,
          SUM(hidden = 1)                                       AS hidden,
          SUM(approved = 0 AND hidden = 0 AND status='ready')    AS pending,
          COALESCE(SUM(bytes), 0)                               AS bytes,
          COUNT(DISTINCT NULLIF(TRIM(COALESCE(guest_name,'')),'')) AS contributors
        FROM media
        """
    )
    out = {k: (row[k] or 0) for k in row.keys()} if row else {}
    gb = query_one("SELECT COUNT(*) AS n FROM guestbook WHERE hidden = 0")
    out["notes"] = gb["n"] if gb else 0
    return out
