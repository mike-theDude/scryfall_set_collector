"""Unit tests for mtgsets.filters — the full-set include/exclude rules.

These are the highest-value tests in the suite: the whole app's correctness
hinges on which printings count as "the regular main set". Every branch of
``exclusion_reason`` is anchored on a *real* fixture card (see
``tests/fixtures/``); minimal constructed dicts are used only to isolate the
individual ``_is_alternate_treatment`` sub-conditions and to lock the priority
ordering between branches.
"""

from __future__ import annotations

import pytest
from conftest import card_by_name

from mtgsets import filters


def base_card(**overrides) -> dict:
    """A minimal printing that passes every filter (i.e. is a main-set card).

    Used only for constructed predicate/ordering tests; per-branch tests use the
    real fixtures. Override individual fields to isolate one rule at a time.
    """
    card = {
        "lang": "en",
        "digital": False,
        "games": ["paper"],
        "layout": "normal",
        "oversized": False,
        "promo": False,
        "promo_types": [],
        "border_color": "black",
        "frame_effects": [],
        "finishes": ["nonfoil", "foil"],
        "variation": False,
        "booster": True,
    }
    card.update(overrides)
    return card


def test_base_card_is_included() -> None:
    # Guards the test helper itself: the baseline must qualify, or every
    # constructed override below would be meaningless.
    assert filters.exclusion_reason(base_card()) is None


# -- one real card per exclusion reason ------------------------------------------

# (fixture card name prefix, expected REASON_* constant)
EXCLUDED_CASES = [
    ("A-Ancestral Katana", filters.REASON_NON_ENGLISH),
    ("Construct", filters.REASON_NON_PLAYABLE),  # token layout (TNEO)
    ("Abeyance", filters.REASON_OVERSIZED),  # oversized (OLEP)
    ("Adaptive Automaton", filters.REASON_SERIALIZED),  # serialized (BRR)
    ("Invoke Despair", filters.REASON_PROMO),  # promo (NEO bundle)
    ("The Wandering Emperor", filters.REASON_VARIANT),  # borderless boosterfun
    ("Alley Strangler", filters.REASON_VARIATION),  # variation (AER)
    ("Axgard Artisan", filters.REASON_NOT_IN_SET),  # deck-exclusive (MOM #332)
]


@pytest.mark.parametrize("name, expected", EXCLUDED_CASES)
def test_exclusion_reason_per_branch(all_sample_cards, name, expected) -> None:
    card = card_by_name(all_sample_cards, name)
    assert filters.exclusion_reason(card) == expected
    assert filters.is_main_set_card(card) is False


# -- included cards (no reason) ---------------------------------------------------

INCLUDED_NAMES = [
    "Ancestral Katana",  # plain main-set card
    "Ao, the Dawn Sky",  # intrinsic 'legendary' frame
    "Befriending the Moths",  # intrinsic 'fandfc' + 'enchantment' frames (DFC)
    "Plains",  # basic land
]


@pytest.mark.parametrize("name", INCLUDED_NAMES)
def test_included_cards_have_no_reason(neo_cards, name) -> None:
    card = card_by_name(neo_cards, name)
    assert filters.exclusion_reason(card) is None
    assert filters.is_main_set_card(card) is True


# -- intrinsic frame effects must stay IN (regression guard) ---------------------

INTRINSIC_FRAME_EFFECTS = ["legendary", "enchantment", "fandfc", "fullart"]


@pytest.mark.parametrize("effect", INTRINSIC_FRAME_EFFECTS)
def test_intrinsic_frame_effects_are_not_variants(effect) -> None:
    # The regression we already hit: these frames are intrinsic to the regular
    # printing and must NOT be treated as alternate treatments.
    card = base_card(frame_effects=[effect])
    assert filters._is_alternate_treatment(card) is False
    assert filters.is_main_set_card(card) is True


