"""Shared database connection (author-knowledge standalone copy).

Connects to ~/.astromind-praxis/astromind_praxis.db
Auto-creates v6.1 tables (articles, knowledge_atoms, mental_models, author_profiles) if missing.
"""

import sqlite3
from pathlib import Path

DB_DIR = Path.home() / ".astromind-praxis"
DB_PATH = DB_DIR / "astromind_praxis.db"

_SCHEMA_V6_1 = """
CREATE TABLE IF NOT EXISTS articles (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    author_name     TEXT    NOT NULL,
    title           TEXT    NOT NULL,
    url             TEXT    NOT NULL,
    url_hash        TEXT    NOT NULL UNIQUE,
    published_at    TEXT,
    ingested_at     TEXT    NOT NULL DEFAULT (datetime('now')),
    content_text    TEXT    NOT NULL DEFAULT '',
    word_count      INTEGER DEFAULT 0,
    source_type     TEXT    DEFAULT 'wechat'
);

CREATE TABLE IF NOT EXISTS knowledge_atoms (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    article_id      INTEGER NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
    author_name     TEXT    NOT NULL,
    type            TEXT    NOT NULL CHECK (type IN ('fact','method','value','assumption','counter','style')),
    content         TEXT    NOT NULL,
    topic           TEXT    NOT NULL,
    evidence        TEXT,
    embedding_ref   TEXT,
    merged_to       INTEGER REFERENCES mental_models(id),
    valid_from      TEXT,
    valid_until     TEXT,
    last_confirmed_at TEXT,
    published_at    TEXT,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_atoms_author ON knowledge_atoms(author_name);
CREATE INDEX IF NOT EXISTS idx_atoms_type ON knowledge_atoms(type);
CREATE INDEX IF NOT EXISTS idx_atoms_topic ON knowledge_atoms(topic);
CREATE INDEX IF NOT EXISTS idx_atoms_merged ON knowledge_atoms(merged_to);
CREATE INDEX IF NOT EXISTS idx_atoms_article ON knowledge_atoms(article_id);

CREATE TABLE IF NOT EXISTS mental_models (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    author_name     TEXT    NOT NULL,
    topic           TEXT    NOT NULL,
    title           TEXT    NOT NULL,
    content_md      TEXT    NOT NULL,
    md_path         TEXT,
    triple_check    TEXT,
    evidence_count  INTEGER DEFAULT 0,
    article_count   INTEGER DEFAULT 0,
    first_seen_at   TEXT,
    last_updated_at TEXT,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS author_profiles (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    author_name         TEXT    NOT NULL UNIQUE,
    knowledge_base_path TEXT    NOT NULL DEFAULT '',
    article_count       INTEGER DEFAULT 0,
    atom_count          INTEGER DEFAULT 0,
    l2_model_count      INTEGER DEFAULT 0,
    l3_available        INTEGER DEFAULT 0,
    l4_available        INTEGER DEFAULT 0,
    persona_md          TEXT,
    mirror_md           TEXT,
    last_article_at     TEXT,
    last_distilled_at   TEXT,
    created_at          TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_atoms_fts USING fts5(
    content, topic, author_name,
    content='knowledge_atoms', content_rowid='id'
);
"""


def get_db_path() -> str:
    return str(DB_PATH)


def ensure_db_dir():
    DB_DIR.mkdir(parents=True, exist_ok=True)


def ensure_schema_v6_1():
    """Create v6.1 tables if they don't exist yet (idempotent)."""
    conn = sqlite3.connect(str(DB_PATH))
    try:
        conn.executescript(_SCHEMA_V6_1)
        conn.commit()
    finally:
        conn.close()


def row_to_dict(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    return dict(row)


def rows_to_dicts(rows: list[sqlite3.Row]) -> list[dict]:
    return [dict(r) for r in rows]


def get_connection() -> sqlite3.Connection:
    ensure_db_dir()
    ensure_schema_v6_1()
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn
