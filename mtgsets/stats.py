"""Collection statistics (issue #12).

Pure assembly over owned-set data (from the db) and the Scryfall set list, so the
CLI stays thin and the arithmetic is unit-testable. Performs no I/O.

The headline figure is **sets owned vs. the total number of sets**, where "sets"
means paper core/expansion releases (``scryfall.release_sets``). Owned sets that
fall outside that universe (Commander decks, Masters reprints, etc.) are still
counted and surfaced separately so nothing is hidden from the totals.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date
from typing import Any

#: One owned printing weighted by how many copies are owned: ``(quantity, card)``
#: where ``card`` is the raw Scryfall object (see :func:`mtgsets.db.get_owned_cards`).
WeightedCard = tuple[int, dict[str, Any]]


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


# == Composition breakdowns (issue #53) ==========================================
#
# All pure over a list of WeightedCard. Each returns ``[(label, count), ...]`` in a
# canonical, render-ready order. Counts are weighted by owned quantity.

#: Rarities in the order people expect them displayed.
RARITY_ORDER = ("common", "uncommon", "rare", "mythic", "special", "bonus")

#: (color_identity letter -> display name), in WUBRG order.
_COLOR_NAMES = (("W", "White"), ("U", "Blue"), ("B", "Black"), ("R", "Red"), ("G", "Green"))

#: Card types in priority order: a multi-type card is bucketed by the first match,
#: so e.g. an Artifact Creature counts as a Creature and a Land Creature as a Creature.
PRIMARY_TYPE_ORDER = (
    "Creature",
    "Planeswalker",
    "Instant",
    "Sorcery",
    "Enchantment",
    "Artifact",
    "Battle",
    "Land",
)

#: Mana values at or above this are bucketed together as the top of the curve.
CURVE_TOP = 7


def _ordered(counts: Counter[str], order: Iterable[str]) -> list[tuple[str, int]]:
    """Return ``(label, count)`` pairs: ``order`` first (only if nonzero), then any
    remaining labels by descending count for stable, render-ready output."""
    seen = set(order)
    ranked = [(label, counts[label]) for label in order if counts[label]]
    extra = sorted(
        ((label, n) for label, n in counts.items() if label not in seen),
        key=lambda ln: (-ln[1], ln[0]),
    )
    return ranked + extra


def composition_by_rarity(cards: Iterable[WeightedCard]) -> list[tuple[str, int]]:
    """Owned counts by rarity (common/uncommon/rare/mythic/…)."""
    counts: Counter[str] = Counter()
    for qty, card in cards:
        counts[card.get("rarity") or "unknown"] += qty
    return _ordered(counts, RARITY_ORDER)


def composition_by_color(cards: Iterable[WeightedCard]) -> list[tuple[str, int]]:
    """Owned counts by color identity (WUBRG, plus Multicolor and Colorless)."""
    counts: Counter[str] = Counter()
    for qty, card in cards:
        identity = card.get("color_identity") or []
        if not identity:
            counts["Colorless"] += qty
        elif len(identity) > 1:
            counts["Multicolor"] += qty
        else:
            counts[dict(_COLOR_NAMES).get(identity[0], identity[0])] += qty
    order = [name for _, name in _COLOR_NAMES] + ["Multicolor", "Colorless"]
    return _ordered(counts, order)


def primary_type(type_line: str | None) -> str:
    """Bucket a Scryfall ``type_line`` into a single primary type (see PRIMARY_TYPE_ORDER).

    Uses the front face and the part before the ``—`` so subtypes (e.g. "Human")
    don't shadow the card type. Returns ``"Other"`` when nothing matches.
    """
    front = (type_line or "").split("//")[0].split("—")[0]
    for type_name in PRIMARY_TYPE_ORDER:
        if type_name in front:
            return type_name
    return "Other"


def composition_by_type(cards: Iterable[WeightedCard]) -> list[tuple[str, int]]:
    """Owned counts by primary card type (Creature/Instant/Land/…)."""
    counts: Counter[str] = Counter()
    for qty, card in cards:
        counts[primary_type(card.get("type_line"))] += qty
    return _ordered(counts, (*PRIMARY_TYPE_ORDER, "Other"))


def mana_curve(cards: Iterable[WeightedCard]) -> list[tuple[str, int]]:
    """Owned counts by mana value, excluding lands; ``CURVE_TOP`` and up are pooled.

    Lands are excluded because their 0 mana value would swamp the low end and a
    "curve" conventionally describes spells.
    """
    counts: Counter[int] = Counter()
    for qty, card in cards:
        if primary_type(card.get("type_line")) == "Land":
            continue
        cmc = card.get("cmc")
        if cmc is None:
            continue
        bucket = min(int(cmc), CURVE_TOP)
        counts[bucket] += qty
    return [
        (f"{b}+" if b == CURVE_TOP else str(b), counts[b])
        for b in range(CURVE_TOP + 1)
        if counts[b]
    ]


# == Progress over the release timeline (issue #53) ==============================


@dataclass(frozen=True)
class SetRef:
    """A minimal set reference for progress output."""

    code: str
    name: str
    released_at: str | None


@dataclass(frozen=True)
class ProgressStats:
    """Where the collection sits along the core/expansion release timeline."""

    newest_owned: SetRef | None = None
    oldest_owned: SetRef | None = None
    latest_release: SetRef | None = None
    #: Core/expansion releases newer than the newest owned release (0 = up to date).
    sets_behind: int | None = None
    #: ``(year, count)`` of owned core/expansion sets, newest year first.
    coverage_by_year: list[tuple[str, int]] = field(default_factory=list)
    added_last_30: int = 0
    added_last_90: int = 0
    added_last_365: int = 0


def _set_ref(set_obj: dict[str, Any]) -> SetRef:
    return SetRef(
        code=(set_obj.get("code") or "").lower(),
        name=set_obj.get("name") or (set_obj.get("code") or "").upper(),
        released_at=set_obj.get("released_at"),
    )


def build_progress(
    owned_added: Iterable[tuple[str, str | None]],
    all_sets: Iterable[dict[str, Any]],
    release_sets: Iterable[dict[str, Any]],
    today: date,
) -> ProgressStats:
    """Assemble :class:`ProgressStats` (pure).

    ``owned_added`` are ``(set_code, added_at_iso)`` rows for every owned set;
    ``all_sets`` maps any owned code to its release date/name; ``release_sets`` is the
    paper core/expansion universe; ``today`` anchors the "added over time" windows.
    """
    index = {(s.get("code") or "").lower(): s for s in all_sets}
    releases = [_set_ref(s) for s in release_sets]
    release_codes = {r.code for r in releases}

    owned_codes = [code.lower() for code, _ in owned_added]
    owned_refs = [_set_ref(index[c]) for c in owned_codes if c in index]
    dated_owned = [r for r in owned_refs if r.released_at]

    newest_owned = max(dated_owned, key=lambda r: r.released_at, default=None)
    oldest_owned = min(dated_owned, key=lambda r: r.released_at, default=None)
    dated_releases = [r for r in releases if r.released_at]
    latest_release = max(dated_releases, key=lambda r: r.released_at, default=None)

    # "Sets behind" measures release sets newer than the newest owned *release* set.
    owned_releases = [r for r in dated_owned if r.code in release_codes]
    newest_owned_release = max(owned_releases, key=lambda r: r.released_at, default=None)
    sets_behind: int | None = None
    if dated_releases:
        cutoff = newest_owned_release.released_at if newest_owned_release else ""
        sets_behind = sum(1 for r in dated_releases if r.released_at > cutoff)

    year_counts: Counter[str] = Counter()
    for ref in owned_releases:
        year_counts[ref.released_at[:4]] += 1
    coverage_by_year = sorted(year_counts.items(), reverse=True)

    added_30 = added_90 = added_365 = 0
    for _, added_at in owned_added:
        if not added_at:
            continue
        try:
            added = date.fromisoformat(added_at[:10])
        except ValueError:
            continue
        age = (today - added).days
        if age < 0:
            continue
        if age <= 30:
            added_30 += 1
        if age <= 90:
            added_90 += 1
        if age <= 365:
            added_365 += 1

    return ProgressStats(
        newest_owned=newest_owned,
        oldest_owned=oldest_owned,
        latest_release=latest_release,
        sets_behind=sets_behind,
        coverage_by_year=coverage_by_year,
        added_last_30=added_30,
        added_last_90=added_90,
        added_last_365=added_365,
    )


# == Per-year set breakdown (issue #55) ==========================================


@dataclass(frozen=True)
class YearStats:
    """Owned vs. missing core/expansion sets for a single release year.

    "Released" means released as of the ``today`` passed to :func:`build_year_stats`;
    sets dated later in the year are reported as :attr:`upcoming` and excluded from the
    owned/missing totals, since they aren't obtainable yet.
    """

    year: str
    #: Released core/expansion sets that year that are owned, oldest first.
    owned: list[SetRef] = field(default_factory=list)
    #: Released core/expansion sets that year that are not owned, oldest first.
    missing: list[SetRef] = field(default_factory=list)
    #: Core/expansion sets dated that year but not released yet, oldest first.
    upcoming: list[SetRef] = field(default_factory=list)
    #: Owned sets from that year outside the core/expansion universe (Commander,
    #: Masters, etc.), surfaced separately like the headline figure does.
    owned_other: list[SetRef] = field(default_factory=list)

    @property
    def total(self) -> int:
        """Released core/expansion sets that year (owned + missing)."""
        return len(self.owned) + len(self.missing)

    @property
    def pct(self) -> float | None:
        """Percentage of the year's core/expansion sets owned, or ``None`` if none."""
        if not self.total:
            return None
        return 100.0 * len(self.owned) / self.total


