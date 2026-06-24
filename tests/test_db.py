"""Unit tests for mtgsets.db — SQLite schema and access.

The crown-jewel test here is the **removal invariant**: removing a set must
delete only that set's generated full_set entries and never touch manual singles
or overrides. That invariant is what makes set removal safe (see docs/DESIGN.md),
so it gets an explicit, heavily-commented test.

All tests run against a real on-disk SQLite file under tmp_path, with foreign
keys enforced exactly as in production (db.get_connection turns them on).
"""

from __future__ import annotations

import sqlite3

import pytest

from mtgsets import db


def make_card(
    card_id: str, *, set_code: str = "neo", name: str = "Test Card", collector_number: str = "1"
) -> dict:
    """A minimal raw Scryfall-shaped card sufficient for db.upsert_cards."""
    return {
        "id": card_id,
        "name": name,
        "set": set_code,
        "collector_number": collector_number,
        "lang": "en",
        "rarity": "common",
        "type_line": "Creature",
        "digital": False,
        "promo": False,
        "variation": False,
        "booster": True,
    }


def add_entry(
    conn,
    card_id: str,
    set_code: str,
    source_type: str,
    source_set_code: str | None,
    collector_number: str = "1",
) -> None:
    """Insert one collection_entries row with an explicit source tagging."""
    row = (card_id, set_code, 1, "Near Mint", "English", 0, source_type, source_set_code)
    db.insert_collection_entries(conn, [row])
    conn.commit()


@pytest.fixture
def conn(tmp_path):
    """A connection to a freshly-initialised database on disk."""
    db_path = tmp_path / "collection.db"
    assert db.init_db(db_path) is True  # newly created
    connection = db.get_connection(db_path)
    try:
        yield connection
    finally:
        connection.close()


def entry_signatures(conn) -> set[tuple]:
    """(scryfall_id, source_type, source_set_code) for every collection entry."""
    rows = conn.execute(
        "SELECT scryfall_id, source_type, source_set_code FROM collection_entries"
    ).fetchall()
    return {tuple(r) for r in rows}


# -- init_db ----------------------------------------------------------------------


def test_init_db_reports_creation(tmp_path) -> None:
    db_path = tmp_path / "c.db"
    assert db.init_db(db_path) is True  # created
    assert db.init_db(db_path) is False  # already existed, schema re-ensured


# -- owned-set round trip ---------------------------------------------------------


def test_insert_and_is_set_owned_case_insensitive(conn) -> None:
    assert db.is_set_owned(conn, "neo") is False
    db.insert_owned_set(conn, set_code="NEO", set_name="Neon Dynasty", added_at="2024-01-01")
    conn.commit()
    # Stored and queried lowercase, regardless of input casing.
    assert db.is_set_owned(conn, "neo") is True
    assert db.is_set_owned(conn, "NEO") is True


# -- FK enforcement ---------------------------------------------------------------


def test_collection_entry_requires_existing_card(conn) -> None:
    # No card cached, so the FK to cards(scryfall_id) must reject the entry.
    with pytest.raises(sqlite3.IntegrityError):
        add_entry(conn, "missing-id", "neo", "full_set", "neo")


# -- the removal invariant --------------------------------------------------------


def seed_removal_scenario(conn) -> None:
    """Two owned sets, generated entries for each, plus a manual single and an
    override that is *deliberately* tagged with source_set_code='neo'."""
    db.upsert_cards(
        conn,
        [
            make_card("neo-1", set_code="neo", name="NEO One", collector_number="1"),
            make_card("neo-2", set_code="neo", name="NEO Two", collector_number="2"),
            make_card("mom-1", set_code="mom", name="MOM One", collector_number="1"),
            make_card("manual-1", set_code="neo", name="Manual Single", collector_number="9"),
            make_card("override-1", set_code="neo", name="Override Card", collector_number="8"),
        ],
    )
    db.insert_owned_set(conn, set_code="neo", set_name="Neon Dynasty", added_at="2024-02-01")
    db.insert_owned_set(
        conn, set_code="mom", set_name="March of the Machine", added_at="2024-01-01"
    )
    conn.commit()

    add_entry(conn, "neo-1", "neo", "full_set", "neo")
    add_entry(conn, "neo-2", "neo", "full_set", "neo")
    add_entry(conn, "mom-1", "mom", "full_set", "mom")
    add_entry(conn, "manual-1", "neo", "manual", None)
    # Override tagged with NEO's code on purpose: removal must be guarded by
    # source_type, not source_set_code, so this must SURVIVE removing NEO.
    add_entry(conn, "override-1", "neo", "override", "neo")


