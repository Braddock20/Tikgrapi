"""Durable history for the video engine.

The engine keeps its own SQLite file so it stays independently executable —
no SQLAlchemy, no async, no server. The tables mirror the ones the service
already keeps for jobs, but they describe *creative* history: which content
was used, which composition produced it, which sound was paired with it.

The connection is opened per operation with a short timeout, so several
processes (the API and a CLI run) can share the file safely.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Iterable

SCHEMA = """
CREATE TABLE IF NOT EXISTS content_history (
    content_id        TEXT PRIMARY KEY,
    category          TEXT,
    publication_status TEXT DEFAULT 'unused',
    publication_time  REAL,
    video_id          TEXT,
    usage_count       INTEGER DEFAULT 0,
    last_used         REAL
);

CREATE TABLE IF NOT EXISTS composition_history (
    composition_id    TEXT PRIMARY KEY,
    visual_identity   TEXT,
    layout            TEXT,
    font_combination  TEXT,
    background_id     TEXT,
    decoration_combination TEXT,
    animation_combination TEXT,
    palette           TEXT,
    content_id        TEXT,
    generation_time   REAL,
    seed              INTEGER
);

CREATE TABLE IF NOT EXISTS sound_history (
    sound_id          TEXT PRIMARY KEY,
    usage_count       INTEGER DEFAULT 0,
    last_used         REAL,
    associated_content_category TEXT
);

CREATE TABLE IF NOT EXISTS render_history (
    render_id         TEXT PRIMARY KEY,
    job_id            TEXT,
    output_path       TEXT,
    duration          REAL,
    resolution        TEXT,
    render_time       REAL,
    status            TEXT,
    error_message     TEXT,
    content_id        TEXT,
    created_at        REAL
);

CREATE TABLE IF NOT EXISTS asset_usage (
    asset_id          TEXT PRIMARY KEY,
    usage_count       INTEGER DEFAULT 0,
    last_used         REAL
);

CREATE TABLE IF NOT EXISTS video_performance (
    video_id          TEXT PRIMARY KEY,
    content_id        TEXT,
    category          TEXT,
    visual_identity   TEXT,
    layout            TEXT,
    sound_id          TEXT,
    duration          REAL,
    views             INTEGER DEFAULT 0,
    likes             INTEGER DEFAULT 0,
    comments          INTEGER DEFAULT 0,
    shares            INTEGER DEFAULT 0,
    saves             INTEGER DEFAULT 0,
    watch_time        REAL,
    completion_rate   REAL,
    updated_at        REAL
);

CREATE TABLE IF NOT EXISTS render_jobs (
    job_id            TEXT PRIMARY KEY,
    status            TEXT,
    params            TEXT,
    result            TEXT,
    error             TEXT,
    attempts          INTEGER DEFAULT 0,
    created_at        REAL,
    updated_at        REAL
);

