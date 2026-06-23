"""SQLite storage for mtgsets.

The schema (owned_sets, cards, collection_entries) is specified in docs/DESIGN.md.
This module owns schema creation and, in later issues, all DB access. Keep the DDL
below in sync with the design doc.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from pathlib import Path
from typing import Any

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


def list_owned_sets(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Return owned sets with their generated full-set entry counts, newest first.

    Each row exposes the owned_sets columns plus ``entry_count`` — the number of
    collection_entries generated for that set (source_type='full_set').
    """
    return conn.execute(
        "SELECT o.set_code, o.set_name, o.quantity, o.language, o.condition, "
        "o.foil, o.profile, o.added_at, "
        "COUNT(e.id) AS entry_count "
        "FROM owned_sets o "
        "LEFT JOIN collection_entries e "
        "  ON e.source_set_code = o.set_code AND e.source_type = 'full_set' "
        "GROUP BY o.set_code "
        "ORDER BY o.added_at DESC",
    ).fetchall()


def get_export_entries(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Return every collection entry joined to its cached card, for export.

    Ordered by set then collector number (numeric where possible). Exposes the
    card's name/set_code/collector_number alongside the entry's quantity,
    condition, language, foil, source_type and source_set_code.
    """
    return conn.execute(
        "SELECT c.name, c.set_code, c.collector_number, "
        "e.quantity, e.condition, e.language, e.foil, "
        "e.source_type, e.source_set_code "
        "FROM collection_entries e "
        "JOIN cards c ON c.scryfall_id = e.scryfall_id "
        "ORDER BY c.set_code, "
        "CAST(c.collector_number AS INTEGER), c.collector_number",
    ).fetchall()


def get_owned_cards(conn: sqlite3.Connection) -> list[tuple[int, dict[str, Any]]]:
    """Return ``(quantity, full Scryfall card object)`` for every collection entry.

    Joins collection_entries to the cached ``cards`` table and parses each stored
    ``full_json`` blob, so the stats layer can aggregate by rarity/color/type/mana
    value/price without re-hitting Scryfall. The raw object carries everything those
    breakdowns need (``rarity``, ``color_identity``, ``type_line``, ``cmc``,
    ``prices``, ``set``, ``id``).
    """
    rows = conn.execute(
        "SELECT e.quantity AS quantity, c.full_json AS full_json "
        "FROM collection_entries e "
        "JOIN cards c ON c.scryfall_id = e.scryfall_id"
    ).fetchall()
    return [(row["quantity"], json.loads(row["full_json"])) for row in rows]


def count_full_set_entries(conn: sqlite3.Connection, set_code: str) -> int:
    """Return how many generated full-set entries exist for ``set_code``."""
    return conn.execute(
        "SELECT COUNT(*) FROM collection_entries "
        "WHERE source_type = 'full_set' AND source_set_code = ?",
        (set_code.lower(),),
    ).fetchone()[0]


def delete_full_set_entries(conn: sqlite3.Connection, set_code: str) -> int:
    """Delete ONLY the generated full-set entries for ``set_code``.

    Unlike :func:`remove_owned_set`, the owned_sets row is left in place — this is
    the half a ``refresh`` needs to regenerate entries while keeping ownership.
    Matches source_type='full_set' AND source_set_code=<set>, so manual singles and
    overrides are never touched. Does not commit (caller owns the transaction).
    """
    return conn.execute(
        "DELETE FROM collection_entries WHERE source_type = 'full_set' AND source_set_code = ?",
        (set_code.lower(),),
    ).rowcount


def update_owned_set_name(conn: sqlite3.Connection, set_code: str, set_name: str) -> None:
    """Update an owned set's cached display name (e.g. on refresh). No commit."""
    conn.execute(
        "UPDATE owned_sets SET set_name = ? WHERE set_code = ?",
        (set_name, set_code.lower()),
    )


def remove_owned_set(conn: sqlite3.Connection, set_code: str) -> tuple[int, bool]:
    """Remove an owned set and ONLY its generated entries.

    Deletes collection_entries with source_type='full_set' and
    source_set_code=<set>, then the owned_sets row. Manual singles and overrides
    are never matched by this query (see docs/DESIGN.md). Returns
    ``(entries_deleted, set_existed)``.
    """
    code = set_code.lower()
    entries_deleted = conn.execute(
        "DELETE FROM collection_entries WHERE source_type = 'full_set' AND source_set_code = ?",
        (code,),
    ).rowcount
    set_existed = conn.execute("DELETE FROM owned_sets WHERE set_code = ?", (code,)).rowcount > 0
    conn.commit()
    return entries_deleted, set_existed


def insert_collection_entries(conn: sqlite3.Connection, rows: Iterable[tuple[Any, ...]]) -> int:
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
