"""Unit tests for mtgsets.stats — the pure stats assembly (issue #12)."""

from __future__ import annotations

from mtgsets import stats


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
