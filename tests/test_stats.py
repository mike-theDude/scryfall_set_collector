"""Unit tests for mtgsets.stats — the pure stats assembly (issues #12, #53)."""

from __future__ import annotations

from datetime import date

from mtgsets import stats


def wc(qty, **fields):
    """A ``(quantity, card)`` WeightedCard carrying the given Scryfall fields."""
    return (qty, fields)


def test_counts_owned_release_vs_total() -> None:
    s = stats.build_stats(
        owned_codes=["neo", "mom"],
        card_entries=583,
        release_codes=["neo", "mom", "war", "dom"],
    )
    assert s.owned_total == 2
    assert s.owned_release == 2
    assert s.owned_other == 0
    assert s.release_total == 4
    assert s.release_pct == 50.0
    assert s.card_entries == 583


def test_owned_sets_outside_release_universe_are_split_out() -> None:
    # cmd is a Commander set: owned but not a core/expansion release.
    s = stats.build_stats(
        owned_codes=["neo", "cmd"],
        card_entries=400,
        release_codes=["neo", "mom"],
    )
    assert s.owned_total == 2
    assert s.owned_release == 1
    assert s.owned_other == 1
    assert s.release_total == 2
    assert s.release_pct == 50.0


def test_case_insensitive_code_matching() -> None:
    s = stats.build_stats(
        owned_codes=["NEO", "MoM"],
        card_entries=1,
        release_codes=["neo", "mom"],
    )
    assert s.owned_release == 2
    assert s.owned_other == 0


def test_no_release_universe_degrades_gracefully() -> None:
    # Scryfall unavailable: owned totals/cards still computed, release figures None.
    s = stats.build_stats(owned_codes=["neo"], card_entries=292, release_codes=None)
    assert s.owned_total == 1
    assert s.owned_release is None
    assert s.owned_other is None
    assert s.release_total is None
    assert s.release_pct is None
    assert s.card_entries == 292


def test_empty_collection() -> None:
    s = stats.build_stats(owned_codes=[], card_entries=0, release_codes=["neo"])
    assert s.owned_total == 0
    assert s.owned_release == 0
    assert s.owned_other == 0
    assert s.release_pct == 0.0


def test_release_pct_none_when_total_zero() -> None:
    s = stats.build_stats(owned_codes=[], card_entries=0, release_codes=[])
    assert s.release_total == 0
    assert s.release_pct is None  # no divide-by-zero


# == composition (issue #53) =====================================================


def test_composition_by_rarity_weighted_and_ordered() -> None:
    cards = [
        wc(1, rarity="mythic"),
        wc(2, rarity="common"),
        wc(1, rarity="rare"),
        wc(1, rarity="common"),
    ]
    assert stats.composition_by_rarity(cards) == [
        ("common", 3),
        ("rare", 1),
        ("mythic", 1),
    ]


def test_composition_by_color_buckets_identity() -> None:
    cards = [
        wc(1, color_identity=["W"]),
        wc(1, color_identity=["U"]),
        wc(3, color_identity=[]),  # colorless
        wc(1, color_identity=["W", "U"]),  # multicolor
        wc(2, color_identity=["W"]),
    ]
    assert stats.composition_by_color(cards) == [
        ("White", 3),
        ("Blue", 1),
        ("Multicolor", 1),
        ("Colorless", 3),
    ]


def test_primary_type_priority_order() -> None:
    # An Artifact Creature is a Creature; a Land Creature is a Creature.
    assert stats.primary_type("Artifact Creature — Golem") == "Creature"
    assert stats.primary_type("Legendary Creature — Dragon") == "Creature"
    assert stats.primary_type("Basic Land — Plains") == "Land"
    assert stats.primary_type("Instant") == "Instant"
    assert stats.primary_type("Enchantment Artifact") == "Enchantment"
    assert stats.primary_type("Hero") == "Other"
    assert stats.primary_type(None) == "Other"


def test_composition_by_type_counts() -> None:
    cards = [
        wc(2, type_line="Creature — Human"),
        wc(1, type_line="Instant"),
        wc(1, type_line="Basic Land — Forest"),
    ]
    assert stats.composition_by_type(cards) == [
        ("Creature", 2),
        ("Instant", 1),
        ("Land", 1),
    ]


def test_mana_curve_excludes_lands_and_pools_top() -> None:
    cards = [
        wc(1, type_line="Land — Forest", cmc=0),  # excluded
        wc(2, type_line="Creature", cmc=1),
        wc(1, type_line="Sorcery", cmc=3),
        wc(1, type_line="Creature", cmc=9),  # pools into 7+
        wc(1, type_line="Creature", cmc=7),  # pools into 7+
    ]
    assert stats.mana_curve(cards) == [("1", 2), ("3", 1), ("7+", 2)]


# == progress (issue #53) ========================================================