def test_remove_owned_set_deletes_only_generated_entries(conn) -> None:
    seed_removal_scenario(conn)
    assert entry_signatures(conn) == {
        ("neo-1", "full_set", "neo"),
        ("neo-2", "full_set", "neo"),
        ("mom-1", "full_set", "mom"),
        ("manual-1", "manual", None),
        ("override-1", "override", "neo"),
    }

    entries_deleted, set_existed = db.remove_owned_set(conn, "neo")

    assert set_existed is True
    assert entries_deleted == 2  # only NEO's two full_set rows

    # NEO's owned_sets row is gone; MOM remains owned.
    assert db.is_set_owned(conn, "neo") is False
    assert db.is_set_owned(conn, "mom") is True

    # The manual single, the override (despite its neo tag), and MOM's generated
    # entry all survive. Only NEO's full_set rows were deleted.
    assert entry_signatures(conn) == {
        ("mom-1", "full_set", "mom"),
        ("manual-1", "manual", None),
        ("override-1", "override", "neo"),
    }


def test_remove_unowned_set_is_noop(conn) -> None:
    entries_deleted, set_existed = db.remove_owned_set(conn, "xyz")
    assert entries_deleted == 0
    assert set_existed is False


# -- delete_full_set_entries (refresh half) ---------------------------------------


def test_delete_full_set_entries_keeps_owned_row_and_others(conn) -> None:
    # The refresh path deletes a set's generated entries but keeps the owned_sets
    # row (unlike remove). Manual singles and overrides must still survive.
    seed_removal_scenario(conn)

    deleted = db.delete_full_set_entries(conn, "NEO")  # case-insensitive
    conn.commit()
    assert deleted == 2  # only NEO's two full_set rows

    # NEO is still owned — only its generated entries were dropped.
    assert db.is_set_owned(conn, "neo") is True
    assert entry_signatures(conn) == {
        ("mom-1", "full_set", "mom"),
        ("manual-1", "manual", None),
        ("override-1", "override", "neo"),
    }


def test_update_owned_set_name(conn) -> None:
    db.insert_owned_set(conn, set_code="neo", set_name="Old Name", added_at="2024-01-01")
    conn.commit()
    db.update_owned_set_name(conn, "NEO", "Kamigawa: Neon Dynasty")
    conn.commit()
    rows = db.list_owned_sets(conn)
    assert rows[0]["set_name"] == "Kamigawa: Neon Dynasty"


# -- count_full_set_entries -------------------------------------------------------


def test_count_full_set_entries(conn) -> None:
    seed_removal_scenario(conn)
    assert db.count_full_set_entries(conn, "neo") == 2
    assert db.count_full_set_entries(conn, "NEO") == 2  # case-insensitive
    assert db.count_full_set_entries(conn, "mom") == 1
    assert db.count_full_set_entries(conn, "unknown") == 0


# -- list_owned_sets --------------------------------------------------------------


def test_list_owned_sets_counts_and_ordering(conn) -> None:
    seed_removal_scenario(conn)
    rows = db.list_owned_sets(conn)
    # Newest added_at first: NEO (2024-02-01) before MOM (2024-01-01).
    assert [r["set_code"] for r in rows] == ["neo", "mom"]
    counts = {r["set_code"]: r["entry_count"] for r in rows}
    # entry_count counts only generated full_set rows, not the manual/override.
    assert counts == {"neo": 2, "mom": 1}


def test_list_owned_sets_zero_entries(conn) -> None:
    db.insert_owned_set(conn, set_code="neo", set_name="Neon Dynasty", added_at="2024-01-01")
    conn.commit()
    rows = db.list_owned_sets(conn)
    assert len(rows) == 1
    assert rows[0]["entry_count"] == 0


# -- manual singles: add_manual_entry / remove_manual_card ------------------------


def test_add_manual_entry_inserts_then_stacks(conn) -> None:
    db.upsert_cards(conn, [make_card("neo-1", set_code="neo", collector_number="2")])
    conn.commit()

    action, qty = db.add_manual_entry(conn, scryfall_id="neo-1", set_code="neo", quantity=1)
    conn.commit()
    assert (action, qty) == ("added", 1)

    # Same printing + finish/condition/language stacks into the one row.
    action, qty = db.add_manual_entry(conn, scryfall_id="neo-1", set_code="neo", quantity=2)
    conn.commit()
    assert (action, qty) == ("increased", 3)
    assert entry_signatures(conn) == {("neo-1", "manual", None)}  # still a single row


