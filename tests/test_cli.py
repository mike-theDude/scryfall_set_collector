"""Integration tests for mtgsets.cli — the Typer command layer.

End to end through real filter/db/export code; only the HTTP boundary is mocked.
``scryfall.ScryfallClient`` is swapped for a real client wired with an
``httpx.MockTransport`` that serves the NEO fixture cards, so ``add``/``preview``
hit no network. Each command runs against a fresh ``--db-path`` under tmp_path —
no shared global database.
"""

from __future__ import annotations

import csv
import json

import httpx
import pytest
from typer.testing import CliRunner

from mtgsets import db, scryfall, wantlist
from mtgsets.cli import app
from mtgsets.scryfall import ScryfallClient

runner = CliRunner()

NEO_SET = {
    "code": "neo",
    "name": "Kamigawa: Neon Dynasty",
    "set_type": "expansion",
    "digital": False,
    "released_at": "2022-02-18",
}
MOM_SET = {
    "code": "mom",
    "name": "March of the Machine",
    "set_type": "expansion",
    "digital": False,
    "released_at": "2023-04-21",
}
HOB_SET = {
    "code": "hob",
    "name": "The Hobbit",
    "set_type": "expansion",
    "digital": False,
    "released_at": "2025-08-01",
}
HOB_CARDS = [
    {
        "id": "hob-94",
        "name": "Dori, Bearer of Friends",
        "set": "hob",
        "set_name": "The Hobbit",
        "collector_number": "94",
        "lang": "en",
        "rarity": "common",
        "scryfall_uri": "https://scryfall.com/card/hob/94/dori-bearer-of-friends",
        "tcgplayer_id": 600094,
        "purchase_uris": {"tcgplayer": "https://shop.tcgplayer.test/dori"},
    },
    {
        "id": "hob-91",
        "name": "Dáin Ironfoot",
        "set": "hob",
        "set_name": "The Hobbit",
        "collector_number": "91",
        "lang": "en",
        "rarity": "rare",
        "scryfall_uri": "https://scryfall.com/card/hob/91/dain-ironfoot",
        "tcgplayer_id": 600091,
    },
    {
        "id": "hob-a-1",
        "name": "Lettered Printing",
        "set": "hob",
        "set_name": "The Hobbit",
        "collector_number": "A-1",
        "lang": "en",
        "rarity": "common",
        "scryfall_uri": "https://scryfall.com/card/hob/A-1/lettered-printing",
        "tcgplayer_id": None,
    },
]


@pytest.fixture
def mock_scryfall(monkeypatch, neo_cards):
    """Patch ScryfallClient so add/preview serve the NEO fixtures over a mock
    transport. /sets/neo and /sets/mom (and their card searches) succeed using
    the same NEO fixture cards; everything else 404s. A second known set lets
    the multi-set ``add`` tests exercise more than one good code per run."""

    sets_by_code = {"neo": NEO_SET, "mom": MOM_SET}

    cards_by_number = {str(c.get("collector_number")): c for c in neo_cards}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.startswith("/sets/"):
            code = path.removeprefix("/sets/")
            if code in sets_by_code:
                return httpx.Response(200, json=sets_by_code[code])
            return httpx.Response(404, json={"details": "Set not found"})
        if path == "/cards/search":
            q = request.url.params.get("q")
            if q in ("set:neo", "set:mom"):
                return httpx.Response(200, json={"data": neo_cards, "has_more": False})
            return httpx.Response(404, json={"details": "no cards found"})
        if path.startswith("/cards/"):
            # /cards/<set>/<number> — one exact printing (add-card).
            parts = path.removeprefix("/cards/").split("/")
            if len(parts) == 2 and parts[0] in sets_by_code and parts[1] in cards_by_number:
                return httpx.Response(200, json=cards_by_number[parts[1]])
            return httpx.Response(404, json={"details": "card not found"})
        if path == "/sets":
            return httpx.Response(
                200, json={"data": list(sets_by_code.values()), "has_more": False}
            )
        return httpx.Response(404, json={"details": "unexpected path"})

    monkeypatch.setattr(scryfall, "_REQUEST_DELAY", 0.0)
    monkeypatch.setattr(
        scryfall,
        "ScryfallClient",
        lambda *a, **k: ScryfallClient(transport=httpx.MockTransport(handler)),
    )


@pytest.fixture
def mock_want_list_scryfall(monkeypatch):
    """Serve one unowned set and exact card lookups for want-list tests."""
    cards_by_number = {card["collector_number"]: card for card in HOB_CARDS}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/sets/hob":
            return httpx.Response(200, json=HOB_SET)
        if path.startswith("/sets/"):
            return httpx.Response(404, json={"details": "Set not found"})
        if path.startswith("/cards/hob/"):
            number = path.removeprefix("/cards/hob/")
            if number in cards_by_number:
                return httpx.Response(200, json=cards_by_number[number])
            return httpx.Response(404, json={"details": "Card not found"})
        return httpx.Response(404, json={"details": "unexpected path"})

    monkeypatch.setattr(scryfall, "_REQUEST_DELAY", 0.0)
    monkeypatch.setattr(
        scryfall,
        "ScryfallClient",
        lambda *a, **k: ScryfallClient(transport=httpx.MockTransport(handler)),
    )


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "collection.db"


def add_neo(db_path) -> object:
    return runner.invoke(app, ["add", "NEO", "--db-path", str(db_path)])


# -- happy path: add -> list -> export -> remove ---------------------------------


def test_full_workflow(mock_scryfall, db_path, tmp_path) -> None:
    csv_path = tmp_path / "out" / "moxfield.csv"

    # add
    result = add_neo(db_path)
    assert result.exit_code == 0, result.output
    assert "Added" in result.output
    assert "4" in result.output  # 4 generated entries
    assert "3 main + 1 basics" in result.output

    # list
    result = runner.invoke(app, ["list", "--db-path", str(db_path)])
    assert result.exit_code == 0, result.output
    assert "NEO" in result.output
    assert "4" in result.output

    # export
    result = runner.invoke(
        app, ["export", "moxfield", "--db-path", str(db_path), "-o", str(csv_path)]
    )
    assert result.exit_code == 0, result.output
    assert "Exported" in result.output
    assert csv_path.exists()
    lines = csv_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1 + 4  # header + 4 cards

    # remove
    result = runner.invoke(app, ["remove", "NEO", "--db-path", str(db_path), "--yes"])
    assert result.exit_code == 0, result.output
    assert "Removed" in result.output
    assert "4" in result.output

    # list after removal is empty
    result = runner.invoke(app, ["list", "--db-path", str(db_path)])
    assert result.exit_code == 0
    assert "No owned sets" in result.output


# -- preview ---------------------------------------------------------------------


def test_preview_shows_breakdown(mock_scryfall, db_path) -> None:
    result = runner.invoke(app, ["preview", "NEO"])
    assert result.exit_code == 0, result.output
    assert "Kamigawa: Neon Dynasty" in result.output
    assert "Included count:" in result.output


# -- guard paths -----------------------------------------------------------------


def test_add_already_owned_exits_1(mock_scryfall, db_path) -> None:
    assert add_neo(db_path).exit_code == 0
    result = add_neo(db_path)
    assert result.exit_code == 1
    assert "already owned" in result.output


def test_remove_unowned_exits_1(mock_scryfall, db_path) -> None:
    # DB exists (NEO added) but MOM was never owned.
    assert add_neo(db_path).exit_code == 0
    result = runner.invoke(app, ["remove", "MOM", "--db-path", str(db_path), "--yes"])
    assert result.exit_code == 1
    assert "not owned" in result.output


def test_remove_missing_db_exits_1(db_path) -> None:
    result = runner.invoke(app, ["remove", "NEO", "--db-path", str(db_path), "--yes"])
    assert result.exit_code == 1
    assert "No collection database" in result.output


