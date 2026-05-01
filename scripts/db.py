#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 OpenTor Contributors
"""
OpenTor — db.py — SQLite Persistence Layer
============================================
Lightweight SQLite store for cache, engine reliability history, and crawl data.
No external dependencies beyond stdlib sqlite3.

This is pure mechanical storage — the LLM is the intelligence. This module
just remembers things across sessions.

Tables:
    cache           — fetch results and search results with TTL expiry
    engine_history  — rolling health checks for reliability scoring
    crawl_pages     — crawled .onion page content, entities, metadata
    crawl_links     — parent→child link graph from spider traversal

Environment:
    OPENTOR_DB_PATH — path to SQLite file (default: ~/.opentor/opentor.db)
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time

# ── config ─────────────────────────────────────────────────────────
_OPENTOR_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.getenv(
    "OPENTOR_DB_PATH",
    os.path.join(_OPENTOR_ROOT, "database", "opentor.db"),
)


class DB:
    """Thread-safe SQLite wrapper with TTL-aware cache and WAL mode.

    Usage:
        db = DB()
        db.cache_set("my-key", "fetch", result_dict)
        cached = db.cache_get("my-key", "fetch", ttl=600)
    """

    def __init__(self, path: str = DB_PATH) -> None:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self._path = path
        self._local = threading.local()
        self._lock = threading.Lock()
        self._init_schema()

    def _conn(self) -> sqlite3.Connection:
        if not getattr(self._local, "conn", None):
            self._local.conn = sqlite3.connect(self._path, check_same_thread=False)
            self._local.conn.row_factory = sqlite3.Row
            self._local.conn.execute("PRAGMA journal_mode=WAL;")
            self._local.conn.execute("PRAGMA synchronous=NORMAL;")
        return self._local.conn

    def _init_schema(self) -> None:
        with self._lock:
            c = self._conn()
            c.executescript("""
                CREATE TABLE IF NOT EXISTS cache (
                    key        TEXT NOT NULL,
                    cache_type TEXT NOT NULL,
                    ts         REAL NOT NULL,
                    data       TEXT NOT NULL,
                    PRIMARY KEY (key, cache_type)
                );
                CREATE TABLE IF NOT EXISTS engine_history (
                    engine     TEXT  NOT NULL,
                    ts         REAL  NOT NULL,
                    status     TEXT  NOT NULL,
                    latency_ms INTEGER,
                    error      TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_eh_engine
                    ON engine_history(engine, ts);
                CREATE TABLE IF NOT EXISTS crawl_pages (
                    url      TEXT PRIMARY KEY,
                    job_id   TEXT,
                    depth    INTEGER,
                    ts       REAL,
                    title    TEXT,
                    text     TEXT,
                    entities TEXT
                );
                CREATE TABLE IF NOT EXISTS crawl_links (
                    src TEXT NOT NULL,
                    dst TEXT NOT NULL,
                    PRIMARY KEY (src, dst)
                );
            """)
            c.commit()

    # ── cache ──────────────────────────────────────────────────────
    def cache_get(self, key: str, cache_type: str, ttl: int) -> dict | list | None:
        """Return cached data if fresh, else None."""
        if ttl <= 0:
            return None
        row = self._conn().execute(
            "SELECT ts, data FROM cache WHERE key=? AND cache_type=?",
            (key, cache_type),
        ).fetchone()
        if row and (time.time() - row["ts"]) < ttl:
            try:
                return json.loads(row["data"])
            except Exception:
                return None
        return None

    def cache_set(self, key: str, cache_type: str, data: dict | list) -> None:
        """Store data in cache with current timestamp."""
        with self._lock:
            self._conn().execute(
                "INSERT OR REPLACE INTO cache(key,cache_type,ts,data) VALUES(?,?,?,?)",
                (key, cache_type, time.time(), json.dumps(data, default=str)),
            )
            self._conn().commit()

    def cache_clear(self, cache_type: str | None = None) -> int:
        """Delete cached entries. Returns count removed."""
        with self._lock:
            if cache_type:
                n = self._conn().execute(
                    "SELECT COUNT(*) FROM cache WHERE cache_type=?", (cache_type,)
                ).fetchone()[0]
                self._conn().execute(
                    "DELETE FROM cache WHERE cache_type=?", (cache_type,)
                )
            else:
                n = self._conn().execute(
                    "SELECT COUNT(*) FROM cache"
                ).fetchone()[0]
                self._conn().execute("DELETE FROM cache")
            self._conn().commit()
            return n

    # ── engine health ──────────────────────────────────────────────
    def engine_history_add(
        self, engine: str, status: str, latency_ms: int | None, error: str | None
    ) -> None:
        """Record an engine health check result."""
        with self._lock:
            self._conn().execute(
                "INSERT INTO engine_history(engine,ts,status,latency_ms,error) "
                "VALUES(?,?,?,?,?)",
                (engine, time.time(), status, latency_ms, error),
            )
            # Keep last 20 checks per engine
            self._conn().execute(
                "DELETE FROM engine_history WHERE engine=? AND ts NOT IN ("
                "SELECT ts FROM engine_history WHERE engine=? ORDER BY ts DESC LIMIT 20"
                ")",
                (engine, engine),
            )
            self._conn().commit()

    def engine_history_get(self, engine: str, n: int = 5) -> list[dict]:
        """Return last N health checks for an engine."""
        rows = self._conn().execute(
            "SELECT ts,status,latency_ms,error FROM engine_history "
            "WHERE engine=? ORDER BY ts DESC LIMIT ?",
            (engine, n),
        ).fetchall()
        return [dict(r) for r in rows]

    def engine_reliability(self, engine: str, window: int = 20) -> float | None:
        """Reliability score with exponential time-decay (half-life 48h)."""
        rows = self.engine_history_get(engine, window)
        if not rows:
            return None
        import math
        _now = time.time()
        _hl = 48 * 3600
        _ln2 = math.log(2)
        w_up = 0.0
        w_total = 0.0
        for r in rows:
            age_s = max(0.0, _now - (r.get("ts") or _now))
            w = math.exp(-_ln2 * age_s / _hl)
            w_total += w
            if r["status"] == "up":
                w_up += w
        return (w_up + 1) / (w_total + 2)

    # ── crawl store ────────────────────────────────────────────────
    def crawl_save_page(
        self, url: str, job_id: str, depth: int,
        title: str, text: str, entities: dict,
    ) -> None:
        """Save a crawled page to the database."""
        with self._lock:
            self._conn().execute(
                "INSERT OR REPLACE INTO crawl_pages(url,job_id,depth,ts,title,text,entities) "
                "VALUES(?,?,?,?,?,?,?)",
                (url, job_id, depth, time.time(), title or "",
                 text[:10000], json.dumps(entities, default=str)),
            )
            self._conn().commit()

    def crawl_save_link(self, src: str, dst: str) -> None:
        """Record a link between two crawled pages."""
        with self._lock:
            self._conn().execute(
                "INSERT OR IGNORE INTO crawl_links(src,dst) VALUES(?,?)", (src, dst)
            )
            self._conn().commit()

    def crawl_export(self, job_id: str) -> dict:
        """Export all pages and links for a crawl job."""
        raw_pages = self._conn().execute(
            "SELECT url,depth,ts,title,text,entities "
            "FROM crawl_pages WHERE job_id=?",
            (job_id,),
        ).fetchall()
        pages = []
        for r in raw_pages:
            row = dict(r)
            try:
                row["entities"] = json.loads(row["entities"]) if row["entities"] else {}
            except Exception:
                row["entities"] = {}
            pages.append(row)
        links = [dict(r) for r in self._conn().execute(
            "SELECT src,dst FROM crawl_links WHERE src IN "
            "(SELECT url FROM crawl_pages WHERE job_id=?)", (job_id,)
        ).fetchall()]
        return {"job_id": job_id, "pages": pages, "links": links}


# ── module-level singleton ─────────────────────────────────────────
_instance: DB | None = None
_lock = threading.Lock()


def get_db() -> DB:
    global _instance
    if _instance is None:
        with _lock:
            if _instance is None:
                _instance = DB()
    return _instance