def test_add_manual_entry_foil_is_a_distinct_row(conn) -> None:
    db.upsert_cards(conn, [make_card("neo-1", set_code="neo", collector_number="2")])
    conn.commit()
    db.add_manual_entry(conn, scryfall_id="neo-1", set_code="neo", foil=0)
    db.add_manual_entry(conn, scryfall_id="neo-1", set_code="neo", foil=1)
    conn.commit()
    # Foil and nonfoil are different collectibles -> two rows, not a stack.
    rows = conn.execute(
        "SELECT foil, quantity FROM collection_entries WHERE source_type = 'manual'"
    ).fetchall()
    assert {(r["foil"], r["quantity"]) for r in rows} == {(0, 1), (1, 1)}


def test_remove_manual_card_only_deletes_manual_rows(conn) -> None:
    # A card present as BOTH a full_set entry and a manual single (coexistence).
    db.upsert_cards(conn, [make_card("neo-2", set_code="neo", collector_number="2")])
    conn.commit()
    add_entry(conn, "neo-2", "neo", "full_set", "neo", collector_number="2")
    db.add_manual_entry(conn, scryfall_id="neo-2", set_code="neo", quantity=3)
    conn.commit()

    rows_deleted, qty_removed = db.remove_manual_card(conn, "NEO", "2")  # case-insensitive
    assert (rows_deleted, qty_removed) == (1, 3)
    # The full_set entry for the same printing survives.
    assert entry_signatures(conn) == {("neo-2", "full_set", "neo")}


def test_remove_manual_card_no_match_is_zero(conn) -> None:
    assert db.remove_manual_card(conn, "neo", "999") == (0, 0)


# -- overrides: get_full_set_printing / set_override / clear_override --------------


def seed_owned_full_set(conn) -> None:
    """NEO owned as a full set with two generated entries (numbers 1 and 2)."""
    db.upsert_cards(
        conn,
        [
            make_card("neo-1", set_code="neo", name="NEO One", collector_number="1"),
            make_card("neo-2", set_code="neo", name="NEO Two", collector_number="2"),
        ],
    )
    db.insert_owned_set(conn, set_code="neo", set_name="Neon Dynasty", added_at="2024-02-01")
    add_entry(conn, "neo-1", "neo", "full_set", "neo", collector_number="1")
    add_entry(conn, "neo-2", "neo", "full_set", "neo", collector_number="2")
    conn.commit()


def test_get_full_set_printing_resolves_only_owned_full_set_cards(conn) -> None:
    seed_owned_full_set(conn)
    row = db.get_full_set_printing(conn, "NEO", "2")  # case-insensitive
    assert (row["scryfall_id"], row["set_code"], row["name"]) == ("neo-2", "neo", "NEO Two")
    # A number that isn't a generated full_set card of NEO resolves to nothing.
    assert db.get_full_set_printing(conn, "neo", "999") is None
    assert db.get_full_set_printing(conn, "mom", "1") is None


def test_set_override_creates_then_replaces(conn) -> None:
    seed_owned_full_set(conn)
    action = db.set_override(
        conn,
        scryfall_id="neo-1",
        set_code="neo",
        source_set_code="neo",
        quantity=2,
        condition="Played",
        language="Japanese",
        foil=1,
    )
    conn.commit()
    assert action == "created"
    # The override coexists with the generated full_set row (both remain).
    assert entry_signatures(conn) == {
        ("neo-1", "full_set", "neo"),
        ("neo-2", "full_set", "neo"),
        ("neo-1", "override", "neo"),
    }

    # Re-running REPLACES the values (not stacked) and stays a single override row.
    action = db.set_override(
        conn,
        scryfall_id="neo-1",
        set_code="neo",
        source_set_code="neo",
        quantity=5,
        condition="Damaged",
        language="English",
        foil=0,
    )
    conn.commit()
    assert action == "updated"
    row = conn.execute(
        "SELECT quantity, condition, language, foil FROM collection_entries "
        "WHERE source_type = 'override'"
    ).fetchone()
    assert (row["quantity"], row["condition"], row["language"], row["foil"]) == (
        5,
        "Damaged",
        "English",
        0,
    )


def test_override_survives_refresh_and_remove(conn) -> None:
    # The whole point: an override is only ever a source_type='override' row, so the
    # full_set-only refresh and remove paths leave it intact.
    seed_owned_full_set(conn)
    db.set_override(conn, scryfall_id="neo-1", set_code="neo", source_set_code="neo", foil=1)
    conn.commit()

    db.delete_full_set_entries(conn, "neo")  # the refresh half
    conn.commit()
    assert entry_signatures(conn) == {("neo-1", "override", "neo")}

    # Re-generate, then a full remove — the override still survives.
    add_entry(conn, "neo-1", "neo", "full_set", "neo", collector_number="1")
    db.remove_owned_set(conn, "neo")
    assert entry_signatures(conn) == {("neo-1", "override", "neo")}