def test_export_missing_db_exits_1(db_path) -> None:
    result = runner.invoke(app, ["export", "moxfield", "--db-path", str(db_path)])
    assert result.exit_code == 1
    assert "No collection database" in result.output


def test_list_missing_db_exits_0(db_path) -> None:
    # list on a missing DB is a friendly no-op, not an error (per spec).
    result = runner.invoke(app, ["list", "--db-path", str(db_path)])
    assert result.exit_code == 0
    assert "No collection database" in result.output


def test_export_empty_collection_exits_1(mock_scryfall, db_path, tmp_path) -> None:
    # DB exists but has no entries: add then remove, leaving an empty collection.
    assert add_neo(db_path).exit_code == 0
    runner.invoke(app, ["remove", "NEO", "--db-path", str(db_path), "--yes"])
    result = runner.invoke(
        app, ["export", "moxfield", "--db-path", str(db_path), "-o", str(tmp_path / "x.csv")]
    )
    assert result.exit_code == 1
    assert "empty" in result.output


def test_add_unknown_set_exits_1(mock_scryfall, db_path) -> None:
    result = runner.invoke(app, ["add", "ZZZ", "--db-path", str(db_path)])
    assert result.exit_code == 1
    assert "No set found" in result.output


def test_preview_unknown_set_exits_1(mock_scryfall) -> None:
    result = runner.invoke(app, ["preview", "ZZZ"])
    assert result.exit_code == 1
    assert "No set found" in result.output


# -- add-multi: multiple set codes -----------------------------------------------


def test_add_multi_all_succeed(mock_scryfall, db_path) -> None:
    result = runner.invoke(app, ["add-multi", "NEO", "MOM", "--db-path", str(db_path)])
    assert result.exit_code == 0, result.output
    assert "Added Kamigawa: Neon Dynasty" in result.output
    assert "Added March of the Machine" in result.output
    assert "2 added." in result.output

    # both sets are now owned
    listing = runner.invoke(app, ["list", "--db-path", str(db_path)])
    assert "NEO" in listing.output
    assert "MOM" in listing.output


def test_add_multi_mixed_skip_and_failure(mock_scryfall, db_path) -> None:
    # NEO is pre-owned (skipped), MOM is new (added), ZZZ is unknown (failed).
    assert add_neo(db_path).exit_code == 0

    result = runner.invoke(app, ["add-multi", "NEO", "MOM", "ZZZ", "--db-path", str(db_path)])
    assert result.exit_code == 1, result.output
    assert "Skipped NEO" in result.output and "already owned" in result.output
    assert "Added March of the Machine" in result.output
    assert "Failed ZZZ" in result.output and "No set found" in result.output
    assert "1 added, 1 skipped, 1 failed." in result.output

    # the one good new set still landed despite the skip and failure
    listing = runner.invoke(app, ["list", "--db-path", str(db_path)])
    assert "MOM" in listing.output


def test_add_multi_deduplicates_repeated_codes(mock_scryfall, db_path) -> None:
    # NEO repeated (and in mixed case) should be added exactly once, not skipped.
    result = runner.invoke(app, ["add-multi", "NEO", "neo", "NEO", "--db-path", str(db_path)])
    assert result.exit_code == 0, result.output
    assert result.output.count("Added Kamigawa: Neon Dynasty") == 1
    assert "already owned" not in result.output
    assert "1 added." in result.output


# -- sync-cards: reference data without ownership (issue #87) --------------------


def test_sync_cards_caches_unowned_set_without_affecting_collection(
    mock_scryfall, db_path, tmp_path
) -> None:
    result = runner.invoke(app, ["sync-cards", "NEO", "--db-path", str(db_path)])
    assert result.exit_code == 0, result.output
    assert "Synced Kamigawa: Neon Dynasty" in result.output
    assert "4 cards cached" in result.output
    assert "1 synced." in result.output

    conn = db.get_connection(db_path)
    try:
        assert db.is_set_owned(conn, "neo") is False
        assert db.count_cached_set_cards(conn, "neo") == 4
        assert len(db.get_cached_set_cards(conn, "NEO")) == 4
        assert conn.execute("SELECT COUNT(*) FROM collection_entries").fetchone()[0] == 0
        assert db.get_export_entries(conn) == []
    finally:
        conn.close()

    listing = runner.invoke(app, ["list", "--db-path", str(db_path)])
    assert listing.exit_code == 0
    assert "No owned sets" in listing.output
    stats_result = runner.invoke(app, ["stats", "--no-remote", "--db-path", str(db_path)])
    assert stats_result.exit_code == 0
    assert "Sets owned" in stats_result.output and "0" in stats_result.output
    assert "Card entries" in stats_result.output and "0" in stats_result.output
    export_result = runner.invoke(
        app,
        [
            "export",
            "moxfield",
            "--db-path",
            str(db_path),
            "-o",
            str(tmp_path / "cached-only.csv"),
        ],
    )
    assert export_result.exit_code == 1
    assert "empty" in export_result.output.lower()


def test_sync_cards_partial_batch_failure_continues(mock_scryfall, db_path) -> None:
    result = runner.invoke(app, ["sync-cards", "ZZZ", "NEO", "--db-path", str(db_path)])
    assert result.exit_code == 1
    assert "Failed ZZZ" in result.output
    assert "Synced Kamigawa: Neon Dynasty" in result.output
    assert "1 synced, 1 failed." in result.output

    conn = db.get_connection(db_path)
    try:
        assert db.count_cached_set_cards(conn, "neo") == 4
        assert db.is_set_owned(conn, "neo") is False
    finally:
        conn.close()


def test_sync_cards_resync_refreshes_fields_and_membership(monkeypatch, neo_cards, db_path) -> None:
    current_cards = list(neo_cards)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/sets/neo":
            return httpx.Response(200, json=NEO_SET)
        if request.url.path == "/cards/search" and request.url.params.get("q") == "set:neo":
            return httpx.Response(200, json={"data": current_cards, "has_more": False})
        return httpx.Response(404, json={"details": "not found"})

    monkeypatch.setattr(scryfall, "_REQUEST_DELAY", 0.0)
    monkeypatch.setattr(
        scryfall,
        "ScryfallClient",
        lambda *a, **k: ScryfallClient(transport=httpx.MockTransport(handler)),
    )

    first = runner.invoke(app, ["sync-cards", "NEO", "--db-path", str(db_path)])
    assert first.exit_code == 0, first.output

    updated = dict(neo_cards[1])
    updated["name"] = "Ancestral Katana (updated)"
    added = dict(neo_cards[1])
    added.update(id="new-printing", name="New Main-Set Card", collector_number="5")
    removed_id = neo_cards[2]["id"]
    current_cards = [
        updated if card["id"] == updated["id"] else card
        for card in neo_cards
        if card["id"] != removed_id
    ]
    current_cards.append(added)

    second = runner.invoke(app, ["sync-cards", "neo", "--db-path", str(db_path)])
    assert second.exit_code == 0, second.output
    assert "1 added, 1 removed" in second.output

    conn = db.get_connection(db_path)
    try:
        cached = db.get_cached_set_cards(conn, "neo")
        by_id = {card["id"]: card for card in cached}
        assert len(cached) == 4
        assert by_id[updated["id"]]["name"] == "Ancestral Katana (updated)"
        assert "new-printing" in by_id
        assert removed_id not in by_id
        assert (
            conn.execute("SELECT 1 FROM cards WHERE scryfall_id = ?", (removed_id,)).fetchone()
            is None
        )
        assert db.is_set_owned(conn, "neo") is False
        assert db.get_export_entries(conn) == []
    finally:
        conn.close()


