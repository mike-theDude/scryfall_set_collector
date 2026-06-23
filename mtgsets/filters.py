"""Single source of truth for full-set include/exclude logic.

A "full set" is one paper, English, nonfoil copy of every card in the regular main
set, basic lands included, with all alternate treatments, promos, tokens, and
deck-exclusive extras removed. See docs/DESIGN.md 'Definition of a full set'.

ALL filter conditions live here so they can be tuned against real Scryfall data;
do not scatter them across the codebase.

Tuning notes (validated against live NEO and MOM `unique=prints` data):

* ``booster == True`` separates the regular set from deck-exclusive / Jumpstart /
  promo-only extras that carry no other distinguishing marker (e.g. MOM #323-337 are
  plain ``normal`` cards with no treatment flags, only ``booster == False``). It also
  already excludes every ``boosterfun`` collector-booster treatment. BUT the flag is
  only meaningful when the set actually has booster cards: some recent sets (SOS,
  TMT) mark *every* printing ``booster == False``, so the membership gate is applied
  only when ``set_uses_booster`` is true for the set (else the whole set would filter
  to nothing — issue #50).
* A printing whose ``finishes`` lacks ``nonfoil`` (foil-only or etched-only, e.g.
  TMT's surgefoil basics #305-314) is a collector treatment, not a regular card.
* Most ``frame_effects`` are INTRINSIC to the regular printing and must NOT be
  treated as variants: ``legendary``, ``enchantment``, ``fandfc`` (front of a
  modal/transform DFC), ``fullart`` basics, etc. Only ``showcase``, ``extendedart``
  and ``inverted`` mark alternate treatments.
* The Japanese full-art "ukiyo-e" basics come back under ``set:neo`` as ``lang=ja``
  and are dropped by the language rule, not by a treatment rule.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

#: Scryfall ``layout`` values that are never part of a collectible main set.
EXCLUDED_LAYOUTS = frozenset(
    {
        "token",
        "double_faced_token",
        "emblem",
        "art_series",
        "vanguard",
        "scheme",
        "planar",
    }
)

#: ``frame_effects`` that denote an *alternate treatment* (as opposed to intrinsic
#: frames like ``legendary`` / ``enchantment`` / ``fandfc`` / ``fullart``).
ALT_TREATMENT_FRAME_EFFECTS = frozenset({"showcase", "extendedart", "inverted"})

# -- exclusion reason labels (stable strings; used to bucket `preview` output) ----
REASON_NON_ENGLISH = "Non-English / non-paper / digital-only"
REASON_NON_PLAYABLE = "Tokens / art cards / non-playable"
REASON_OVERSIZED = "Oversized"
REASON_SERIALIZED = "Serialized"
REASON_PROMO = "Promos"
REASON_VARIANT = "Borderless / showcase / extended-art variants"
REASON_VARIATION = "Alternate printing / variation"
REASON_NOT_IN_SET = "Not in the regular set (Commander / deck-exclusive / extras)"


def _is_alternate_treatment(card: dict[str, Any]) -> bool:
    """True if the printing is a borderless/showcase/extended-art/etched variant."""
    if card.get("border_color") == "borderless":
        return True
    if ALT_TREATMENT_FRAME_EFFECTS & set(card.get("frame_effects") or ()):
        return True
    if "boosterfun" in (card.get("promo_types") or ()):
        return True
    # Foil-only / etched-only printings (e.g. surgefoil basics in TMT, etched foil
    # legends) are collector treatments; the regular set always has a nonfoil copy.
    finishes = card.get("finishes")
    if finishes and "nonfoil" not in finishes:
        return True
    return False


def set_uses_booster(cards: Iterable[dict[str, Any]]) -> bool:
    """True if any printing in the set carries ``booster == True``.

    The ``booster`` flag only separates main-set cards from deck-exclusive / promo
    extras in sets that actually have a booster configuration (e.g. NEO, MOM, where
    the deck-only extras are the *only* ``booster == False`` cards). Some recent sets
    (e.g. SOS, TMT) mark *every* printing ``booster == False``, making the flag
    uninformative; for those the membership gate must be skipped, or the whole set
    filters down to nothing. See issue #50 and ``exclusion_reason``.
    """
    return any(card.get("booster") for card in cards)


def exclusion_reason(card: dict[str, Any], *, set_has_booster: bool = True) -> str | None:
    """Return why a printing is excluded from the full set, or ``None`` if included.

    Checks run in priority order so the returned reason is the most informative
    bucket for `preview` (e.g. a borderless promo is reported as a promo, a
    boosterfun showcase as a variant rather than merely "not in set").

    ``set_has_booster`` is the set-level signal from :func:`set_uses_booster`: when
    ``False`` (no printing in the set has ``booster == True``) the membership gate is
    skipped, because the flag carries no information for that set. Callers filtering a
    whole set should pass it; the default ``True`` preserves the historical behaviour
    for single-card checks.
    """
    if card.get("lang") != "en" or card.get("digital") or "paper" not in (card.get("games") or ()):
        return REASON_NON_ENGLISH
    if card.get("layout") in EXCLUDED_LAYOUTS:
        return REASON_NON_PLAYABLE
    if card.get("oversized"):
        return REASON_OVERSIZED
    if "serialized" in (card.get("promo_types") or ()):
        return REASON_SERIALIZED
    if card.get("promo"):
        return REASON_PROMO
    if _is_alternate_treatment(card):
        return REASON_VARIANT
    if card.get("variation"):
        return REASON_VARIATION
    if set_has_booster and not card.get("booster"):
        return REASON_NOT_IN_SET
    return None


def is_main_set_card(card: dict[str, Any], *, set_has_booster: bool = True) -> bool:
    """Return True if a printing belongs in a 'full set' (see docs/DESIGN.md)."""
    return exclusion_reason(card, set_has_booster=set_has_booster) is None
