"""Collection statistics (issue #12).

Pure assembly over owned-set data (from the db) and the Scryfall set list, so the
CLI stays thin and the arithmetic is unit-testable. Performs no I/O.

The headline figure is **sets owned vs. the total number of sets**, where "sets"
means paper core/expansion releases (``scryfall.release_sets``). Owned sets that
fall outside that universe (Commander decks, Masters reprints, etc.) are still
counted and surfaced separately so nothing is hidden from the totals.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True)
class CollectionStats:
    """Computed collection statistics for the ``stats`` command."""

    owned_total: int
    #: Owned sets that are paper core/expansion releases. ``None`` when the release
    #: universe is unavailable (e.g. Scryfall could not be reached).
    owned_release: int | None
    #: Owned sets outside the core/expansion universe. ``None`` when unavailable.
    owned_other: int | None
    #: Total paper core/expansion sets known to Scryfall. ``None`` when unavailable.
    release_total: int | None
    #: Total generated full-set card entries across the collection.
    card_entries: int

    @property
    def release_pct(self) -> float | None:
        """Percentage of all core/expansion sets owned, or ``None`` if unknown."""
        if not self.release_total or self.owned_release is None:
            return None
        return 100.0 * self.owned_release / self.release_total


def build_stats(
    owned_codes: Iterable[str],
    card_entries: int,
    release_codes: Iterable[str] | None,
) -> CollectionStats:
    """Assemble :class:`CollectionStats` from owned set codes and the release universe.

    ``owned_codes`` are the codes of every owned set; ``release_codes`` are the codes
    of all paper core/expansion sets (or ``None`` when that list is unavailable, in
    which case the release-relative figures degrade to ``None`` but owned totals and
    card counts are still reported). Codes are compared case-insensitively.
    """
    owned = [c.lower() for c in owned_codes]
    owned_total = len(owned)

    if release_codes is None:
        return CollectionStats(
            owned_total=owned_total,
            owned_release=None,
            owned_other=None,
            release_total=None,
            card_entries=card_entries,
        )

    release = {c.lower() for c in release_codes}
    owned_release = sum(1 for c in owned if c in release)
    return CollectionStats(
        owned_total=owned_total,
        owned_release=owned_release,
        owned_other=owned_total - owned_release,
        release_total=len(release),
        card_entries=card_entries,
    )