def _by_date(refs: Iterable[SetRef]) -> list[SetRef]:
    """Sort set refs chronologically (oldest first), ties broken by code."""
    return sorted(refs, key=lambda r: (r.released_at or "", r.code))


def build_year_stats(
    owned_codes: Iterable[str],
    all_sets: Iterable[dict[str, Any]],
    release_sets: Iterable[dict[str, Any]],
    year: str,
    today: date,
) -> YearStats:
    """Assemble :class:`YearStats` for ``year`` (pure).

    ``owned_codes`` are the codes of every owned set; ``all_sets`` is the full Scryfall
    set list (used to surface owned non-core/expansion sets from the year); and
    ``release_sets`` is the paper core/expansion universe whose owned/missing split
    drives the headline year figure. A set belongs to a year when its ``released_at``
    starts with that 4-digit year; undated sets are excluded. ``today`` separates
    sets already released (``released_at <= today``, counted as owned/missing) from
    ones still to come (reported as ``upcoming``), so future sets don't inflate the
    total or read as missing. Codes are compared case-insensitively.
    """
    owned = {c.lower() for c in owned_codes}
    today_iso = today.isoformat()

    release_refs = [_set_ref(s) for s in release_sets]
    release_codes = {r.code for r in release_refs}
    year_releases = [r for r in release_refs if (r.released_at or "")[:4] == year]

    released = [r for r in year_releases if r.released_at <= today_iso]
    upcoming = _by_date(r for r in year_releases if r.released_at > today_iso)

    owned_in_year = _by_date(r for r in released if r.code in owned)
    missing_in_year = _by_date(r for r in released if r.code not in owned)

    owned_other = _by_date(
        ref
        for s in all_sets
        if (ref := _set_ref(s)).code in owned
        and ref.code not in release_codes
        and (ref.released_at or "")[:4] == year
    )

    return YearStats(
        year=year,
        owned=owned_in_year,
        missing=missing_in_year,
        upcoming=upcoming,
        owned_other=owned_other,
    )


