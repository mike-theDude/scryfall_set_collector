"""SQLite storage for mtgsets.

The schema (owned_sets, cards, collection_entries) is specified in docs/DESIGN.md
and created here in issue #2. This module owns schema creation and all DB access.
"""

from __future__ import annotations

from pathlib import Path

#: Default on-disk location of the collection database (gitignored).
DB_PATH = Path("data") / "collection.db"


def init_db(db_path: Path = DB_PATH) -> None:
    """Create the database file and tables. Implemented in issue #2."""
    raise NotImplementedError("db.init_db — see issue #2")
