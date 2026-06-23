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
    # --value fetches live prices.
    assert "Fetching current prices" in result.output
    assert "Estimated value" in result.output
    # Progress sees NEO as newest/oldest owned.
    assert "Newest owned" in result.output and "NEO" in result.output


def test_stats_value_skipped_with_no_remote(mock_scryfall, db_path) -> None:
    assert add_neo(db_path).exit_code == 0
    result = runner.invoke(
        app, ["stats", "--value", "--no-remote", "--db-path", str(db_path)]
    )
    assert result.exit_code == 0, result.output
    assert "needs live Scryfall prices" in result.output
    assert "Fetching current prices" not in result.output


def test_stats_progress_skipped_with_no_remote(mock_scryfall, db_path) -> None:
    assert add_neo(db_path).exit_code == 0
    result = runner.invoke(
        app, ["stats", "--progress", "--no-remote", "--db-path", str(db_path)]
    )
    assert result.exit_code == 0, result.output
    assert "needs Scryfall" in result.output
