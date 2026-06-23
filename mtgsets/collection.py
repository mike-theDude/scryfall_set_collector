"""Expand owned sets into card-level collection entries.

Generated entries use source_type='full_set' with source_set_code=<set>, so that
set removal (issue #9) can target only generated rows and never touch manual
singles or overrides. See docs/DESIGN.md.

The card-level breakdown of a set (what's included vs excluded, and why) is computed
here from the filter rules so it can be shared by `preview` (#6) and `add` (#7).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from . import filters


def _is_basic_land(card: dict[str, Any]) -> bool:
    return "Basic Land" in (card.get("type_line") or "")


@dataclass
class SetBreakdown:
    """Result of applying the full-set filter to every printing in a set."""

    included: list[dict[str, Any]] = field(default_factory=list)
    #: reason label -> excluded printings (see mtgsets.filters REASON_* constants)
    excluded: dict[str, list[dict[str, Any]]] = field(default_factory=dict)

    @property
    def basics(self) -> list[dict[str, Any]]:
        return [c for c in self.included if _is_basic_land(c)]

    @property
    def main_cards(self) -> list[dict[str, Any]]:
        return [c for c in self.included if not _is_basic_land(c)]

    @property
    def included_count(self) -> int:
        return len(self.included)

    @property
    def excluded_count(self) -> int:
        return sum(len(cards) for cards in self.excluded.values())

    @property
    def total(self) -> int:
        return self.included_count + self.excluded_count

    def excluded_by_count(self) -> list[tuple[str, int]]:
        """Excluded reasons with their counts, most numerous first."""
        return sorted(
            ((reason, len(cards)) for reason, cards in self.excluded.items()),
            key=lambda rc: rc[1],
            reverse=True,
        )


def build_breakdown(cards: list[dict[str, Any]]) -> SetBreakdown:
    """Partition raw Scryfall printings into included / excluded-by-reason.

    Pure function over the filter rules in :mod:`mtgsets.filters`; performs no I/O
    so both `preview` and `add` can rely on it.
    """
    breakdown = SetBreakdown()
    for card in cards:
        reason = filters.exclusion_reason(card)
        if reason is None:
            breakdown.included.append(card)
        else:
            breakdown.excluded.setdefault(reason, []).append(card)
    return breakdown