def test_clear_override_removes_only_the_override(conn) -> None:
    seed_owned_full_set(conn)
    db.set_override(conn, scryfall_id="neo-1", set_code="neo", source_set_code="neo", foil=1)
    conn.commit()

    assert db.clear_override(conn, "NEO", "1") == 1  # case-insensitive
    # The generated full_set row is untouched; only the override is gone.
    assert entry_signatures(conn) == {
        ("neo-1", "full_set", "neo"),
        ("neo-2", "full_set", "neo"),
    }
    assert db.clear_override(conn, "neo", "1") == 0  # already gone -> no-op


# -- get_owned_cards scoping / get_owned_set --------------------------------------


def test_get_owned_cards_scoped_to_one_set(conn) -> None:
    seed_removal_scenario(conn)
    # Whole collection: every entry (2 NEO full_set + 1 MOM + manual + override).
    assert len(db.get_owned_cards(conn)) == 5
    # Scoped to NEO: only its two generated full_set cards — the manual single and
    # override tagged with neo are NOT part of the set's contents.
    neo = db.get_owned_cards(conn, source_set_code="NEO")  # case-insensitive
    assert sorted(c["id"] for _, c in neo) == ["neo-1", "neo-2"]
    mom = db.get_owned_cards(conn, source_set_code="mom")
    assert [c["id"] for _, c in mom] == ["mom-1"]
    assert db.get_owned_cards(conn, source_set_code="unknown") == []


def test_get_owned_set_returns_row_or_none(conn) -> None:
    db.insert_owned_set(conn, set_code="neo", set_name="Neon Dynasty", added_at="2024-02-01")
    conn.commit()
    row = db.get_owned_set(conn, "NEO")  # case-insensitive
    assert row is not None
    assert row["set_code"] == "neo"
    assert row["set_name"] == "Neon Dynasty"
    assert row["added_at"] == "2024-02-01"
    assert db.get_owned_set(conn, "mom") is None


# -- get_export_entries -----------------------------------------------------------


def test_get_export_entries_numeric_aware_ordering(conn) -> None:
    # Same set, collector numbers that sort differently as text vs as integers.
    db.upsert_cards(
        conn,
        [
            make_card("c-100", set_code="neo", name="Hundred", collector_number="100"),
            make_card("c-2", set_code="neo", name="Two", collector_number="2"),
            make_card("c-10", set_code="neo", name="Ten", collector_number="10"),
            make_card("m-1", set_code="mom", name="Mom One", collector_number="1"),
        ],
    )
    conn.commit()
    add_entry(conn, "c-100", "neo", "full_set", "neo", collector_number="100")
    add_entry(conn, "c-2", "neo", "full_set", "neo", collector_number="2")
    add_entry(conn, "c-10", "neo", "full_set", "neo", collector_number="10")
    add_entry(conn, "m-1", "mom", "full_set", "mom", collector_number="1")

    rows = db.get_export_entries(conn)
    # Ordered by set_code first, then collector number numerically (2,10,100 — not
    # the lexicographic 10,100,2).
    assert [(r["set_code"], r["collector_number"]) for r in rows] == [
        ("mom", "1"),
        ("neo", "2"),
        ("neo", "10"),
        ("neo", "100"),
    ]


def test_get_export_entries_joins_card_fields(conn) -> None:
    db.upsert_cards(conn, [make_card("neo-1", name="Boseiju", collector_number="266")])
    conn.commit()
    add_entry(conn, "neo-1", "neo", "full_set", "neo", collector_number="266")
    rows = db.get_export_entries(conn)
    assert len(rows) == 1
    row = rows[0]
    assert row["name"] == "Boseiju"
    assert row["collector_number"] == "266"
    assert row["source_type"] == "full_set"
    assert row["source_set_code"] == "neo"


def test_get_export_entries_empty(conn) -> None:
    assert db.get_export_entries(conn) == []


def test_get_export_entries_override_suppresses_full_set_row(conn) -> None:
    # neo-1 has both a full_set row and an override; neo-2 only a full_set row.
    seed_owned_full_set(conn)
    db.set_override(
        conn,
        scryfall_id="neo-1",
        set_code="neo",
        source_set_code="neo",
        quantity=3,
        condition="Played",
        foil=1,
    )
    conn.commit()

    rows = db.get_export_entries(conn)
    # neo-1 is emitted ONCE — as the override (the full_set row is suppressed).
    by_number = {(r["set_code"], r["collector_number"]): r for r in rows}
    assert len(rows) == 2
    over = by_number[("neo", "1")]
    assert over["source_type"] == "override"
    assert (over["quantity"], over["condition"], over["foil"]) == (3, "Played", 1)
    # neo-2 (no override) still exports as its generated full_set row.
    assert by_number[("neo", "2")]["source_type"] == "full_set"
