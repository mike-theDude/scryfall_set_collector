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
    exported_at     TEXT,
    FOREIGN KEY (scryfall_id) REFERENCES cards(scryfall_id)
);

-- Supports safe set removal (issue #9): delete only generated rows for one set
-- without touching manual singles or overrides.
CREATE INDEX IF NOT EXISTS idx_collection_entries_source
    ON collection_entries (source_type, source_set_code);

-- Local cache of Scryfall's full set list (issue #68). One row per set, all sharing
-- a single ``fetched_at`` so the cache is an atomic snapshot with one age (TTL).
CREATE TABLE IF NOT EXISTS scryfall_sets (
    code       TEXT PRIMARY KEY,
    full_json  TEXT NOT NULL,
    fetched_at TEXT NOT NULL
);
"""


def get_connection(db_path: Path = DB_PATH) -> sqlite3.Connection:
    """Open a SQLite connection with name-based row access and FK enforcement on.

    Also applies idempotent column migrations (:func:`_migrate`) so a database created
    before a column existed is brought up to date — not every command runs ``init_db``,
    so the connection itself is the safe choke point.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    _migrate(conn)
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    """Apply lightweight, idempotent schema migrations to an existing database.

    New databases get everything from :data:`SCHEMA`; this only patches *older* ones.
    Currently: add ``collection_entries.exported_at`` (issue #76) when missing. No-ops
    once the column exists, and skips the table when it doesn't exist yet (e.g. during
    initial creation, before ``init_db`` has run the schema).
    """
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(collection_entries)")}
    if cols and "exported_at" not in cols:
        conn.execute("ALTER TABLE collection_entries ADD COLUMN exported_at TEXT")
        conn.commit()


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


def get_export_entries(
    conn: sqlite3.Connection,
    *,
    unexported_only: bool = False,
    source_set_code: str | None = None,
) -> list[sqlite3.Row]:
    """Return collection entries joined to their cached card, for export.

    Ordered by set then collector number (numeric where possible). Exposes each entry's
    ``id`` (so the caller can stamp it as exported) plus the card's
    name/set_code/collector_number and the entry's quantity, condition, language, foil,
    source_type and source_set_code.

    A generated ``full_set`` row is **suppressed** when an ``override`` exists for the
    same ``(scryfall_id, source_set_code)`` — the override carries the corrected
    values and stands in for it, so the printing is exported once (see docs/DESIGN.md).

    Filters for the incremental export (issue #76):

    - ``unexported_only`` restricts to entries not yet exported (``exported_at IS NULL``)
      — the delta default.
    - ``source_set_code`` restricts to one set's entries regardless of export state.
    """
    sql = [
        "SELECT e.id, c.name, c.set_code, c.collector_number, ",
        "e.quantity, e.condition, e.language, e.foil, ",
        "e.source_type, e.source_set_code ",
        "FROM collection_entries e ",
        "JOIN cards c ON c.scryfall_id = e.scryfall_id ",
        "WHERE NOT (e.source_type = 'full_set' AND EXISTS (",
        "  SELECT 1 FROM collection_entries o ",
        "  WHERE o.source_type = 'override' ",
        "  AND o.scryfall_id = e.scryfall_id ",
        "  AND o.source_set_code = e.source_set_code)) ",
    ]
    params: list[Any] = []
    if unexported_only:
        sql.append("AND e.exported_at IS NULL ")
    if source_set_code is not None:
        sql.append("AND e.source_set_code = ? ")
        params.append(source_set_code.lower())
    sql.append("ORDER BY c.set_code, CAST(c.collector_number AS INTEGER), c.collector_number")
    return conn.execute("".join(sql), params).fetchall()


def mark_entries_exported(conn: sqlite3.Connection, ids: list[int], exported_at: str) -> int:
    """Stamp the given collection_entries as exported at ``exported_at``. Commits.

    Called after a successful Moxfield write so a delta export is idempotent — the same
    rows won't re-export next run. Returns the number of rows stamped (0 for empty ids).
    """
    if not ids:
        return 0
    placeholders = ",".join("?" * len(ids))
    conn.execute(
        f"UPDATE collection_entries SET exported_at = ? WHERE id IN ({placeholders})",
        [exported_at, *ids],
    )
    conn.commit()
    return len(ids)


def get_owned_cards(
    conn: sqlite3.Connection, source_set_code: str | None = None
) -> list[tuple[int, dict[str, Any]]]:
    """Return ``(quantity, full Scryfall card object)`` for collection entries.

    Joins collection_entries to the cached ``cards`` table and parses each stored
    ``full_json`` blob, so the stats layer can aggregate by rarity/color/type/mana
    value/price without re-hitting Scryfall. The raw object carries everything those
    breakdowns need (``rarity``, ``color_identity``, ``type_line``, ``cmc``,
    ``prices``, ``set``, ``id``, ``collector_number``, ``name``).

    With ``source_set_code``, scope to a single set's generated entries
    (source_type='full_set'), e.g. for ``show`` — manual singles and overrides are
    excluded. Without it, every entry is returned (the whole-collection default).
    """
    sql = (
        "SELECT e.quantity AS quantity, c.full_json AS full_json "
        "FROM collection_entries e "
        "JOIN cards c ON c.scryfall_id = e.scryfall_id"
    )
    params: tuple[Any, ...] = ()
    if source_set_code is not None:
        sql += " WHERE e.source_type = 'full_set' AND e.source_set_code = ?"
        params = (source_set_code.lower(),)
    rows = conn.execute(sql, params).fetchall()
    return [(row["quantity"], json.loads(row["full_json"])) for row in rows]


def get_owned_set(conn: sqlite3.Connection, set_code: str) -> sqlite3.Row | None:
    """Return the owned_sets row for ``set_code``, or None if not owned."""
    return conn.execute(
        "SELECT set_code, set_name, quantity, language, condition, foil, profile, added_at "
        "FROM owned_sets WHERE set_code = ?",
        (set_code.lower(),),
    ).fetchone()


def count_full_set_entries(conn: sqlite3.Connection, set_code: str) -> int:
    """Return how many generated full-set entries exist for ``set_code``."""
    return conn.execute(
        "SELECT COUNT(*) FROM collection_entries "
        "WHERE source_type = 'full_set' AND source_set_code = ?",
        (set_code.lower(),),
    ).fetchone()[0]


def count_exported_full_set_entries(conn: sqlite3.Connection, set_code: str) -> int:
    """Return how many of a set's full-set entries were already exported to Moxfield.

    These are the ``Full Set: <CODE>``-tagged cards that a CSV import pushed into
    Moxfield and that ``remove`` is about to delete locally — used to tell the user
    whether a manual Moxfield prune is needed (issue #85). Call before removal.
    """
    return conn.execute(
        "SELECT COUNT(*) FROM collection_entries "
        "WHERE source_type = 'full_set' AND source_set_code = ? AND exported_at IS NOT NULL",
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


def add_manual_entry(
    conn: sqlite3.Connection,
    *,
    scryfall_id: str,
    set_code: str,
    quantity: int = 1,
    condition: str = "Near Mint",
    language: str = "English",
    foil: int = 0,
) -> tuple[str, int]:
    """Add a manual single, or bump an existing identical one's quantity.

    Manual entries (source_type='manual', source_set_code=NULL) coexist with any
    full_set entry for the same card — they are never merged with generated rows.
    Two manual adds of the same printing **and** finish/condition/language stack
    into one row's quantity rather than creating duplicates. The referenced card
    must already be cached (FK), so call :func:`upsert_cards` first. Does not commit.

    Returns ``(action, new_quantity)`` where action is ``"added"`` or ``"increased"``.
    """
    existing = conn.execute(
        "SELECT id, quantity FROM collection_entries "
        "WHERE source_type = 'manual' AND scryfall_id = ? AND foil = ? "
        "AND condition = ? AND language = ?",
        (scryfall_id, foil, condition, language),
    ).fetchone()
    if existing is not None:
        new_qty = existing["quantity"] + quantity
        conn.execute(
            "UPDATE collection_entries SET quantity = ? WHERE id = ?",
            (new_qty, existing["id"]),
        )
        return "increased", new_qty
    conn.execute(
        "INSERT INTO collection_entries "
        "(scryfall_id, set_code, quantity, condition, language, foil, "
        "source_type, source_set_code) "
        "VALUES (?, ?, ?, ?, ?, ?, 'manual', NULL)",
        (scryfall_id, set_code.lower(), quantity, condition, language, foil),
    )
    return "added", quantity


def remove_manual_card(
    conn: sqlite3.Connection, set_code: str, collector_number: str
) -> tuple[int, int]:
    """Delete the manual single(s) for a printing, identified by set + number.

    Matches only source_type='manual' rows, so a card's full_set or override
    entries are never touched (the coexistence invariant). Removes every manual
    holding of that printing (any finish/condition). Commits.

    Returns ``(rows_deleted, quantity_removed)``; ``(0, 0)`` if none matched.
    """
    rows = conn.execute(
        "SELECT e.id AS id, e.quantity AS quantity "
        "FROM collection_entries e JOIN cards c ON c.scryfall_id = e.scryfall_id "
        "WHERE e.source_type = 'manual' AND c.set_code = ? AND c.collector_number = ?",
        (set_code.lower(), str(collector_number)),
    ).fetchall()
    if not rows:
        return 0, 0
    ids = [r["id"] for r in rows]
    quantity_removed = sum(r["quantity"] for r in rows)
    placeholders = ",".join("?" * len(ids))
    conn.execute(f"DELETE FROM collection_entries WHERE id IN ({placeholders})", ids)
    conn.commit()
    return len(ids), quantity_removed


def get_full_set_printing(
    conn: sqlite3.Connection, set_code: str, collector_number: str
) -> sqlite3.Row | None:
    """Resolve a printing that's part of an owned full set, by set + number.

    Returns a row exposing ``scryfall_id``, ``set_code`` (the card's printing set) and
    ``name`` for the ``full_set`` entry of ``set_code`` whose card has this collector
    number, or None if that set doesn't generate such an entry. Used by ``override-card``
    to confirm the card is in an owned full set before recording an override against it.
    """
    return conn.execute(
        "SELECT e.scryfall_id AS scryfall_id, c.set_code AS set_code, c.name AS name "
        "FROM collection_entries e JOIN cards c ON c.scryfall_id = e.scryfall_id "
        "WHERE e.source_type = 'full_set' AND e.source_set_code = ? "
        "AND c.collector_number = ?",
        (set_code.lower(), str(collector_number)),
    ).fetchone()


def set_override(
    conn: sqlite3.Connection,
    *,
    scryfall_id: str,
    set_code: str,
    source_set_code: str,
    quantity: int = 1,
    condition: str = "Near Mint",
    language: str = "English",
    foil: int = 0,
) -> str:
    """Create or replace the override for one full-set printing. No commit.

    An override is keyed by ``(scryfall_id, source_set_code)`` — at most one per
    printing per set. Unlike :func:`add_manual_entry`, re-running **replaces** the
    stored values instead of stacking, because an override is a correction of the
    generated row, not an additive single. Returns ``"created"`` or ``"updated"``.
    """
    existing = conn.execute(
        "SELECT id FROM collection_entries "
        "WHERE source_type = 'override' AND scryfall_id = ? AND source_set_code = ?",
        (scryfall_id, source_set_code.lower()),
    ).fetchone()
    if existing is not None:
        conn.execute(
            "UPDATE collection_entries SET quantity = ?, condition = ?, language = ?, "
            "foil = ? WHERE id = ?",
            (quantity, condition, language, foil, existing["id"]),
        )
        return "updated"
    conn.execute(
        "INSERT INTO collection_entries "
        "(scryfall_id, set_code, quantity, condition, language, foil, "
        "source_type, source_set_code) "
        "VALUES (?, ?, ?, ?, ?, ?, 'override', ?)",
        (
            scryfall_id,
            set_code.lower(),
            quantity,
            condition,
            language,
            foil,
            source_set_code.lower(),
        ),
    )
    return "created"


def clear_override(conn: sqlite3.Connection, set_code: str, collector_number: str) -> int:
    """Delete the override for a printing (source_type='override' only). Commits.

    Identifies the printing by ``set_code`` (the overriding set's code, matched against
    ``source_set_code``) + collector number. The generated ``full_set`` row is left
    intact, so the printing reverts to the full-set defaults. Returns rows deleted.
    """
    deleted = conn.execute(
        "DELETE FROM collection_entries WHERE id IN ("
        "  SELECT e.id FROM collection_entries e "
        "  JOIN cards c ON c.scryfall_id = e.scryfall_id "
        "  WHERE e.source_type = 'override' AND e.source_set_code = ? "
        "  AND c.collector_number = ?)",
        (set_code.lower(), str(collector_number)),
    ).rowcount
    conn.commit()
    return deleted


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


# -- Scryfall set-list cache (issue #68) ------------------------------------------


def replace_cached_sets(
    conn: sqlite3.Connection, sets: Iterable[dict[str, Any]], fetched_at: str
) -> int:
    """Replace the cached Scryfall set list wholesale. Commits.

    Clears ``scryfall_sets`` and re-inserts one row per set (its ``code``, the raw
    JSON object, and a shared ``fetched_at`` timestamp), so the cache is always a
    single atomic snapshot with one age for the TTL check. Returns the rows written.
    """
    rows = [
        (s["code"], json.dumps(s, separators=(",", ":")), fetched_at) for s in sets if s.get("code")
    ]
    conn.execute("DELETE FROM scryfall_sets")
    conn.executemany(
        "INSERT OR REPLACE INTO scryfall_sets (code, full_json, fetched_at) VALUES (?, ?, ?)",
        rows,
    )
    conn.commit()
    return len(rows)


def get_cached_sets(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Return the cached Scryfall set list (parsed), or ``[]`` if the cache is empty."""
    rows = conn.execute("SELECT full_json FROM scryfall_sets").fetchall()
    return [json.loads(row["full_json"]) for row in rows]


def get_sets_fetched_at(conn: sqlite3.Connection) -> str | None:
    """Return when the set-list cache was last refreshed (ISO 8601), or None if empty.

    Every cached row shares the same ``fetched_at`` (see :func:`replace_cached_sets`),
    so any row's value is the snapshot's age.
    """
    row = conn.execute("SELECT fetched_at FROM scryfall_sets LIMIT 1").fetchone()
    return row["fetched_at"] if row is not None else None
