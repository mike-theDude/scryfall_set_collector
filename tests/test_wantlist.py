"""Unit tests for the plain-text and CSV card-store want-list formats."""

from __future__ import annotations

import csv

from mtgsets import wantlist


def sample_card(**overrides) -> dict:
    card = {
        "id": "hob-94",
        "name": "Dori, Bearer of Friends",
        "set": "hob",
        "set_name": "The Hobbit",
        "collector_number": "94",
        "rarity": "common",
        "scryfall_uri": "https://scryfall.com/card/hob/94/dori-bearer-of-friends",
        "tcgplayer_id": 600094,
        "purchase_uris": {"tcgplayer": "https://shop.tcgplayer.test/dori"},
    }
    card.update(overrides)
    return card


def test_item_from_card_maps_printing_and_preferences() -> None:
    item = wantlist.item_from_card(
        sample_card(),
        quantity=2,
        language="Japanese",
        finish=wantlist.Finish.ANY,
        condition="Near Mint",
    )
    assert item == wantlist.WantListItem(
        quantity=2,
        name="Dori, Bearer of Friends",
        set_name="The Hobbit",
        set_code="hob",
        collector_number="94",
        language="Japanese",
        finish=wantlist.Finish.ANY,
        condition="Near Mint",
        rarity="common",
        scryfall_url="https://scryfall.com/card/hob/94/dori-bearer-of-friends",
        tcgplayer_id=600094,
        tcgplayer_url="https://shop.tcgplayer.test/dori",
    )


def test_item_from_card_builds_lookup_url_fallbacks() -> None:
    item = wantlist.item_from_card(
        sample_card(
            collector_number="A-1★",
            scryfall_uri=None,
            purchase_uris=None,
            related_uris=None,
        )
    )
    assert item.scryfall_url == "https://scryfall.com/card/hob/A-1%E2%98%85"
    assert item.tcgplayer_url == "https://www.tcgplayer.com/product/600094"


def test_render_plain_is_copy_safe_and_preserves_order() -> None:
    first = wantlist.item_from_card(
        sample_card(),
        quantity=2,
        language="Japanese",
        finish=wantlist.Finish.ANY,
        condition="Near Mint",
    )
    second = wantlist.item_from_card(
        sample_card(
            id="hob-91",
            name="Dáin Ironfoot",
            collector_number="91",
            tcgplayer_id=None,
            purchase_uris=None,
        ),
        condition="Played",
    )
    text = wantlist.render_plain([first, second])

    assert text == "Dori, Bearer of Friends HOB 94\nDáin Ironfoot HOB 91\n"
    assert "\x1b" not in text


def test_item_to_row_matches_fixed_csv_column_order() -> None:
    item = wantlist.item_from_card(sample_card(), condition="Near Mint")
    assert wantlist.item_to_row(item) == [
        1,
        "Dori, Bearer of Friends",
        "The Hobbit",
        "HOB",
        "94",
        "English",
        "nonfoil",
        "Near Mint",
        "common",
        "https://scryfall.com/card/hob/94/dori-bearer-of-friends",
        600094,
        "https://shop.tcgplayer.test/dori",
    ]


def test_write_csv_round_trips_commas_unicode_and_fixed_header(tmp_path) -> None:
    items = [
        wantlist.item_from_card(sample_card()),
        wantlist.item_from_card(
            sample_card(id="hob-91", name="Dáin Ironfoot", collector_number="91")
        ),
    ]
    dest = tmp_path / "nested" / "want-list.csv"
    assert wantlist.write_csv(items, dest) == 2

    with dest.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        assert reader.fieldnames == wantlist.WANT_LIST_COLUMNS
        rows = list(reader)
    assert [row["Card Name"] for row in rows] == [
        "Dori, Bearer of Friends",
        "Dáin Ironfoot",
    ]
    assert rows[0]["Set Code"] == "HOB"
    assert rows[0]["TCGplayer Product ID"] == "600094"


def test_empty_plain_and_csv_have_predictable_shapes(tmp_path) -> None:
    assert wantlist.render_plain([]) == ""
    dest = tmp_path / "empty.csv"
    assert wantlist.write_csv([], dest) == 0
    with dest.open(newline="", encoding="utf-8") as handle:
        assert next(csv.reader(handle)) == wantlist.WANT_LIST_COLUMNS