# == Collection value (issue #53) ================================================


@dataclass(frozen=True)
class ValueStats:
    """Estimated collection value and its biggest contributors (USD)."""

    total_usd: float = 0.0
    #: ``(name, set_code, value)`` for the most valuable owned cards, descending.
    top_cards: list[tuple[str, str, float]] = field(default_factory=list)
    #: ``(set_code, value)`` for the most valuable owned sets, descending.
    top_sets: list[tuple[str, float]] = field(default_factory=list)
    priced_count: int = 0
    unpriced_count: int = 0


def card_usd(card: dict[str, Any]) -> float | None:
    """Return a card's nonfoil USD price as a float, or ``None`` if unpriced."""
    price = (card.get("prices") or {}).get("usd")
    if price in (None, ""):
        return None
    try:
        return float(price)
    except (TypeError, ValueError):
        return None


def build_value(cards: Iterable[WeightedCard], top_n: int = 5) -> ValueStats:
    """Sum owned cards' USD prices and rank the top contributors (pure)."""
    total = 0.0
    priced = unpriced = 0
    per_card: list[tuple[str, str, float]] = []
    per_set: Counter[str] = Counter()
    for qty, card in cards:
        price = card_usd(card)
        if price is None:
            unpriced += qty
            continue
        priced += qty
        value = qty * price
        total += value
        set_code = (card.get("set") or "").upper()
        per_card.append((card.get("name") or "?", set_code, value))
        per_set[set_code] += value
    top_cards = sorted(per_card, key=lambda c: c[2], reverse=True)[:top_n]
    top_sets = sorted(per_set.items(), key=lambda s: s[1], reverse=True)[:top_n]
    return ValueStats(
        total_usd=round(total, 2),
        top_cards=top_cards,
        top_sets=top_sets,
        priced_count=priced,
        unpriced_count=unpriced,
    )