# -- want-list: exact unowned printings in text/CSV (issue #88) ------------------


def test_want_list_plain_text_is_unowned_copy_safe_and_ordered(
    mock_want_list_scryfall, db_path
) -> None:
    result = runner.invoke(
        app,
        ["want-list", "HOB", "94", "91", "--db-path", str(db_path)],
        color=True,
    )
    assert result.exit_code == 0, result.output
    assert result.output.index("Dori, Bearer of Friends") < result.output.index("Dáin Ironfoot")
    assert "HOB 94" in result.output and "HOB 91" in result.output
    assert "Scryfall: https://scryfall.com/card/hob/94" in result.output
    assert "TCGplayer:" in result.output
    assert "\x1b" not in result.output
    assert result.stderr == ""
    # A cold-cache want list is entirely read-only: it doesn't even create the DB.
    assert not db_path.exists()


def test_want_list_csv_round_trips_unicode_commas_and_order(
    mock_want_list_scryfall, db_path, tmp_path
) -> None:
    output = tmp_path / "lists" / "hob.csv"
    result = runner.invoke(
        app,
        [
            "want-list",
            "HOB",
            "94",
            "91",
            "--db-path",
            str(db_path),
            "--output",
            str(output),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "Wrote 2 card(s)" in result.output
    with output.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        assert reader.fieldnames == wantlist.WANT_LIST_COLUMNS
        rows = list(reader)
    assert [row["Card Name"] for row in rows] == [
        "Dori, Bearer of Friends",
        "Dáin Ironfoot",
    ]
    assert [row["Collector Number"] for row in rows] == ["94", "91"]
    assert not db_path.exists()


def test_want_list_preferences_and_alphanumeric_collector_number(
    mock_want_list_scryfall, db_path
) -> None:
    result = runner.invoke(
        app,
        [
            "want-list",
            "HOB",
            "A-1",
            "--quantity",
            "2",
            "--language",
            "Japanese",
            "--finish",
            "any",
            "--condition",
            "Played",
            "--db-path",
            str(db_path),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "2x Lettered Printing" in result.output
    assert "HOB A-1 | Japanese | any | condition: Played" in result.output


def test_want_list_partial_failure_still_emits_valid_cards(
    mock_want_list_scryfall, db_path
) -> None:
    result = runner.invoke(app, ["want-list", "HOB", "999", "91", "--db-path", str(db_path)])
    assert result.exit_code == 1
    assert "Dáin Ironfoot" in result.stdout
    assert "Unresolved HOB 999" in result.stderr
    assert "1 resolved, 1 unresolved" in result.stderr
    assert "\x1b" not in result.stdout + result.stderr


def test_want_list_partial_failure_writes_valid_csv(
    mock_want_list_scryfall, db_path, tmp_path
) -> None:
    output = tmp_path / "partial.csv"
    result = runner.invoke(
        app,
        [
            "want-list",
            "HOB",
            "94",
            "missing",
            "--db-path",
            str(db_path),
            "-o",
            str(output),
        ],
    )
    assert result.exit_code == 1
    with output.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert [row["Card Name"] for row in rows] == ["Dori, Bearer of Friends"]
    assert "Wrote 1 card(s)" in result.stdout
    assert "1 resolved, 1 unresolved" in result.stderr


def test_want_list_invalid_set_is_actionable_and_writes_no_csv(
    mock_want_list_scryfall, db_path, tmp_path
) -> None:
    output = tmp_path / "invalid.csv"
    result = runner.invoke(
        app,
        ["want-list", "ZZZ", "1", "2", "--db-path", str(db_path), "-o", str(output)],
    )
    assert result.exit_code == 1
    assert "No set found with code ZZZ" in result.output
    assert "mtgsets search ZZZ" in result.output
    assert "0 resolved, 2 unresolved" in result.output
    assert not output.exists()


def test_want_list_uses_cached_unowned_set_offline(monkeypatch, db_path) -> None:
    db.init_db(db_path)
    conn = db.get_connection(db_path)
    try:
        db.replace_cached_set_cards(
            conn,
            set_code="hob",
            set_name="The Hobbit",
            raw_cards=HOB_CARDS,
            synced_at="2026-08-10T00:00:00+00:00",
        )
        conn.commit()
    finally:
        conn.close()

    def unexpected_client(*args, **kwargs):
        raise AssertionError("cached want-list lookup should not open Scryfall")

    monkeypatch.setattr(scryfall, "ScryfallClient", unexpected_client)
    result = runner.invoke(app, ["want-list", "HOB", "94", "91", "--db-path", str(db_path)])
    assert result.exit_code == 0, result.output
    assert result.output.index("Dori, Bearer of Friends") < result.output.index("Dáin Ironfoot")

    conn = db.get_connection(db_path)
    try:
        assert db.is_set_owned(conn, "hob") is False
        assert conn.execute("SELECT COUNT(*) FROM collection_entries").fetchone()[0] == 0
        assert db.get_export_entries(conn) == []
    finally:
        conn.close()


# -- refresh ---------------------------------------------------------------------


def test_refresh_reupdates_owned_set(mock_scryfall, db_path) -> None:
    # Add NEO, then refresh it: the cache is re-fetched and entries regenerated.
    assert add_neo(db_path).exit_code == 0
    result = runner.invoke(app, ["refresh", "NEO", "--db-path", str(db_path)])
    assert result.exit_code == 0, result.output
    assert "Refreshed Kamigawa: Neon Dynasty" in result.output
    assert "cards cached" in result.output
    # Same fixture -> same entry count, so it reports unchanged.
    assert "unchanged" in result.output

    # NEO is still owned with its 4 entries (regeneration didn't drop them).
    listing = runner.invoke(app, ["list", "--db-path", str(db_path)])
    assert "NEO" in listing.output and "4" in listing.output


def test_refresh_unowned_set_exits_1(mock_scryfall, db_path) -> None:
    # MOM is a known set but never added: refresh should refuse and point to add.
    assert add_neo(db_path).exit_code == 0
    result = runner.invoke(app, ["refresh", "MOM", "--db-path", str(db_path)])
    assert result.exit_code == 1
    assert "is not owned" in result.output


def test_refresh_missing_db_exits_1(db_path) -> None:
    result = runner.invoke(app, ["refresh", "NEO", "--db-path", str(db_path)])
    assert result.exit_code == 1
    assert "No collection database" in result.output


def test_refresh_unknown_set_exits_1(mock_scryfall, db_path) -> None:
    # Owned set whose code Scryfall no longer resolves: _load_set reports it.
    runner.invoke(app, ["init", "--db-path", str(db_path)])
    # Force an owned row for an unknown code, then try to refresh it.
    conn = scryfall_owned_row(db_path, "zzz", "Gone Set")
    conn.close()
    result = runner.invoke(app, ["refresh", "ZZZ", "--db-path", str(db_path)])
    assert result.exit_code == 1
    assert "No set found" in result.output


# -- refresh --all (issue #70) ---------------------------------------------------


def test_refresh_all_refreshes_every_owned_set(mock_scryfall, db_path) -> None:
    assert add_neo(db_path).exit_code == 0
    assert runner.invoke(app, ["add", "MOM", "--db-path", str(db_path)]).exit_code == 0

    result = runner.invoke(app, ["refresh", "--all", "--db-path", str(db_path)])
    assert result.exit_code == 0, result.output
    assert "Refreshed Kamigawa: Neon Dynasty" in result.output
    assert "Refreshed March of the Machine" in result.output
    assert "2 refreshed" in result.output


def test_refresh_all_continues_past_a_failure(mock_scryfall, db_path) -> None:
    # A recorded-but-unresolvable set sorted FIRST (newest), then a good set: the
    # failure must not abort the run, so the good set still refreshes after it.
    assert add_neo(db_path).exit_code == 0
    conn = db.get_connection(db_path)
    db.insert_owned_set(
        conn, set_code="zzz", set_name="Gone Set", added_at="2099-01-01T00:00:00+00:00"
    )
    conn.commit()
    conn.close()

    result = runner.invoke(app, ["refresh", "--all", "--db-path", str(db_path)])
    assert result.exit_code == 1  # something failed
    assert "Failed ZZZ" in result.output
    assert "Refreshed Kamigawa: Neon Dynasty" in result.output  # ran despite ZZZ failing
    assert "1 refreshed" in result.output and "1 failed" in result.output


def test_refresh_all_preserves_manual_singles(mock_scryfall, db_path, tmp_path) -> None:
    assert add_neo(db_path).exit_code == 0
    assert runner.invoke(app, ["add-card", "NEO", "2", "--db-path", str(db_path)]).exit_code == 0
    assert len(export_lines(db_path, tmp_path)) == 5  # 4 full_set + 1 manual

    result = runner.invoke(app, ["refresh", "--all", "--db-path", str(db_path)])
    assert result.exit_code == 0, result.output
    # The manual single survives the bulk refresh (still 5 rows, one untagged).
    rows = export_lines(db_path, tmp_path)
    assert len(rows) == 5
    assert any("Full Set" not in r for r in rows)


def test_refresh_all_no_owned_sets_is_friendly(db_path) -> None:
    runner.invoke(app, ["init", "--db-path", str(db_path)])
    result = runner.invoke(app, ["refresh", "--all", "--db-path", str(db_path)])
    assert result.exit_code == 0
    assert "No owned sets to refresh" in result.output


def test_refresh_all_missing_db_exits_1(db_path) -> None:
    result = runner.invoke(app, ["refresh", "--all", "--db-path", str(db_path)])
    assert result.exit_code == 1
    assert "No collection database" in result.output


def test_refresh_rejects_set_code_with_all(mock_scryfall, db_path) -> None:
    assert add_neo(db_path).exit_code == 0
    result = runner.invoke(app, ["refresh", "NEO", "--all", "--db-path", str(db_path)])
    assert result.exit_code == 1
    assert "not both" in result.output


def test_refresh_without_set_code_or_all_exits_1(db_path) -> None:
    runner.invoke(app, ["init", "--db-path", str(db_path)])
    result = runner.invoke(app, ["refresh", "--db-path", str(db_path)])
    assert result.exit_code == 1
    assert "Specify a set code" in result.output


def scryfall_owned_row(db_path, code: str, name: str):
    """Insert a bare owned_sets row directly, for guard-path tests."""
    from mtgsets import db as _db

    conn = _db.get_connection(db_path)
    _db.insert_owned_set(conn, set_code=code, set_name=name, added_at="2026-01-01T00:00:00+00:00")
    conn.commit()
    return conn


# -- add-card / remove-card (manual singles) -------------------------------------


def export_lines(db_path, tmp_path) -> list[str]:
    """Export the FULL collection and return the CSV's data rows (no header).

    Uses ``--all`` so the snapshot reflects the whole collection on every call,
    independent of the incremental delta state (issue #76).
    """
    csv_path = tmp_path / "out.csv"
    result = runner.invoke(
        app, ["export", "moxfield", "--all", "--db-path", str(db_path), "-o", str(csv_path)]
    )
    assert result.exit_code == 0, result.output
    return csv_path.read_text(encoding="utf-8").splitlines()[1:]


def test_add_card_creates_manual_single(mock_scryfall, db_path, tmp_path) -> None:
    # Collector #2 in the NEO fixture is "Ao, the Dawn Sky".
    result = runner.invoke(
        app, ["add-card", "NEO", "2", "-q", "2", "--foil", "--db-path", str(db_path)]
    )
    assert result.exit_code == 0, result.output
    assert "Added" in result.output and "Ao, the Dawn Sky" in result.output
    assert "quantity now 2" in result.output

    # Exported as a manual single: foil, count 2, and NO "Full Set" tag.
    rows = export_lines(db_path, tmp_path)
    assert len(rows) == 1
    assert "Ao, the Dawn Sky" in rows[0]
    assert "foil" in rows[0]
    assert rows[0].startswith("2,")  # Count column
    assert "Full Set" not in rows[0]


def test_add_card_stacks_quantity(mock_scryfall, db_path) -> None:
    assert runner.invoke(app, ["add-card", "NEO", "2", "--db-path", str(db_path)]).exit_code == 0
    result = runner.invoke(app, ["add-card", "NEO", "2", "--db-path", str(db_path)])
    assert result.exit_code == 0, result.output
    assert "Updated" in result.output and "quantity now 2" in result.output


def test_add_card_coexists_with_full_set(mock_scryfall, db_path, tmp_path) -> None:
    # NEO full set includes card #2; adding it again as a manual single is allowed
    # and flagged, and both rows survive.
    assert add_neo(db_path).exit_code == 0
    result = runner.invoke(app, ["add-card", "NEO", "2", "--db-path", str(db_path)])
    assert result.exit_code == 0, result.output
    assert "also own this card via a full set" in result.output

    # 4 generated full-set rows + 1 manual = 5 exported rows.
    assert len(export_lines(db_path, tmp_path)) == 5


def test_add_card_unknown_exits_1(mock_scryfall, db_path) -> None:
    result = runner.invoke(app, ["add-card", "NEO", "9999", "--db-path", str(db_path)])
    assert result.exit_code == 1
    assert "No card found" in result.output


def test_remove_card_deletes_manual_single(mock_scryfall, db_path) -> None:
    assert runner.invoke(app, ["add-card", "NEO", "2", "--db-path", str(db_path)]).exit_code == 0
    result = runner.invoke(app, ["remove-card", "NEO", "2", "--db-path", str(db_path)])
    assert result.exit_code == 0, result.output
    assert "Removed" in result.output
    # Removing again finds nothing.
    again = runner.invoke(app, ["remove-card", "NEO", "2", "--db-path", str(db_path)])
    assert again.exit_code == 1
    assert "No manual single found" in again.output


def test_remove_card_leaves_full_set_untouched(mock_scryfall, db_path, tmp_path) -> None:
    # Manual single + full set both present; remove-card drops only the manual one.
    assert add_neo(db_path).exit_code == 0
    assert runner.invoke(app, ["add-card", "NEO", "2", "--db-path", str(db_path)]).exit_code == 0
    assert len(export_lines(db_path, tmp_path)) == 5  # 4 full_set + 1 manual

    result = runner.invoke(app, ["remove-card", "NEO", "2", "--db-path", str(db_path)])
    assert result.exit_code == 0, result.output
    # Back to the 4 generated full-set rows — the set is intact.
    assert len(export_lines(db_path, tmp_path)) == 4


def test_remove_card_missing_db_exits_1(db_path) -> None:
    result = runner.invoke(app, ["remove-card", "NEO", "2", "--db-path", str(db_path)])
    assert result.exit_code == 1
    assert "nothing to remove" in result.output


# -- override-card ---------------------------------------------------------------


def test_override_card_replaces_full_set_values_on_export(mock_scryfall, db_path, tmp_path) -> None:
    # NEO #2 ("Ao, the Dawn Sky") is part of the owned full set; override it.
    assert add_neo(db_path).exit_code == 0
    result = runner.invoke(
        app,
        [
            "override-card",
            "NEO",
            "2",
            "-q",
            "3",
            "--foil",
            "--condition",
            "Played",
            "--db-path",
            str(db_path),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "Set override" in result.output and "Ao, the Dawn Sky" in result.output

    # Still 4 exported rows (no duplicate) — but #2 now carries the override values
    # and keeps its Full Set tag.
    rows = export_lines(db_path, tmp_path)
    assert len(rows) == 4
    ao = next(r for r in rows if "Ao, the Dawn Sky" in r)
    assert ao.startswith("3,")  # Count
    assert "foil" in ao and "Played" in ao and "Full Set: NEO" in ao


def test_override_card_rerun_updates(mock_scryfall, db_path) -> None:
    assert add_neo(db_path).exit_code == 0
    assert (
        runner.invoke(
            app, ["override-card", "NEO", "2", "--foil", "--db-path", str(db_path)]
        ).exit_code
        == 0
    )
    result = runner.invoke(app, ["override-card", "NEO", "2", "-q", "5", "--db-path", str(db_path)])
    assert result.exit_code == 0, result.output
    assert "Updated override" in result.output


def test_override_card_clear_reverts_to_defaults(mock_scryfall, db_path, tmp_path) -> None:
    assert add_neo(db_path).exit_code == 0
    assert (
        runner.invoke(
            app, ["override-card", "NEO", "2", "--foil", "--db-path", str(db_path)]
        ).exit_code
        == 0
    )

    result = runner.invoke(app, ["override-card", "NEO", "2", "--clear", "--db-path", str(db_path)])
    assert result.exit_code == 0, result.output
    assert "Cleared" in result.output

    # Back to the generated defaults: 4 rows, #2 nonfoil again.
    rows = export_lines(db_path, tmp_path)
    assert len(rows) == 4
    ao = next(r for r in rows if "Ao, the Dawn Sky" in r)
    assert "foil" not in ao

    # Clearing again is a no-op that reports nothing to clear.
    again = runner.invoke(app, ["override-card", "NEO", "2", "--clear", "--db-path", str(db_path)])
    assert again.exit_code == 1
    assert "No override found" in again.output


def test_override_card_not_in_full_set_exits_1(mock_scryfall, db_path) -> None:
    # The DB exists (a manual single was added) but NEO is not owned as a full set,
    # so #2 has no generated full_set entry to override.
    assert runner.invoke(app, ["add-card", "NEO", "2", "--db-path", str(db_path)]).exit_code == 0
    result = runner.invoke(app, ["override-card", "NEO", "2", "--db-path", str(db_path)])
    assert result.exit_code == 1
    assert "isn't part of an owned full set" in result.output


def test_override_card_missing_db_exits_1(db_path) -> None:
    result = runner.invoke(app, ["override-card", "NEO", "2", "--db-path", str(db_path)])
    assert result.exit_code == 1
    assert "No collection database yet" in result.output


# -- import-csv ------------------------------------------------------------------

MOX_HEADER = "Count,Tradelist Count,Name,Edition,Condition,Language,Foil,Tags,Collector Number"


def write_csv(tmp_path, *rows: str):
    path = tmp_path / "in.csv"
    path.write_text("\n".join([MOX_HEADER, *rows]) + "\n", encoding="utf-8")
    return path


def test_import_csv_adds_manual_singles(mock_scryfall, db_path, tmp_path) -> None:
    csv_path = write_csv(
        tmp_path,
        "1,0,Ao the Dawn Sky,NEO,Near Mint,English,,,2",
        "2,0,Plains,NEO,Near Mint,English,foil,,283",
    )
    result = runner.invoke(app, ["import-csv", str(csv_path), "--db-path", str(db_path)])
    assert result.exit_code == 0, result.output
    assert "Imported 2 card(s)" in result.output
    # Both land as manual singles (no Full Set tag); 3 total copies.
    rows = export_lines(db_path, tmp_path)
    assert len(rows) == 2
    assert all("Full Set" not in r for r in rows)


def test_import_csv_skips_full_set_rows(mock_scryfall, db_path, tmp_path) -> None:
    csv_path = write_csv(
        tmp_path,
        '1,0,"Boseiju, Who Endures",NEO,Near Mint,English,,Full Set: NEO,2',
    )
    result = runner.invoke(app, ["import-csv", str(csv_path), "--db-path", str(db_path)])
    assert result.exit_code == 0, result.output
    assert "Imported 0 card(s)" in result.output
    assert "Skipped 1 full-set row" in result.output


def test_import_csv_reports_unresolved_but_continues(mock_scryfall, db_path, tmp_path) -> None:
    csv_path = write_csv(
        tmp_path,
        "1,0,Ao the Dawn Sky,NEO,Near Mint,English,,,2",  # resolves
        "1,0,Phantom,NEO,Near Mint,English,,,9999",  # 404
    )
    result = runner.invoke(app, ["import-csv", str(csv_path), "--db-path", str(db_path)])
    assert result.exit_code == 1  # unresolved rows -> nonzero
    assert "Imported 1 card(s)" in result.output
    assert "Unresolved 1 row" in result.output and "NEO 9999" in result.output
    # The good row still imported despite the bad one.
    assert len(export_lines(db_path, tmp_path)) == 1


def test_import_csv_reports_malformed_rows(mock_scryfall, db_path, tmp_path) -> None:
    csv_path = write_csv(
        tmp_path,
        "1,0,Ao the Dawn Sky,NEO,Near Mint,English,,,2",  # ok
        "1,0,Nameless,NEO,Near Mint,English,,,",  # missing collector number
    )
    result = runner.invoke(app, ["import-csv", str(csv_path), "--db-path", str(db_path)])
    assert result.exit_code == 1
    assert "Imported 1 card(s)" in result.output
    assert "Malformed 1 row" in result.output


def test_import_csv_round_trips_manual_singles(mock_scryfall, db_path, tmp_path) -> None:
    # Add a manual single, export it, then import that CSV into a fresh db.
    assert (
        runner.invoke(app, ["add-card", "NEO", "2", "-q", "2", "--db-path", str(db_path)]).exit_code
        == 0
    )
    csv_path = tmp_path / "export.csv"
    assert (
        runner.invoke(
            app, ["export", "moxfield", "--db-path", str(db_path), "-o", str(csv_path)]
        ).exit_code
        == 0
    )

    fresh = tmp_path / "fresh.db"
    result = runner.invoke(app, ["import-csv", str(csv_path), "--db-path", str(fresh)])
    assert result.exit_code == 0, result.output
    assert "Imported 1 card(s) (2 cop" in result.output
    rows = export_lines(fresh, tmp_path)
    assert len(rows) == 1 and rows[0].startswith("2,")  # quantity preserved


def test_import_csv_missing_file_exits_1(db_path, tmp_path) -> None:
    result = runner.invoke(
        app, ["import-csv", str(tmp_path / "nope.csv"), "--db-path", str(db_path)]
    )
    assert result.exit_code == 1
    assert "No such file" in result.output


# -- show ------------------------------------------------------------------------


def test_show_lists_set_contents(mock_scryfall, db_path) -> None:
    assert add_neo(db_path).exit_code == 0
    result = runner.invoke(app, ["show", "NEO", "--db-path", str(db_path)])
    assert result.exit_code == 0, result.output
    # Header + summary.
    assert "Set:" in result.output and "Kamigawa: Neon Dynasty" in result.output
    assert "Cards" in result.output and "3 main + 1 basics" in result.output
    # Composition + the card listing (the "contents").
    assert "By rarity" in result.output
    assert "NEO cards" in result.output
    assert "Plains" in result.output  # the basic land, only listed in the card table
    assert "Mythic" in result.output  # Ao, the Dawn Sky is mythic


def test_show_no_cards_prints_summary_only(mock_scryfall, db_path) -> None:
    assert add_neo(db_path).exit_code == 0
    result = runner.invoke(app, ["show", "NEO", "--no-cards", "--db-path", str(db_path)])
    assert result.exit_code == 0, result.output
    assert "By rarity" in result.output  # summary still shown
    assert "Plains" not in result.output  # the per-card table is suppressed


def test_show_unowned_set_exits_1(mock_scryfall, db_path) -> None:
    assert add_neo(db_path).exit_code == 0
    result = runner.invoke(app, ["show", "MOM", "--db-path", str(db_path)])
    assert result.exit_code == 1
    assert "is not owned" in result.output


def test_show_missing_db_exits_1(db_path) -> None:
    result = runner.invoke(app, ["show", "NEO", "--db-path", str(db_path)])
    assert result.exit_code == 1
    assert "No collection database" in result.output


# -- search ----------------------------------------------------------------------


def test_search_lists_matches(mock_scryfall, db_path) -> None:
    result = runner.invoke(app, ["search", "neo", "--db-path", str(db_path)])
    assert result.exit_code == 0, result.output
    assert "NEO" in result.output


# -- set-list cache: TTL, manual refresh, offline (issue #68) --------------------


def counting_get_sets(monkeypatch, sets):
    """Patch ScryfallClient.get_sets to a call-counter returning ``sets``.

    Returns the mutable counter dict so a test can assert how many real fetches the
    cache let through.
    """
    calls = {"n": 0}

    def _get(self):
        calls["n"] += 1
        return sets

    monkeypatch.setattr(scryfall.ScryfallClient, "get_sets", _get)
    return calls


def test_search_serves_second_call_from_cache(monkeypatch, db_path) -> None:
    calls = counting_get_sets(monkeypatch, [NEO_SET, MOM_SET])
    assert runner.invoke(app, ["search", "neo", "--db-path", str(db_path)]).exit_code == 0
    assert calls["n"] == 1  # cold cache -> one fetch
    # Second search within the TTL is served from the cache, no new fetch.
    result = runner.invoke(app, ["search", "mom", "--db-path", str(db_path)])
    assert result.exit_code == 0, result.output
    assert "MOM" in result.output
    assert calls["n"] == 1


def test_search_refresh_sets_forces_fetch(monkeypatch, db_path) -> None:
    calls = counting_get_sets(monkeypatch, [NEO_SET, MOM_SET])
    assert runner.invoke(app, ["search", "neo", "--db-path", str(db_path)]).exit_code == 0
    assert calls["n"] == 1
    # --refresh-sets bypasses the fresh cache and re-fetches.
    result = runner.invoke(app, ["search", "neo", "--refresh-sets", "--db-path", str(db_path)])
    assert result.exit_code == 0, result.output
    assert calls["n"] == 2


def test_search_refetches_when_cache_stale(monkeypatch, db_path) -> None:
    calls = counting_get_sets(monkeypatch, [NEO_SET, MOM_SET])
    assert runner.invoke(app, ["search", "neo", "--db-path", str(db_path)]).exit_code == 0
    assert calls["n"] == 1
    # Age the cache past the 24h TTL by rewriting its timestamp directly.
    conn = db.get_connection(db_path)
    conn.execute("UPDATE scryfall_sets SET fetched_at = ?", ("2000-01-01T00:00:00+00:00",))
    conn.commit()
    conn.close()
    assert runner.invoke(app, ["search", "neo", "--db-path", str(db_path)]).exit_code == 0
    assert calls["n"] == 2  # stale -> re-fetch


def test_search_warm_cache_works_offline(monkeypatch, db_path) -> None:
    counting_get_sets(monkeypatch, [NEO_SET, MOM_SET])
    assert runner.invoke(app, ["search", "neo", "--db-path", str(db_path)]).exit_code == 0

    # Network now down: a fresh cache means get_sets is never called, so it still works.
    def boom(self):
        raise scryfall.ScryfallError("network down")

    monkeypatch.setattr(scryfall.ScryfallClient, "get_sets", boom)
    result = runner.invoke(app, ["search", "neo", "--db-path", str(db_path)])
    assert result.exit_code == 0, result.output
    assert "NEO" in result.output


def test_search_stale_cache_falls_back_when_offline(monkeypatch, db_path) -> None:
    counting_get_sets(monkeypatch, [NEO_SET, MOM_SET])
    assert runner.invoke(app, ["search", "neo", "--db-path", str(db_path)]).exit_code == 0
    # Age the cache, then drop the network: the stale snapshot is used, not an error.
    conn = db.get_connection(db_path)
    conn.execute("UPDATE scryfall_sets SET fetched_at = ?", ("2000-01-01T00:00:00+00:00",))
    conn.commit()
    conn.close()

    def boom(self):
        raise scryfall.ScryfallError("network down")

    monkeypatch.setattr(scryfall.ScryfallClient, "get_sets", boom)
    result = runner.invoke(app, ["search", "neo", "--db-path", str(db_path)])
    assert result.exit_code == 0, result.output
    assert "NEO" in result.output


def test_search_cold_cache_offline_errors(monkeypatch, db_path) -> None:
    # No cache and no network -> a clean failure (nothing to fall back on).
    def boom(self):
        raise scryfall.ScryfallError("network down")

    monkeypatch.setattr(scryfall.ScryfallClient, "get_sets", boom)
    result = runner.invoke(app, ["search", "neo", "--db-path", str(db_path)])
    assert result.exit_code == 1
    assert "Scryfall request failed" in result.output


def test_stats_uses_cached_set_list(monkeypatch, db_path) -> None:
    # stats shares the same cache: warming it via search means stats needs no fetch.
    # Seed an owned set directly so this test drives only the set-list cache path
    # (counting get_sets needs the real client class, which mock_scryfall would shadow).
    db.init_db(db_path)
    conn = db.get_connection(db_path)
    db.insert_owned_set(
        conn, set_code="neo", set_name="Kamigawa: Neon Dynasty", added_at="2024-01-01"
    )
    conn.commit()
    conn.close()

    calls = counting_get_sets(monkeypatch, [NEO_SET, MOM_SET])
    assert runner.invoke(app, ["search", "neo", "--db-path", str(db_path)]).exit_code == 0
    assert calls["n"] == 1
    result = runner.invoke(app, ["stats", "--db-path", str(db_path)])
    assert result.exit_code == 0, result.output
    assert "1 / 2 core+expansion" in result.output
    assert calls["n"] == 1  # served from the cache search warmed


# -- stats -----------------------------------------------------------------------


def test_stats_shows_owned_vs_total(mock_scryfall, db_path) -> None:
    # NEO owned; the /sets mock knows two core/expansion sets (NEO, MOM).
    assert add_neo(db_path).exit_code == 0
    result = runner.invoke(app, ["stats", "--db-path", str(db_path)])
    assert result.exit_code == 0, result.output
    assert "Sets owned" in result.output
    assert "1 / 2 core+expansion" in result.output
    assert "(50.0%)" in result.output
    # NEO generates 4 card entries from the fixture (plain, legendary, DFC, basic).
    assert "Card entries" in result.output and "4" in result.output


def test_stats_no_remote_omits_total(mock_scryfall, db_path) -> None:
    assert add_neo(db_path).exit_code == 0
    result = runner.invoke(app, ["stats", "--no-remote", "--db-path", str(db_path)])
    assert result.exit_code == 0, result.output
    assert "Sets owned" in result.output
    assert "core+expansion" not in result.output  # no denominator without Scryfall


def test_stats_requires_database(db_path) -> None:
    result = runner.invoke(app, ["stats", "--db-path", str(db_path)])
    assert result.exit_code == 1
    assert "No collection database yet" in result.output


def test_stats_degrades_when_scryfall_unreachable(monkeypatch, db_path) -> None:
    # DB present (init), but Scryfall errors: owned totals still print, no denominator.
    runner.invoke(app, ["init", "--db-path", str(db_path)])

    def boom(*a, **k):
        raise scryfall.ScryfallError("network down")

    monkeypatch.setattr(scryfall.ScryfallClient, "get_sets", boom)
    result = runner.invoke(app, ["stats", "--db-path", str(db_path)])
    assert result.exit_code == 0, result.output
    assert "could not reach Scryfall" in result.output
    assert "Sets owned" in result.output


# -- stats breakdown flags (issue #53) -------------------------------------------


def test_stats_rarity_and_types_sections(mock_scryfall, db_path) -> None:
    assert add_neo(db_path).exit_code == 0
    result = runner.invoke(app, ["stats", "--rarity", "--types", "--db-path", str(db_path)])
    assert result.exit_code == 0, result.output
    assert "By rarity" in result.output
    assert "By type" in result.output
    # The NEO fixture has a basic land (Land) and the plain card is a creature/etc.
    assert "Land" in result.output


def test_stats_all_runs_every_section(mock_scryfall, db_path) -> None:
    assert add_neo(db_path).exit_code == 0
    result = runner.invoke(app, ["stats", "--all", "--db-path", str(db_path)])
    assert result.exit_code == 0, result.output
    for header in ("By rarity", "By color", "By type", "Mana curve", "Progress"):
        assert header in result.output, header
    # --value reads cached prices (no live fetch).
    assert "Fetching current prices" not in result.output
    assert "Estimated value" in result.output
    # Progress sees NEO as newest/oldest owned.
    assert "Newest owned" in result.output and "NEO" in result.output


def test_stats_value_uses_cached_prices_offline(mock_scryfall, db_path) -> None:
    # --value reads prices from the cached card data, so it works under --no-remote
    # and makes no network call (issue #58).
    assert add_neo(db_path).exit_code == 0
    result = runner.invoke(app, ["stats", "--value", "--no-remote", "--db-path", str(db_path)])
    assert result.exit_code == 0, result.output
    assert "Estimated value" in result.output
    assert "Fetching current prices" not in result.output


def test_stats_progress_skipped_with_no_remote(mock_scryfall, db_path) -> None:
    assert add_neo(db_path).exit_code == 0
    result = runner.invoke(app, ["stats", "--progress", "--no-remote", "--db-path", str(db_path)])
    assert result.exit_code == 0, result.output
    assert "needs Scryfall" in result.output


# -- stats --year (issue #55) ----------------------------------------------------


def test_stats_year_lists_owned_and_missing(mock_scryfall, db_path) -> None:
    # Own NEO (2022); the mock knows MOM (2023). 2023 has MOM owned by nobody here.
    assert add_neo(db_path).exit_code == 0
    result = runner.invoke(app, ["stats", "--year", "2022", "--db-path", str(db_path)])
    assert result.exit_code == 0, result.output
    assert "2022 sets" in result.output
    assert "1 / 1 owned" in result.output
    assert "Owned:" in result.output and "NEO" in result.output


def test_stats_year_shows_missing_sets(mock_scryfall, db_path) -> None:
    assert add_neo(db_path).exit_code == 0
    result = runner.invoke(app, ["stats", "--year", "2023", "--db-path", str(db_path)])
    assert result.exit_code == 0, result.output
    assert "2023 sets" in result.output
    assert "0 / 1 owned" in result.output
    assert "Missing:" in result.output and "MOM" in result.output


def test_stats_year_empty_when_no_releases(mock_scryfall, db_path) -> None:
    assert add_neo(db_path).exit_code == 0
    result = runner.invoke(app, ["stats", "--year", "1999", "--db-path", str(db_path)])
    assert result.exit_code == 0, result.output
    assert "No core/expansion sets released in 1999" in result.output


def test_stats_year_skipped_with_no_remote(mock_scryfall, db_path) -> None:
    assert add_neo(db_path).exit_code == 0
    result = runner.invoke(
        app, ["stats", "--year", "2022", "--no-remote", "--db-path", str(db_path)]
    )
    assert result.exit_code == 0, result.output
    assert "2022 sets" in result.output and "needs Scryfall" in result.output


def test_stats_year_lists_unreleased_sets_as_upcoming(monkeypatch, db_path) -> None:
    # A far-future-dated set (issue #57): it must show under Upcoming, never Missing,
    # and must not count toward the year's owned/total.
    runner.invoke(app, ["init", "--db-path", str(db_path)])
    future = {
        "code": "fut",
        "name": "Future Expansion",
        "set_type": "expansion",
        "digital": False,
        "released_at": "2099-12-31",
    }
    monkeypatch.setattr(scryfall.ScryfallClient, "get_sets", lambda self: [future])
    result = runner.invoke(app, ["stats", "--year", "2099", "--db-path", str(db_path)])
    assert result.exit_code == 0, result.output
    assert "Upcoming" in result.output and "FUT" in result.output
    assert "Missing:" not in result.output
    # Nothing is out yet in 2099, so the total is empty.
    assert "No core/expansion sets released in 2099" in result.output


# -- stats --from-year/--to-year (issue #77) -------------------------------------


def test_stats_range_lists_each_year_and_rollup(mock_scryfall, db_path) -> None:
    # Own NEO (2022); the mock also knows MOM (2023). The span shows both years
    # plus a roll-up: 1 of 2 sets owned across 2022-2023.
    assert add_neo(db_path).exit_code == 0
    result = runner.invoke(
        app, ["stats", "--from-year", "2022", "--to-year", "2023", "--db-path", str(db_path)]
    )
    assert result.exit_code == 0, result.output
    assert "2022 sets" in result.output and "2023 sets" in result.output
    assert "NEO" in result.output and "MOM" in result.output
    assert "2022–2023 total" in result.output
    assert "1 / 2 owned" in result.output and "50.0%" in result.output


def test_stats_range_requires_both_bounds(mock_scryfall, db_path) -> None:
    assert add_neo(db_path).exit_code == 0
    result = runner.invoke(app, ["stats", "--from-year", "2022", "--db-path", str(db_path)])
    assert result.exit_code == 1
    assert "must be used together" in result.output


def test_stats_range_rejects_inverted_bounds(mock_scryfall, db_path) -> None:
    assert add_neo(db_path).exit_code == 0
    result = runner.invoke(
        app,
        ["stats", "--from-year", "2023", "--to-year", "2022", "--db-path", str(db_path)],
    )
    assert result.exit_code == 1
    assert "must not be after" in result.output


def test_stats_range_skipped_with_no_remote(mock_scryfall, db_path) -> None:
    assert add_neo(db_path).exit_code == 0
    result = runner.invoke(
        app,
        [
            "stats",
            "--from-year",
            "2022",
            "--to-year",
            "2023",
            "--no-remote",
            "--db-path",
            str(db_path),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "2022–2023 sets" in result.output and "needs Scryfall" in result.output


# -- export json (issue #71) -----------------------------------------------------


def test_export_json_writes_snapshot(mock_scryfall, db_path, tmp_path) -> None:
    assert add_neo(db_path).exit_code == 0
    out = tmp_path / "collection.json"
    result = runner.invoke(app, ["export", "json", "--db-path", str(db_path), "-o", str(out)])
    assert result.exit_code == 0, result.output
    assert "Exported" in result.output and "1 set(s)" in result.output
    assert out.exists()

    doc = json.loads(out.read_text(encoding="utf-8"))
    assert doc["version"] == 1
    assert [s["set_code"] for s in doc["owned_sets"]] == ["neo"]
    # NEO generates 4 entries from the fixture; all tagged as full_set.
    assert len(doc["entries"]) == 4
    assert {e["source_type"] for e in doc["entries"]} == {"full_set"}


def test_export_json_includes_manual_and_override(mock_scryfall, db_path, tmp_path) -> None:
    assert add_neo(db_path).exit_code == 0
    assert runner.invoke(app, ["add-card", "NEO", "2", "--db-path", str(db_path)]).exit_code == 0
    assert (
        runner.invoke(
            app, ["override-card", "NEO", "2", "--foil", "--db-path", str(db_path)]
        ).exit_code
        == 0
    )
    out = tmp_path / "collection.json"
    assert (
        runner.invoke(app, ["export", "json", "--db-path", str(db_path), "-o", str(out)]).exit_code
        == 0
    )

    doc = json.loads(out.read_text(encoding="utf-8"))
    types = [e["source_type"] for e in doc["entries"]]
    # 3 untouched full_set + 1 override (full_set #2 suppressed) + 1 manual single.
    assert types.count("override") == 1
    assert types.count("manual") == 1
    assert types.count("full_set") == 3


def test_export_json_missing_db_exits_1(db_path) -> None:
    result = runner.invoke(app, ["export", "json", "--db-path", str(db_path)])
    assert result.exit_code == 1
    assert "No collection database" in result.output


def test_export_json_empty_collection_exits_1(mock_scryfall, db_path, tmp_path) -> None:
    assert add_neo(db_path).exit_code == 0
    runner.invoke(app, ["remove", "NEO", "--db-path", str(db_path), "--yes"])
    result = runner.invoke(
        app, ["export", "json", "--db-path", str(db_path), "-o", str(tmp_path / "x.json")]
    )
    assert result.exit_code == 1
    assert "empty" in result.output


# -- export moxfield: incremental delta (issue #76) ------------------------------


def moxfield_export(db_path, tmp_path, *args):
    """Run `export moxfield <args>`; return (result, csv_path)."""
    out = tmp_path / "mox.csv"
    result = runner.invoke(
        app, ["export", "moxfield", *args, "--db-path", str(db_path), "-o", str(out)]
    )
    return result, out


def data_rows(csv_path) -> list[str]:
    return csv_path.read_text(encoding="utf-8").splitlines()[1:]


def test_export_delta_default_then_idempotent(mock_scryfall, db_path, tmp_path) -> None:
    assert add_neo(db_path).exit_code == 0

    # First default export emits all 4 (none exported yet) and stamps them.
    result, out = moxfield_export(db_path, tmp_path)
    assert result.exit_code == 0, result.output
    assert "Exported" in result.output and "4 new cards" in result.output
    assert len(data_rows(out)) == 4

    # Immediate rerun finds nothing new (idempotent), exit 0, friendly message.
    result, _ = moxfield_export(db_path, tmp_path)
    assert result.exit_code == 0, result.output
    assert "Nothing new to export" in result.output


def test_export_delta_emits_only_new_after_adding_a_set(mock_scryfall, db_path, tmp_path) -> None:
    assert add_neo(db_path).exit_code == 0
    assert moxfield_export(db_path, tmp_path)[0].exit_code == 0  # stamps NEO's 4

    assert runner.invoke(app, ["add", "MOM", "--db-path", str(db_path)]).exit_code == 0
    result, out = moxfield_export(db_path, tmp_path)
    assert result.exit_code == 0, result.output
    rows = data_rows(out)
    # Only MOM's 4 new entries — tagged Full Set: MOM, not NEO.
    assert len(rows) == 4
    assert all("Full Set: MOM" in r for r in rows)


def test_export_all_redumps_regardless_of_state(mock_scryfall, db_path, tmp_path) -> None:
    assert add_neo(db_path).exit_code == 0
    assert moxfield_export(db_path, tmp_path)[0].exit_code == 0  # delta stamps the 4

    result, out = moxfield_export(db_path, tmp_path, "--all")
    assert result.exit_code == 0, result.output
    # Full dump even though everything was already exported; "new" not in the message.
    assert "4 cards" in result.output and "new cards" not in result.output
    assert len(data_rows(out)) == 4


def test_export_set_filter_exports_one_set_on_demand(mock_scryfall, db_path, tmp_path) -> None:
    assert add_neo(db_path).exit_code == 0
    assert moxfield_export(db_path, tmp_path)[0].exit_code == 0  # stamp NEO

    # --set re-exports NEO regardless of its exported state.
    result, out = moxfield_export(db_path, tmp_path, "--set", "NEO")
    assert result.exit_code == 0, result.output
    rows = data_rows(out)
    assert len(rows) == 4
    assert all("Full Set: NEO" in r for r in rows)


def test_export_set_unowned_exits_1(mock_scryfall, db_path, tmp_path) -> None:
    assert add_neo(db_path).exit_code == 0
    result, _ = moxfield_export(db_path, tmp_path, "--set", "MOM")
    assert result.exit_code == 1
    assert "Nothing to export for MOM" in result.output


def test_export_delta_includes_manual_singles(mock_scryfall, db_path, tmp_path) -> None:
    assert add_neo(db_path).exit_code == 0
    assert moxfield_export(db_path, tmp_path)[0].exit_code == 0  # stamp the 4 full_set

    # A manual single added afterwards is a brand-new, un-exported entry.
    assert runner.invoke(app, ["add-card", "NEO", "2", "--db-path", str(db_path)]).exit_code == 0
    result, out = moxfield_export(db_path, tmp_path)
    assert result.exit_code == 0, result.output
    rows = data_rows(out)
    assert len(rows) == 1  # just the manual single
    assert "Full Set" not in rows[0]  # manual singles carry no set tag


def test_export_all_and_set_conflict_exits_1(mock_scryfall, db_path, tmp_path) -> None:
    assert add_neo(db_path).exit_code == 0
    result, _ = moxfield_export(db_path, tmp_path, "--all", "--set", "NEO")
    assert result.exit_code == 1
    assert "not both" in result.output


# -- remove: Moxfield prune guidance (issue #85) ---------------------------------


def test_remove_warns_to_prune_moxfield_when_exported(mock_scryfall, db_path, tmp_path) -> None:
    assert add_neo(db_path).exit_code == 0
    # Push NEO to Moxfield (stamps the 4 entries as exported).
    assert moxfield_export(db_path, tmp_path)[0].exit_code == 0

    result = runner.invoke(app, ["remove", "NEO", "--db-path", str(db_path), "--yes"])
    assert result.exit_code == 0, result.output
    assert "Removed" in result.output
    # Actionable prune guidance: the exact tag + count + instruction.
    assert "Moxfield sync" in result.output
    assert "Full Set: NEO" in result.output
    assert "4 of these" in result.output


def test_remove_no_moxfield_note_when_never_exported(mock_scryfall, db_path) -> None:
    # NEO owned but never exported -> nothing in Moxfield to prune, so no note.
    assert add_neo(db_path).exit_code == 0
    result = runner.invoke(app, ["remove", "NEO", "--db-path", str(db_path), "--yes"])
    assert result.exit_code == 0, result.output
    assert "Removed" in result.output
    assert "Moxfield sync" not in result.output


def test_remove_note_only_counts_exported_entries(mock_scryfall, db_path, tmp_path) -> None:
    # Export only one set; removing the other set warns nothing, removing the
    # exported one warns. Proves the count is per-set and export-aware.
    assert add_neo(db_path).exit_code == 0
    assert runner.invoke(app, ["add", "MOM", "--db-path", str(db_path)]).exit_code == 0
    # Export just NEO (its 4 entries get stamped); MOM stays un-exported.
    assert moxfield_export(db_path, tmp_path, "--set", "NEO")[0].exit_code == 0

    mom = runner.invoke(app, ["remove", "MOM", "--db-path", str(db_path), "--yes"])
    assert mom.exit_code == 0 and "Moxfield sync" not in mom.output
    neo = runner.invoke(app, ["remove", "NEO", "--db-path", str(db_path), "--yes"])
    assert neo.exit_code == 0 and "Moxfield sync" in neo.output