CREATE INDEX IF NOT EXISTS idx_comp_time ON composition_history(generation_time);
CREATE INDEX IF NOT EXISTS idx_render_status ON render_history(status);
"""


class HistoryStore:
    """Thin, dependency-free persistence layer."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init_schema()

    # -- plumbing --------------------------------------------------------
    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def _init_schema(self) -> None:
        with self.connect() as conn:
            conn.executescript(SCHEMA)

    def execute(self, sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
        with self._lock, self.connect() as conn:
            cur = conn.execute(sql, tuple(params))
            rows = cur.fetchall()
            conn.commit()
            return rows

    # -- content ---------------------------------------------------------
    def content_used(self) -> dict[str, int]:
        rows = self.execute("SELECT content_id, usage_count FROM content_history")
        return {r["content_id"]: r["usage_count"] for r in rows}

    def mark_content_used(self, content_id: str, category: str, video_id: str, published: bool) -> None:
        now = time.time()
        self.execute(
            """
            INSERT INTO content_history
                (content_id, category, publication_status, publication_time, video_id, usage_count, last_used)
            VALUES (?, ?, ?, ?, ?, 1, ?)
            ON CONFLICT(content_id) DO UPDATE SET
                usage_count = usage_count + 1,
                last_used = excluded.last_used,
                video_id = excluded.video_id,
                publication_status = excluded.publication_status,
                publication_time = excluded.publication_time
            """,
            (
                content_id,
                category,
                "published" if published else "rendered",
                now if published else None,
                video_id,
                now,
            ),
        )

    def release_content(self, content_id: str) -> None:
        """Give an item back after a failed render so it is not burned."""
        self.execute(
            "UPDATE content_history SET usage_count = MAX(0, usage_count - 1),"
            " publication_status = 'unused' WHERE content_id = ?",
            (content_id,),
        )

    # -- composition -----------------------------------------------------
    def record_composition(self, record: dict[str, Any]) -> str:
        composition_id = record.get("composition_id") or uuid.uuid4().hex
        self.execute(
            """
            INSERT OR REPLACE INTO composition_history
                (composition_id, visual_identity, layout, font_combination, background_id,
                 decoration_combination, animation_combination, palette, content_id,
                 generation_time, seed)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                composition_id,
                record.get("visual_identity"),
                record.get("layout"),
                record.get("font_combination"),
                record.get("background_id"),
                record.get("decoration_combination"),
                record.get("animation_combination"),
                record.get("palette"),
                record.get("content_id"),
                time.time(),
                record.get("seed"),
            ),
        )
        return composition_id

    def recent_compositions(self, limit: int = 24) -> list[dict[str, Any]]:
        rows = self.execute(
            "SELECT * FROM composition_history ORDER BY generation_time DESC LIMIT ?", (limit,)
        )
        return [dict(r) for r in rows]

    # -- sounds ----------------------------------------------------------
    def record_sound(self, sound_id: str, category: str) -> None:
        now = time.time()
        self.execute(
            """
            INSERT INTO sound_history (sound_id, usage_count, last_used, associated_content_category)
            VALUES (?, 1, ?, ?)
            ON CONFLICT(sound_id) DO UPDATE SET
                usage_count = usage_count + 1,
                last_used = excluded.last_used,
                associated_content_category = excluded.associated_content_category
            """,
            (sound_id, now, category),
        )

    def sound_usage(self) -> dict[str, int]:
        rows = self.execute("SELECT sound_id, usage_count FROM sound_history")
        return {r["sound_id"]: r["usage_count"] for r in rows}

    # -- assets ----------------------------------------------------------
    def record_asset(self, asset_id: str) -> None:
        now = time.time()
        self.execute(
            """
            INSERT INTO asset_usage (asset_id, usage_count, last_used) VALUES (?, 1, ?)
            ON CONFLICT(asset_id) DO UPDATE SET
                usage_count = usage_count + 1, last_used = excluded.last_used
            """,
            (asset_id, now),
        )

    def asset_usage(self) -> dict[str, int]:
        rows = self.execute("SELECT asset_id, usage_count FROM asset_usage")
        return {r["asset_id"]: r["usage_count"] for r in rows}

    # -- renders ---------------------------------------------------------
    def record_render(
        self,
        render_id: str,
        job_id: str,
        output_path: str,
        duration: float,
        resolution: str,
        render_time: float,
        status: str,
        error_message: str | None = None,
        content_id: str | None = None,
    ) -> None:
        self.execute(
            """
            INSERT OR REPLACE INTO render_history
                (render_id, job_id, output_path, duration, resolution, render_time,
                 status, error_message, content_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                render_id,
                job_id,
                output_path,
                duration,
                resolution,
                render_time,
                status,
                error_message,
                content_id,
                time.time(),
            ),
        )

    def render_stats(self) -> dict[str, Any]:
        rows = self.execute(
            "SELECT status, COUNT(*) n, AVG(render_time) avg_time FROM render_history GROUP BY status"
        )
        return {r["status"]: {"count": r["n"], "avg_render_time": r["avg_time"] or 0.0} for r in rows}

    def recent_renders(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = self.execute(
            "SELECT * FROM render_history ORDER BY created_at DESC LIMIT ?", (limit,)
        )
        return [dict(r) for r in rows]

    # -- performance -----------------------------------------------------
    def record_performance(self, record: dict[str, Any]) -> None:
        self.execute(
            """
            INSERT OR REPLACE INTO video_performance
                (video_id, content_id, category, visual_identity, layout, sound_id, duration,
                 views, likes, comments, shares, saves, watch_time, completion_rate, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.get("video_id"),
                record.get("content_id"),
                record.get("category"),
                record.get("visual_identity"),
                record.get("layout"),
                record.get("sound_id"),
                record.get("duration"),
                record.get("views", 0),
                record.get("likes", 0),
                record.get("comments", 0),
                record.get("shares", 0),
                record.get("saves", 0),
                record.get("watch_time"),
                record.get("completion_rate"),
                time.time(),
            ),
        )

    def performance_rows(self) -> list[dict[str, Any]]:
        rows = self.execute("SELECT * FROM video_performance")
        return [dict(r) for r in rows]

    # -- job queue -------------------------------------------------------
    def save_job(self, job_id: str, status: str, params: dict, result: Any = None, error: str | None = None) -> None:
        now = time.time()
        self.execute(
            """
            INSERT INTO render_jobs (job_id, status, params, result, error, attempts, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, 1, ?, ?)
            ON CONFLICT(job_id) DO UPDATE SET
                status = excluded.status,
                params = excluded.params,
                result = excluded.result,
                error = excluded.error,
                attempts = render_jobs.attempts + 1,
                updated_at = excluded.updated_at
            """,
            (job_id, status, json.dumps(params or {}), json.dumps(result) if result is not None else None, error, now, now),
        )

    def load_pending_jobs(self) -> list[dict[str, Any]]:
        rows = self.execute("SELECT * FROM render_jobs WHERE status IN ('queued','running')")
        return [dict(r) for r in rows]

    def update_job(self, job_id: str, status: str, result: Any = None, error: str | None = None) -> None:
        self.execute(
            "UPDATE render_jobs SET status = ?, result = ?, error = ?, updated_at = ? WHERE job_id = ?",
            (status, json.dumps(result) if result is not None else None, error, time.time(), job_id),
        )

    def job_rows(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = self.execute("SELECT * FROM render_jobs ORDER BY updated_at DESC LIMIT ?", (limit,))
        out = []
        for r in rows:
            item = dict(r)
            for key in ("params", "result"):
                try:
                    item[key] = json.loads(item[key]) if item[key] else None
                except (TypeError, json.JSONDecodeError):
                    pass
            out.append(item)
        return out
