"""SQLite storage for mtgsets.

The schema (owned_sets, cards, collection_entries) is specified in docs/DESIGN.md.
This module owns schema creation and, in later issues, all DB access. Keep the DDL
below in sync with the design doc.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Iterable

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


def _card_row(raw: dict[str, Any]) -> tuple[Any, ...]:
    """Map a raw Scryfall card object to a `cards` table row (see docs/DESIGN.md)."""
    booster = raw.get("booster")
    return (
        raw["id"],
        raw["name"],
        raw["set"],
        raw["collector_number"],
        raw.get("lang"),
        raw.get("rarity"),
        raw.get("type_line"),
        int(bool(raw.get("digital", False))),
        int(bool(raw.get("promo", False))),
        int(bool(raw.get("variation", False))),
        None if booster is None else int(bool(booster)),
        json.dumps(raw, separators=(",", ":")),
    )


def upsert_cards(conn: sqlite3.Connection, raw_cards: Iterable[dict[str, Any]]) -> int:
    """Insert or replace raw Scryfall card objects into the `cards` cache.

    Keyed by scryfall_id, so re-fetching a set refreshes existing rows. Returns the
    number of rows written.
    """
    rows = [_card_row(c) for c in raw_cards]
    conn.executemany(
        "INSERT OR REPLACE INTO cards "
        "(scryfall_id, name, set_code, collector_number, lang, rarity, type_line, "
        "digital, promo, variation, booster, full_json) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    conn.commit()
    return len(rows)


def is_set_owned(conn: sqlite3.Connection, set_code: str) -> bool:
    """Return True if a row for ``set_code`` exists in owned_sets."""
    row = conn.execute(
        "SELECT 1 FROM owned_sets WHERE set_code = ?", (set_code.lower(),)
    ).fetchone()
    return row is not None


def insert_owned_set(
    conn: sqlite3.Connection,
    *,
    set_code: str,
    set_name: str,
    added_at: str,
    quantity: int = 1,
    language: str = "English",
    condition: str = "Near Mint",
    foil: int = 0,
    profile: str = "main_set_plus_basics",
) -> None:
    """Insert one owned_sets row. Does not commit (caller owns the transaction)."""
    conn.execute(
        "INSERT INTO owned_sets "
        "(set_code, set_name, quantity, language, condition, foil, profile, added_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            set_code.lower(),
            set_name,
            quantity,
            language,
            condition,
            foil,
            profile,
            added_at,
        ),
    )


def insert_collection_entries(
    conn: sqlite3.Connection, rows: Iterable[tuple[Any, ...]]
) -> int:
    """Insert collection_entries rows. Does not commit (caller owns the transaction).

    Each row is column-ordered: (scryfall_id, set_code, quantity, condition,
    language, foil, source_type, source_set_code). The referenced cards must
    already exist (FK), so cache them with upsert_cards first.
    """
    rows = list(rows)
    conn.executemany(
        "INSERT INTO collection_entries "
        "(scryfall_id, set_code, quantity, condition, language, foil, "
        "source_type, source_set_code) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    return len(rows)
