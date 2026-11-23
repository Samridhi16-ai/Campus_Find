"""Database layer for CampusFind.

Uses PostgreSQL when DATABASE_URL is configured (Render deployment).
Falls back to SQLite for local development and the existing test suite.
"""
import os
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).with_name("campusfind.db")

SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS listings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    type TEXT NOT NULL CHECK (type IN ('lost', 'found')),
    category TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT NOT NULL,
    location TEXT NOT NULL,
    date TEXT NOT NULL,
    reporter TEXT NOT NULL,
    email TEXT NOT NULL,
    photo TEXT,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'returned')),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS claims (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    listing_id INTEGER NOT NULL REFERENCES listings(id) ON DELETE CASCADE,
    claimant TEXT NOT NULL,
    email TEXT NOT NULL,
    proof TEXT NOT NULL,
    claim_code TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'approved', 'rejected', 'completed')),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

POSTGRES_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS listings (
        id BIGSERIAL PRIMARY KEY,
        type TEXT NOT NULL CHECK (type IN ('lost', 'found')),
        category TEXT NOT NULL,
        name TEXT NOT NULL,
        description TEXT NOT NULL,
        location TEXT NOT NULL,
        date TEXT NOT NULL,
        reporter TEXT NOT NULL,
        email TEXT NOT NULL,
        photo TEXT,
        status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'returned')),
        created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
    )""",
    """CREATE TABLE IF NOT EXISTS claims (
        id BIGSERIAL PRIMARY KEY,
        listing_id BIGINT NOT NULL REFERENCES listings(id) ON DELETE CASCADE,
        claimant TEXT NOT NULL,
        email TEXT NOT NULL,
        proof TEXT NOT NULL,
        claim_code TEXT NOT NULL UNIQUE,
        status TEXT NOT NULL DEFAULT 'pending'
            CHECK (status IN ('pending', 'approved', 'rejected', 'completed')),
        created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
    )""",
)


def _database_url():
    url = os.environ.get("DATABASE_URL", "").strip()
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    return url


class Connection:
    """Small adapter so app.py can use the same execute API for both DBs."""
    def __init__(self, raw, is_postgres=False):
        self.raw = raw
        self.is_postgres = is_postgres

    def execute(self, sql, params=()):
        if self.is_postgres:
            sql = sql.replace("?", "%s")
        return self.raw.execute(sql, params)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        try:
            if exc_type:
                self.raw.rollback()
            else:
                self.raw.commit()
        finally:
            self.raw.close()
        return False


def get_conn(path=None):
    """Open PostgreSQL on Render, otherwise SQLite locally.

    Passing a path explicitly always selects SQLite; this keeps test_app.py
    compatible with its temporary test database.
    """
    if path is None and _database_url():
        import psycopg
        from psycopg.rows import dict_row

        return Connection(
            psycopg.connect(_database_url(), row_factory=dict_row),
            is_postgres=True,
        )

    conn = sqlite3.connect(path or DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return Connection(conn)


def init_db(path=None):
    """Create the tables if they do not already exist."""
    with get_conn(path) as conn:
        if conn.is_postgres:
            for statement in POSTGRES_SCHEMA:
                conn.execute(statement)
        else:
            conn.raw.executescript(SQLITE_SCHEMA)
