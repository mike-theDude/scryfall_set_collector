"""Unit tests for mtgsets.collection — set breakdown and entry generation.

Pure functions over the filter rules; no I/O. Uses the real fixtures from #27.
"""

from __future__ import annotations

from conftest import card_by_name

from mtgsets import collection, filters

# -- _is_basic_land ---------------------------------------------------------------


def test_is_basic_land_detection(neo_cards) -> None:
    assert collection._is_basic_land(card_by_name(neo_cards, "Plains")) is True
    assert collection._is_basic_land(card_by_name(neo_cards, "Ancestral Katana")) is False


def test_is_basic_land_handles_missing_type_line() -> None:
    assert collection._is_basic_land({}) is False
    assert collection._is_basic_land({"type_line": None}) is False


# -- build_breakdown --------------------------------------------------------------


def test_build_breakdown_partitions_neo(neo_cards) -> None:
    breakdown = collection.build_breakdown(neo_cards)
    assert breakdown.included_count == 4
    assert breakdown.excluded_count == 3
    assert breakdown.total == 7
    # Excluded bucketed by the filter's reason labels, one card each.
    assert set(breakdown.excluded) == {
        filters.REASON_NON_ENGLISH,
        filters.REASON_VARIANT,
        filters.REASON_PROMO,
    }
    assert all(len(cards) == 1 for cards in breakdown.excluded.values())


def test_build_breakdown_booster_false_set(booster_false_sets) -> None:
    # A set where every printing is booster=False (SOS/TMT, issue #50): the
    # membership gate is skipped, so main cards/basics are included and only the
    # treatment variants (borderless, foil-only surgefoil) are excluded.
    breakdown = collection.build_breakdown(booster_false_sets)
    included = {c["name"] for c in breakdown.included}
    assert included == {"The Dawning Archaic", "Forest", "Action News Crew"}
    assert set(breakdown.excluded) == {filters.REASON_VARIANT}
    assert breakdown.excluded_count == 2
    # The old unconditional booster gate would have excluded everything.
    assert breakdown.included_count == 3


def test_build_breakdown_included_matches_filter(all_sample_cards) -> None:
    breakdown = collection.build_breakdown(all_sample_cards)
    expected = [c for c in all_sample_cards if filters.is_main_set_card(c)]
    assert breakdown.included == expected
    # Each excluded card lands under its own exclusion_reason.
    for reason, cards in breakdown.excluded.items():
        for card in cards:
            assert filters.exclusion_reason(card) == reason


def test_excluded_by_count_sorted_descending(neo_cards) -> None:
    # Build an uneven distribution from real cards: 3 promos, 1 variant.
    promo = card_by_name(neo_cards, "Invoke Despair")
    variant = card_by_name(neo_cards, "The Wandering Emperor")
    breakdown = collection.build_breakdown([promo, promo, promo, variant])
    counts = breakdown.excluded_by_count()
    assert counts == [(filters.REASON_PROMO, 3), (filters.REASON_VARIANT, 1)]
    # Most numerous first.
    assert [c for _, c in counts] == sorted((c for _, c in counts), reverse=True)


def test_empty_breakdown() -> None:
    breakdown = collection.build_breakdown([])
    assert breakdown.included_count == 0
    assert breakdown.excluded_count == 0
    assert breakdown.total == 0
    assert breakdown.excluded_by_count() == []


# -- SetBreakdown properties ------------------------------------------------------


def test_basics_and_main_cards_split(neo_cards) -> None:
    breakdown = collection.build_breakdown(neo_cards)
    assert [c["name"] for c in breakdown.basics] == ["Plains"]
    assert len(breakdown.main_cards) == 3
    assert "Plains" not in [c["name"] for c in breakdown.main_cards]
    # basics + main_cards partition the included list.
    assert len(breakdown.basics) + len(breakdown.main_cards) == breakdown.included_count


def test_breakdown_count_arithmetic(all_sample_cards) -> None:
    breakdown = collection.build_breakdown(all_sample_cards)
    assert breakdown.total == breakdown.included_count + breakdown.excluded_count
    assert breakdown.total == len(all_sample_cards)


# -- generate_full_set_entries ----------------------------------------------------


def test_generate_full_set_entries_one_per_card(neo_cards) -> None:
    included = collection.build_breakdown(neo_cards).included
    entries = collection.generate_full_set_entries("NEO", included)
    assert len(entries) == len(included)
    assert [e.scryfall_id for e in entries] == [c["id"] for c in included]


def test_generated_entry_defaults_and_tagging(neo_cards) -> None:
    included = collection.build_breakdown(neo_cards).included
    # set_code passed uppercase; must be normalised to lowercase on the entries.
    entries = collection.generate_full_set_entries("NEO", included)
    for entry in entries:
        assert entry.set_code == "neo"
        assert entry.source_set_code == "neo"
        assert entry.source_type == collection.SOURCE_FULL_SET == "full_set"
        assert entry.quantity == 1
        assert entry.condition == "Near Mint"
        assert entry.language == "English"
        assert entry.foil == 0


def test_generate_full_set_entries_empty() -> None:
    assert collection.generate_full_set_entries("neo", []) == []


# -- GeneratedEntry.as_row matches the DB column order ----------------------------


def test_as_row_column_order() -> None:
    entry = collection.GeneratedEntry(scryfall_id="abc-123", set_code="neo", source_set_code="neo")
    assert entry.as_row() == (
        "abc-123",  # scryfall_id
        "neo",  # set_code
        1,  # quantity
        "Near Mint",  # condition
        "English",  # language
        0,  # foil
        "full_set",  # source_type
        "neo",  # source_set_code
    )


def test_as_row_aligns_with_db_insert_columns() -> None:
    # The row tuple must line up positionally with the INSERT in db.py; if the
    # schema column order ever changes, this and as_row must move together.
    entry = collection.GeneratedEntry(scryfall_id="abc-123", set_code="neo", source_set_code="neo")
    columns = (
        "scryfall_id",
        "set_code",
        "quantity",
        "condition",
        "language",
        "foil",
        "source_type",
        "source_set_code",
    )
    row = dict(zip(columns, entry.as_row(), strict=True))
    assert row == {
        "scryfall_id": "abc-123",
        "set_code": "neo",
        "quantity": 1,
        "condition": "Near Mint",
        "language": "English",
        "foil": 0,
        "source_type": "full_set",
        "source_set_code": "neo",
    }
