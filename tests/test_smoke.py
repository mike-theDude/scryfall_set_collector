"""Smoke tests: the package imports and the fixtures are wired up correctly.

These don't test behaviour — that's the per-module issues (#28-#33). They just
prove the harness is green and the fixture coverage is intact, so the rest of the
suite has solid ground to build on.
"""

from __future__ import annotations

import importlib

import pytest

from mtgsets import filters

MODULES = ["cli", "collection", "db", "export", "filters", "scryfall"]


@pytest.mark.parametrize("name", MODULES)
def test_module_imports(name: str) -> None:
    assert importlib.import_module(f"mtgsets.{name}") is not None


def test_neo_fixture_loads(neo_cards: list[dict]) -> None:
    assert neo_cards, "neo_cards fixture is empty"
    # Trimmed-but-real: identity + the fields the filter reads survive trimming.
    sample = neo_cards[0]
    for key in ("id", "name", "set", "collector_number", "lang", "games"):
        assert key in sample, f"trimmed away required field {key!r}"


def test_exclusion_samples_load(exclusion_samples: list[dict]) -> None:
    assert exclusion_samples, "exclusion_samples fixture is empty"


def test_fixtures_cover_every_exclusion_reason(all_sample_cards: list[dict]) -> None:
    """Each REASON_* constant has at least one real card that triggers it."""
    reasons = {filters.exclusion_reason(c) for c in all_sample_cards}
    expected = {
        filters.REASON_NON_ENGLISH,
        filters.REASON_NON_PLAYABLE,
        filters.REASON_OVERSIZED,
        filters.REASON_SERIALIZED,
        filters.REASON_PROMO,
        filters.REASON_VARIANT,
        filters.REASON_VARIATION,
        filters.REASON_NOT_IN_SET,
    }
    missing = expected - reasons
    assert not missing, f"no fixture card triggers: {missing}"


def test_fixtures_include_some_main_set_cards(all_sample_cards: list[dict]) -> None:
    assert any(filters.is_main_set_card(c) for c in all_sample_cards)
