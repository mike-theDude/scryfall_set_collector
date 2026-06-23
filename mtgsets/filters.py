"""Single source of truth for full-set include/exclude logic. Implemented in issue #4.

Keep ALL filter conditions here so they can be tuned against real Scryfall data;
do not scatter them across the codebase. See docs/DESIGN.md 'Filtering rules'.
"""

from __future__ import annotations


def is_main_set_card(card: dict) -> bool:
    """Return True if a card belongs in a 'full set'. Implemented in issue #4."""
    raise NotImplementedError("filters.is_main_set_card — see issue #4")
