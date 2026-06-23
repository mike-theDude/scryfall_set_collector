"""Unit tests for mtgsets.scryfall — API client and set matching.

Zero real network: the client is driven by an injected ``httpx.MockTransport``
(via the ``transport`` seam on ScryfallClient), and the inter-request delay is
patched to zero so the suite stays fast.
"""

from __future__ import annotations

import httpx
import pytest

from mtgsets import scryfall
from mtgsets.scryfall import ScryfallClient, ScryfallError, match_sets


@pytest.fixture(autouse=True)
def no_request_delay(monkeypatch):
    """Drop the politeness sleep so the mocked tests don't actually wait."""
    monkeypatch.setattr(scryfall, "_REQUEST_DELAY", 0.0)


def client_for(handler) -> ScryfallClient:
    """A ScryfallClient whose HTTP is served by ``handler`` (no network)."""
    return ScryfallClient(transport=httpx.MockTransport(handler))


# -- match_sets (pure) ------------------------------------------------------------

SETS = [
    {"code": "neo", "name": "Kamigawa: Neon Dynasty", "released_at": "2022-02-18"},
    {"code": "mom", "name": "March of the Machine", "released_at": "2023-04-21"},
    {"code": "one", "name": "Phyrexia: All Will Be One", "released_at": "2023-02-10"},
    {"code": "und", "name": "Undated Set", "released_at": None},
]


def test_match_sets_by_code_case_insensitive() -> None:
    assert [s["code"] for s in match_sets(SETS, "NEO")] == ["neo"]


def test_match_sets_by_name_substring() -> None:
    assert [s["code"] for s in match_sets(SETS, "machine")] == ["mom"]


def test_match_sets_sorted_newest_first() -> None:
    # "the" appears in no code; match on name substrings that hit several sets.
    results = match_sets(SETS, "a")  # appears in most names
    dated = [s for s in results if s["released_at"]]
    assert dated == sorted(dated, key=lambda s: s["released_at"], reverse=True)


def test_match_sets_undated_sorts_last() -> None:
    results = match_sets(SETS, "set")  # matches only "Undated Set"
    assert [s["code"] for s in results] == ["und"]
    # And when mixed in, the undated set trails the dated ones.
    mixed = match_sets([*SETS], "")  # empty needle matches everything
    assert mixed[-1]["released_at"] is None


def test_match_sets_no_match() -> None:
    assert match_sets(SETS, "zzz") == []


def test_match_sets_handles_missing_fields() -> None:
    assert match_sets([{"code": None, "name": None}], "x") == []


# -- get_set_cards pagination -----------------------------------------------------

def test_get_set_cards_paginates_and_concatenates() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/cards/search"
        if request.url.params.get("page") == "2":
            return httpx.Response(200, json={"data": [{"id": "c3"}], "has_more": False})
        # First page: signals more, points at an absolute next_page URL.
        return httpx.Response(
            200,
            json={
                "data": [{"id": "c1"}, {"id": "c2"}],
                "has_more": True,
                "next_page": "https://api.scryfall.com/cards/search?page=2",
            },
        )

    with client_for(handler) as client:
        cards = client.get_set_cards("tst")
    assert [c["id"] for c in cards] == ["c1", "c2", "c3"]


def test_get_set_cards_sends_unique_prints_query() -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(dict(request.url.params))
        return httpx.Response(200, json={"data": [], "has_more": False})

    with client_for(handler) as client:
        client.get_set_cards("NEO")
    assert seen["q"] == "set:neo"  # lowercased
    assert seen["unique"] == "prints"


def test_get_set_cards_returns_empty_on_404() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"details": "no cards found"})

    with client_for(handler) as client:
        assert client.get_set_cards("nope") == []


# -- error mapping ----------------------------------------------------------------

def test_http_error_status_maps_to_scryfall_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"details": "server boom"})

    with client_for(handler) as client:
        with pytest.raises(ScryfallError) as excinfo:
            client.get_set("neo")
    assert excinfo.value.status_code == 500
    assert "server boom" in str(excinfo.value)


def test_get_set_404_raises_with_status_code() -> None:
    # Unlike /cards/search, a missing /sets/<code> surfaces as an error.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"details": "Set not found"})

    with client_for(handler) as client:
        with pytest.raises(ScryfallError) as excinfo:
            client.get_set("zzz")
    assert excinfo.value.status_code == 404


def test_transport_failure_maps_to_scryfall_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    with client_for(handler) as client:
        with pytest.raises(ScryfallError) as excinfo:
            client.get_set("neo")
    # Transport failures carry no HTTP status.
    assert excinfo.value.status_code is None
    assert "failed" in str(excinfo.value)


# -- get_set / get_sets happy paths ----------------------------------------------

def test_get_set_returns_object() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/sets/neo"
        return httpx.Response(200, json={"code": "neo", "name": "Neon Dynasty"})

    with client_for(handler) as client:
        assert client.get_set("NEO")["code"] == "neo"  # path lowercased


def test_get_sets_paginates() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("page") == "2":
            return httpx.Response(200, json={"data": [{"code": "mom"}], "has_more": False})
        return httpx.Response(
            200,
            json={
                "data": [{"code": "neo"}],
                "has_more": True,
                "next_page": "https://api.scryfall.com/sets?page=2",
            },
        )

    with client_for(handler) as client:
        sets = client.get_sets()
    assert [s["code"] for s in sets] == ["neo", "mom"]
