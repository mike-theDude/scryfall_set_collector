"""Single source of truth for full-set include/exclude logic.

A "full set" is one paper, English, nonfoil copy of every card in the regular main
set, basic lands included, with all alternate treatments, promos, tokens, and
deck-exclusive extras removed. See docs/DESIGN.md 'Definition of a full set'.

ALL filter conditions live here so they can be tuned against real Scryfall data;
do not scatter them across the codebase.

Tuning notes (validated against live NEO and MOM `unique=prints` data):

* ``booster == True`` is the reliable main-set membership signal. It is what
  separates the regular set from deck-exclusive / Jumpstart / promo-only extras
  that carry no other distinguishing marker (e.g. MOM #323-337 are plain ``normal``
  cards with no treatment flags, only ``booster == False``). It also already
  excludes every ``boosterfun`` collector-booster treatment.
* Most ``frame_effects`` are INTRINSIC to the regular printing and must NOT be
  treated as variants: ``legendary``, ``enchantment``, ``fandfc`` (front of a
  modal/transform DFC), ``fullart`` basics, etc. Only ``showcase``, ``extendedart``
  and ``inverted`` mark alternate treatments.
* The Japanese full-art "ukiyo-e" basics come back under ``set:neo`` as ``lang=ja``
  and are dropped by the language rule, not by a treatment rule.
"""

from __future__ import annotations

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
    # Etched-only printings (e.g. etched foil legends) are a collector treatment.
    if card.get("finishes") == ["etched"]:
        return True
    return False


def exclusion_reason(card: dict[str, Any]) -> str | None:
    """Return why a printing is excluded from the full set, or ``None`` if included.

    Checks run in priority order so the returned reason is the most informative
    bucket for `preview` (e.g. a borderless promo is reported as a promo, a
    boosterfun showcase as a variant rather than merely "not in set").
    """
    if (
        card.get("lang") != "en"
        or card.get("digital")
        or "paper" not in (card.get("games") or ())
    ):
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
    if not card.get("booster"):
        return REASON_NOT_IN_SET
    return None


def is_main_set_card(card: dict[str, Any]) -> bool:
    """Return True if a printing belongs in a 'full set' (see docs/DESIGN.md)."""
    return exclusion_reason(card) is None
