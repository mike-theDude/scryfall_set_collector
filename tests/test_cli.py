"""Integration tests for mtgsets.cli — the Typer command layer.

End to end through real filter/db/export code; only the HTTP boundary is mocked.
``scryfall.ScryfallClient`` is swapped for a real client wired with an
``httpx.MockTransport`` that serves the NEO fixture cards, so ``add``/``preview``
hit no network. Each command runs against a fresh ``--db-path`` under tmp_path —
no shared global database.
"""

from __future__ import annotations

import httpx
import pytest
from typer.testing import CliRunner

from mtgsets import scryfall
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


def scryfall_owned_row(db_path, code: str, name: str):
    """Insert a bare owned_sets row directly, for guard-path tests."""
    from mtgsets import db as _db

    conn = _db.get_connection(db_path)
    _db.insert_owned_set(conn, set_code=code, set_name=name, added_at="2026-01-01T00:00:00+00:00")
    conn.commit()
    return conn


# -- add-card / remove-card (manual singles) -------------------------------------


def export_lines(db_path, tmp_path) -> list[str]:
    """Export the collection and return the CSV's data rows (no header)."""
    csv_path = tmp_path / "out.csv"
    result = runner.invoke(
        app, ["export", "moxfield", "--db-path", str(db_path), "-o", str(csv_path)]
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


def test_search_lists_matches(mock_scryfall) -> None:
    result = runner.invoke(app, ["search", "neo"])
    assert result.exit_code == 0, result.output
    assert "NEO" in result.output


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