def test_real_intrinsic_frame_cards_stay_included(neo_cards) -> None:
    # Same guard, anchored on real NEO cards that carry intrinsic frames.
    for name in ("Ao, the Dawn Sky", "Befriending the Moths"):
        card = card_by_name(neo_cards, name)
        assert card.get("frame_effects"), f"{name} fixture lost its frame_effects"
        assert filters.is_main_set_card(card) is True


# -- _is_alternate_treatment, condition by condition -----------------------------

ALT_TREATMENT_TRUE = [
    ("borderless border", {"border_color": "borderless"}),
    ("showcase frame", {"frame_effects": ["showcase"]}),
    ("extendedart frame", {"frame_effects": ["extendedart"]}),
    ("inverted frame", {"frame_effects": ["inverted"]}),
    ("boosterfun promo type", {"promo_types": ["boosterfun"]}),
    ("etched-only finish", {"finishes": ["etched"]}),
]


@pytest.mark.parametrize(
    "label, overrides", ALT_TREATMENT_TRUE, ids=[c[0] for c in ALT_TREATMENT_TRUE]
)
def test_is_alternate_treatment_true(label, overrides) -> None:
    assert filters._is_alternate_treatment(base_card(**overrides)) is True


def test_is_alternate_treatment_true_on_real_variant(neo_cards) -> None:
    card = card_by_name(neo_cards, "The Wandering Emperor")
    assert filters._is_alternate_treatment(card) is True


ALT_TREATMENT_FALSE = [
    ("plain card", {}),
    ("black border", {"border_color": "black"}),
    ("intrinsic legendary frame", {"frame_effects": ["legendary"]}),
    ("normal finishes", {"finishes": ["nonfoil", "foil"]}),
    ("etched among others", {"finishes": ["nonfoil", "etched"]}),
    ("non-boosterfun promo type", {"promo_types": ["bundle"]}),
]


@pytest.mark.parametrize(
    "label, overrides", ALT_TREATMENT_FALSE, ids=[c[0] for c in ALT_TREATMENT_FALSE]
)
def test_is_alternate_treatment_false(label, overrides) -> None:
    assert filters._is_alternate_treatment(base_card(**overrides)) is False


# -- priority ordering between branches ------------------------------------------


def test_promo_reported_before_variant() -> None:
    # A borderless promo is reported as a promo, not a variant.
    card = base_card(promo=True, border_color="borderless")
    assert filters.exclusion_reason(card) == filters.REASON_PROMO


def test_serialized_reported_before_promo() -> None:
    card = base_card(promo=True, promo_types=["serialized"])
    assert filters.exclusion_reason(card) == filters.REASON_SERIALIZED


def test_variant_reported_before_not_in_set() -> None:
    # Boosterfun cards are out of the booster, but reported as the more specific
    # "variant" rather than merely "not in set".
    card = base_card(promo_types=["boosterfun"], booster=False)
    assert filters.exclusion_reason(card) == filters.REASON_VARIANT


def test_non_english_reported_first() -> None:
    # Even a token in another language is bucketed as non-English first.
    card = base_card(lang="ja", layout="token")
    assert filters.exclusion_reason(card) == filters.REASON_NON_ENGLISH


# -- is_main_set_card is exactly the negation ------------------------------------


def test_is_main_set_card_is_negation(all_sample_cards) -> None:
    for card in all_sample_cards:
        assert filters.is_main_set_card(card) == (filters.exclusion_reason(card) is None)


# -- aggregate split over the NEO fixture subset ---------------------------------


def test_neo_fixture_split(neo_cards) -> None:
    included = [c for c in neo_cards if filters.is_main_set_card(c)]
    excluded = [c for c in neo_cards if not filters.is_main_set_card(c)]
    assert len(included) == 4  # plain, legendary, fandfc/enchantment DFC, basic
    assert len(excluded) == 3  # non-English, variant, promo
    assert len(neo_cards) == 7
