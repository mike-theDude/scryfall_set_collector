"""SQLite storage for mtgsets.

The schema (owned_sets, cards, collection_entries) is specified in docs/DESIGN.md.
This module owns schema creation and, in later issues, all DB access. Keep the DDL
below in sync with the design doc.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

#: Default on-disk location of the collection database (gitignored).
DB_PATH = Path("data") / "collection.db"

#: Schema DDL — keep in sync with docs/DESIGN.md.
SCHEMA = """
CREATE TABLE IF NOT EXISTS owned_sets (
    set_code  TEXT PRIMARY KEY,
    set_name  TEXT NOT NULL,
    quantity  INTEGER NOT NULL DEFAULT 1,
    language  TEXT NOT NULL DEFAULT 'English',
    condition TEXT NOT NULL DEFAULT 'Near Mint',
    foil      INTEGER NOT NULL DEFAULT 0,
    profile   TEXT NOT NULL DEFAULT 'main_set_plus_basics',
    added_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cards (
    scryfall_id      TEXT PRIMARY KEY,
    name             TEXT NOT NULL,
    set_code         TEXT NOT NULL,
    collector_number TEXT NOT NULL,
    lang             TEXT,
    rarity           TEXT,
    type_line        TEXT,
    digital          INTEGER NOT NULL,
    promo            INTEGER NOT NULL,
    variation        INTEGER NOT NULL,
    booster          INTEGER,
    full_json        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS collection_entries (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    scryfall_id     TEXT NOT NULL,
    set_code        TEXT NOT NULL,
    quantity        INTEGER NOT NULL DEFAULT 1,
    condition       TEXT NOT NULL DEFAULT 'Near Mint',
    language        TEXT NOT NULL DEFAULT 'English',
    foil            INTEGER NOT NULL DEFAULT 0,
    source_type     TEXT NOT NULL,
    source_set_code TEXT,
    FOREIGN KEY (scryfall_id) REFERENCES cards(scryfall_id)
);

-- Supports safe set removal (issue #9): delete only generated rows for one set
-- without touching manual singles or overrides.
CREATE INDEX IF NOT EXISTS idx_collection_entries_source
    ON collection_entries (source_type, source_set_code);
"""


def get_connection(db_path: Path = DB_PATH) -> sqlite3.Connection:
    """Open a SQLite connection with name-based row access and FK enforcement on."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(db_path: Path = DB_PATH) -> bool:
    """Create the database file and tables. Idempotent.

    Returns True if the database file was newly created, False if it already existed
    (the schema is still ensured either way).
    """
    db_path = Path(db_path)
    existed = db_path.exists()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = get_connection(db_path)
    try:
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()
    return not existed
