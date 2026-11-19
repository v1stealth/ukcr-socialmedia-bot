import asyncio
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from core.console import get_logger

log = get_logger("database")

SCHEMA = """
CREATE TABLE IF NOT EXISTS tickets (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    channel_id   INTEGER NOT NULL UNIQUE,
    guild_id     INTEGER NOT NULL,
    opener_id    INTEGER NOT NULL,
    category     TEXT    NOT NULL,
    status       TEXT    NOT NULL DEFAULT 'open',
    created_at   TEXT    NOT NULL,
    closed_at    TEXT,
    closed_by    INTEGER
);

CREATE TABLE IF NOT EXISTS video_submissions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id      INTEGER NOT NULL,
    user_id       INTEGER NOT NULL,
    video_url     TEXT    NOT NULL,
    caption       TEXT    NOT NULL,
    description   TEXT    NOT NULL,
    hashtags      TEXT    NOT NULL,
    submitted_at  TEXT    NOT NULL,
    status        TEXT    NOT NULL DEFAULT 'pending',
    reviewer_id   INTEGER,
    reviewed_at   TEXT,
    channel_id    INTEGER,
    message_id    INTEGER
);

CREATE TABLE IF NOT EXISTS member_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    member_id   INTEGER NOT NULL,
    guild_id    INTEGER NOT NULL,
    event       TEXT    NOT NULL,
    event_date  TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS log_channels (
    guild_id    INTEGER NOT NULL,
    log_key     TEXT    NOT NULL,
    channel_id  INTEGER NOT NULL,
    PRIMARY KEY (guild_id, log_key)
);

CREATE TABLE IF NOT EXISTS bot_settings (
    guild_id    INTEGER NOT NULL,
    key         TEXT    NOT NULL,
    value       INTEGER NOT NULL,
    PRIMARY KEY (guild_id, key)
);

CREATE TABLE IF NOT EXISTS content_schedule (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id    INTEGER NOT NULL,
    title       TEXT    NOT NULL,
    platform    TEXT    NOT NULL,
    owner_id    INTEGER,
    due_at      TEXT    NOT NULL,
    notes       TEXT,
    status      TEXT    NOT NULL DEFAULT 'scheduled',
    reminded    INTEGER NOT NULL DEFAULT 0,
    created_by  INTEGER NOT NULL,
    created_at  TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS content_ideas (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id     INTEGER NOT NULL,
    user_id      INTEGER NOT NULL,
    idea         TEXT    NOT NULL,
    status       TEXT    NOT NULL DEFAULT 'pending',
    reviewer_id  INTEGER,
    channel_id   INTEGER,
    message_id   INTEGER,
    created_at   TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS hashtag_packs (
    guild_id    INTEGER NOT NULL,
    name        TEXT    NOT NULL,
    tags        TEXT    NOT NULL,
    PRIMARY KEY (guild_id, name)
);
"""

MIGRATIONS = {
    "video_submissions": (
        ("reject_reason", "TEXT"),
        ("posted_url", "TEXT"),
        ("posted_at", "TEXT"),
        ("last_reminded_at", "TEXT"),
        ("resubmission_of", "INTEGER"),
    ),
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class Database:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._conn: Optional[sqlite3.Connection] = None
        self._lock = asyncio.Lock()

    async def connect(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await asyncio.to_thread(self._open)
        await asyncio.to_thread(self._migrate)
        log.info("Database ready at %s", self.path)

    def _open(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def _migrate(self) -> None:
        assert self._conn is not None
        self._conn.executescript(SCHEMA)
        for table, columns in MIGRATIONS.items():
            existing = {row[1] for row in self._conn.execute(f"PRAGMA table_info({table})")}
            for column, declaration in columns:
                if column not in existing:
                    self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")
                    log.info("Migrated %s: added column %s", table, column)
        self._conn.commit()

    async def close(self) -> None:
        if self._conn is not None:
            conn, self._conn = self._conn, None
            await asyncio.to_thread(conn.close)
            log.info("Database closed")

    def _require(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("Database is not connected")
        return self._conn

    async def run(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        def work() -> sqlite3.Cursor:
            conn = self._require()
            try:
                cur = conn.execute(sql, params)
                conn.commit()
                return cur
            except sqlite3.Error:
                conn.rollback()
                raise

        async with self._lock:
            return await asyncio.to_thread(work)

    async def one(self, sql: str, params: tuple = ()) -> Optional[sqlite3.Row]:
        def work() -> Optional[sqlite3.Row]:
            return self._require().execute(sql, params).fetchone()

        async with self._lock:
            return await asyncio.to_thread(work)

    async def all(self, sql: str, params: tuple = ()) -> list:
        def work() -> list:
            return self._require().execute(sql, params).fetchall()

        async with self._lock:
            return await asyncio.to_thread(work)

    async def scalar(self, sql: str, params: tuple = (), default: Any = 0) -> Any:
        row = await self.one(sql, params)
        return row[0] if row is not None and row[0] is not None else default

    async def setting_get(self, guild_id: int, key: str) -> Optional[int]:
        row = await self.one("SELECT value FROM bot_settings WHERE guild_id=? AND key=?", (guild_id, key))
        return row["value"] if row else None

    async def setting_set(self, guild_id: int, key: str, value: int) -> None:
        await self.run("INSERT OR REPLACE INTO bot_settings (guild_id, key, value) VALUES (?, ?, ?)", (guild_id, key, value))

    async def log_channel_id(self, guild_id: int, key: str) -> Optional[int]:
        row = await self.one("SELECT channel_id FROM log_channels WHERE guild_id=? AND log_key=?", (guild_id, key))
        return row["channel_id"] if row else None

    async def log_channel_ids(self, guild_id: int) -> set:
        rows = await self.all("SELECT channel_id FROM log_channels WHERE guild_id=?", (guild_id,))
        return {row[0] for row in rows}
