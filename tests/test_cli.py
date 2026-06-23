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

NEO_SET = {"code": "neo", "name": "Kamigawa: Neon Dynasty"}


@pytest.fixture
def mock_scryfall(monkeypatch, neo_cards):
    """Patch ScryfallClient so add/preview serve the NEO fixtures over a mock
    transport. /sets/neo and set:neo searches succeed; everything else 404s."""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/sets/neo":
            return httpx.Response(200, json=NEO_SET)
        if path.startswith("/sets/"):
            return httpx.Response(404, json={"details": "Set not found"})
        if path == "/cards/search":
            if request.url.params.get("q") == "set:neo":
                return httpx.Response(200, json={"data": neo_cards, "has_more": False})
            return httpx.Response(404, json={"details": "no cards found"})
        if path == "/sets":
            return httpx.Response(200, json={"data": [NEO_SET], "has_more": False})
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


# -- search ----------------------------------------------------------------------


def test_search_lists_matches(mock_scryfall) -> None:
    result = runner.invoke(app, ["search", "neo"])
    assert result.exit_code == 0, result.output
    assert "NEO" in result.output