ALL_SETS = [
    {"code": "neo", "name": "Neon Dynasty", "released_at": "2022-02-18"},
    {"code": "snc", "name": "New Capenna", "released_at": "2022-04-29"},
    {"code": "mom", "name": "March of the Machine", "released_at": "2023-04-21"},
    {"code": "cmd", "name": "Commander Deck", "released_at": "2023-01-01"},
]
RELEASES = [s for s in ALL_SETS if s["code"] != "cmd"]  # core/expansion only


def test_progress_newest_oldest_and_sets_behind() -> None:
    owned = [("neo", "2026-06-01T00:00:00+00:00")]
    p = stats.build_progress(owned, ALL_SETS, RELEASES, date(2026, 6, 23))
    assert p.newest_owned.code == "neo"
    assert p.oldest_owned.code == "neo"
    assert p.latest_release.code == "mom"
    # snc and mom are newer core/expansion releases than neo -> 2 behind.
    assert p.sets_behind == 2
    assert p.coverage_by_year == [("2022", 1)]


def test_progress_year_coverage_groups_owned_releases() -> None:
    # Own neo (2022) and mom (2023); newest owned release is mom, the newest release
    # overall, so nothing is newer -> 0 behind. Coverage groups by release year.
    owned = [("neo", None), ("mom", None)]
    p = stats.build_progress(owned, ALL_SETS, RELEASES, date(2026, 6, 23))
    assert p.sets_behind == 0
    assert p.coverage_by_year == [("2023", 1), ("2022", 1)]


def test_progress_added_windows() -> None:
    owned = [
        ("neo", "2026-06-20T00:00:00+00:00"),  # 3 days ago
        ("snc", "2026-04-01T00:00:00+00:00"),  # ~83 days ago
        ("mom", "2025-01-01T00:00:00+00:00"),  # >365 days ago
    ]
    p = stats.build_progress(owned, ALL_SETS, RELEASES, date(2026, 6, 23))
    assert p.added_last_30 == 1
    assert p.added_last_90 == 2
    assert p.added_last_365 == 2


def test_progress_owns_latest_is_zero_behind() -> None:
    owned = [("mom", "2026-06-01T00:00:00+00:00")]
    p = stats.build_progress(owned, ALL_SETS, RELEASES, date(2026, 6, 23))
    assert p.sets_behind == 0


# == per-year breakdown (issues #55, #57) ========================================

# A fixed "today" well after every set in ALL_SETS, so those are all released.
YEAR_TODAY = date(2026, 6, 23)


def test_year_splits_owned_and_missing_core_expansion() -> None:
    # 2022 has neo + snc; owning only neo leaves snc missing.
    y = stats.build_year_stats(["neo"], ALL_SETS, RELEASES, "2022", YEAR_TODAY)
    assert y.year == "2022"
    assert [r.code for r in y.owned] == ["neo"]
    assert [r.code for r in y.missing] == ["snc"]
    assert y.total == 2
    assert y.pct == 50.0


def test_year_orders_chronologically() -> None:
    # Own nothing in 2022 -> both sets missing, oldest (neo, Feb) before snc (Apr).
    y = stats.build_year_stats([], ALL_SETS, RELEASES, "2022", YEAR_TODAY)
    assert [r.code for r in y.missing] == ["neo", "snc"]
    assert y.owned == []
    assert y.pct == 0.0


def test_year_surfaces_owned_other_sets_separately() -> None:
    # cmd is a 2023 Commander deck (outside core/expansion): owned, but listed as
    # "other" rather than counting toward the year's owned/missing totals.
    y = stats.build_year_stats(["mom", "cmd"], ALL_SETS, RELEASES, "2023", YEAR_TODAY)
    assert [r.code for r in y.owned] == ["mom"]
    assert y.missing == []
    assert [r.code for r in y.owned_other] == ["cmd"]
    assert y.total == 1  # cmd not counted in the core/expansion total


def test_year_case_insensitive_codes() -> None:
    y = stats.build_year_stats(["NEO"], ALL_SETS, RELEASES, "2022", YEAR_TODAY)
    assert [r.code for r in y.owned] == ["neo"]


def test_year_with_no_releases_has_no_total() -> None:
    y = stats.build_year_stats(["neo"], ALL_SETS, RELEASES, "1999", YEAR_TODAY)
    assert y.total == 0
    assert y.pct is None
    assert y.owned == [] and y.missing == []


# -- unreleased / future sets (issue #57) ----------------------------------------

# Three 2026 core/expansion sets: one already out, one out exactly today, one future.
SETS_2026 = [
    {"code": "ecl", "name": "Released Set", "released_at": "2026-02-06"},
    {"code": "tmt", "name": "Out Today", "released_at": "2026-06-23"},
    {"code": "sos", "name": "Future Set", "released_at": "2026-11-14"},
]


def test_year_excludes_future_sets_from_total_and_missing() -> None:
    y = stats.build_year_stats([], SETS_2026, SETS_2026, "2026", YEAR_TODAY)
    # Only ecl + tmt are out as of today; sos is future, so it's not counted.
    assert [r.code for r in y.missing] == ["ecl", "tmt"]
    assert y.total == 2
    assert [r.code for r in y.upcoming] == ["sos"]


def test_year_set_released_today_counts_as_released() -> None:
    y = stats.build_year_stats(["tmt"], SETS_2026, SETS_2026, "2026", YEAR_TODAY)
    # A set dated exactly today is obtainable -> owned, not upcoming.
    assert "tmt" in [r.code for r in y.owned]
    assert "tmt" not in [r.code for r in y.upcoming]


def test_year_past_years_unaffected_by_today() -> None:
    # Every 2022 set is long released, so none land in upcoming regardless of today.
    y = stats.build_year_stats(["neo"], ALL_SETS, RELEASES, "2022", YEAR_TODAY)
    assert y.upcoming == []
    assert y.total == 2


# == year-range breakdown (issue #77) ============================================


def test_range_aggregates_across_years() -> None:
    # 2022 has neo+snc, 2023 has mom. Own neo+mom -> per-year split plus a roll-up.
    r = stats.build_range_stats(["neo", "mom"], ALL_SETS, RELEASES, 2022, 2023, YEAR_TODAY)
    assert [y.year for y in r.years] == ["2022", "2023"]  # oldest first
    assert [r0.code for r0 in r.years[0].owned] == ["neo"]
    assert [r0.code for r0 in r.years[0].missing] == ["snc"]
    assert [r0.code for r0 in r.years[1].owned] == ["mom"]
    # Roll-up: 2 owned of 3 released across the span.
    assert r.owned_total == 2
    assert r.release_total == 3
    assert round(r.pct, 1) == 66.7


def test_range_single_year_matches_year_stats() -> None:
    r = stats.build_range_stats(["neo"], ALL_SETS, RELEASES, 2022, 2022, YEAR_TODAY)
    assert len(r.years) == 1
    assert r.owned_total == 1 and r.release_total == 2
    assert r.pct == 50.0


def test_range_includes_years_with_no_releases() -> None:
    # 1999 contributes a YearStats with an empty total; it doesn't skew the roll-up.
    r = stats.build_range_stats(["neo"], ALL_SETS, RELEASES, 1999, 2000, YEAR_TODAY)
    assert [y.year for y in r.years] == ["1999", "2000"]
    assert all(y.total == 0 for y in r.years)
    assert r.owned_total == 0 and r.release_total == 0
    assert r.pct is None  # no divide-by-zero when the span has no releases


def test_range_excludes_future_sets_from_totals() -> None:
    # The 2026 span includes a future set (sos); it must not count toward the total.
    r = stats.build_range_stats([], SETS_2026, SETS_2026, 2026, 2026, YEAR_TODAY)
    assert r.release_total == 2  # ecl + tmt are out; sos is upcoming
    assert [u.code for u in r.years[0].upcoming] == ["sos"]
    assert r.owned_total == 0


def test_range_from_after_to_is_empty() -> None:
    # The CLI rejects this, but the pure function degrades to an empty span.
    r = stats.build_range_stats(["neo"], ALL_SETS, RELEASES, 2023, 2022, YEAR_TODAY)
    assert r.years == []
    assert r.owned_total == 0 and r.release_total == 0
    assert r.pct is None


# == value (issue #53) ===========================================================


def test_card_usd_parsing() -> None:
    assert stats.card_usd({"prices": {"usd": "1.50"}}) == 1.5
    assert stats.card_usd({"prices": {"usd": None}}) is None
    assert stats.card_usd({"prices": {"usd": ""}}) is None
    assert stats.card_usd({}) is None
    assert stats.card_usd({"prices": {"usd": "bad"}}) is None


def test_build_value_total_and_tops() -> None:
    cards = [
        wc(1, name="Pricey", set="neo", prices={"usd": "50.00"}),
        wc(2, name="Cheap", set="neo", prices={"usd": "1.00"}),
        wc(1, name="MidMom", set="mom", prices={"usd": "10.00"}),
        wc(1, name="NoPrice", set="mom", prices={"usd": None}),
    ]
    v = stats.build_value(cards, top_n=2)
    assert v.total_usd == 62.0  # 50 + 2*1 + 10
    assert v.priced_count == 4  # 1 + 2 + 1
    assert v.unpriced_count == 1
    assert v.top_cards == [("Pricey", "NEO", 50.0), ("MidMom", "MOM", 10.0)]
    assert v.top_sets == [("NEO", 52.0), ("MOM", 10.0)]


def test_build_value_empty() -> None:
    v = stats.build_value([])
    assert v.total_usd == 0.0
    assert v.top_cards == [] and v.top_sets == []
    assert v.priced_count == 0 and v.unpriced_count == 0
