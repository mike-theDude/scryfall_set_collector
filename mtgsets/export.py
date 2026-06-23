"""Moxfield CSV export and import.

Columns and per-entry defaults are specified in docs/DESIGN.md 'Moxfield CSV export'.
Example row: ``1,0,"Boseiju, Who Endures",NEO,Near Mint,English,,Full Set: NEO,266``

The same column knowledge drives both directions: :func:`write_moxfield_csv` emits the
format and :func:`parse_moxfield_csv` reads it back (issue #61), so an exported
collection round-trips.
"""

from __future__ import annotations

import csv
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: Moxfield import column order (see docs/DESIGN.md).
MOXFIELD_COLUMNS = [
    "Count",
    "Tradelist Count",
    "Name",
    "Edition",
    "Condition",
    "Language",
    "Foil",
    "Tags",
    "Collector Number",
]


def _tag_for(entry: Mapping[str, Any]) -> str:
    """``Full Set: <CODE>`` for set-generated entries; empty for manual singles."""
    code = entry["source_set_code"]
    return f"Full Set: {code.upper()}" if code else ""


def entry_to_row(entry: Mapping[str, Any]) -> list[Any]:
    """Map a joined collection_entries+cards record to a Moxfield CSV row.

    ``entry`` exposes: name, set_code, collector_number, quantity, condition,
    language, foil, source_set_code.
    """
    return [
        entry["quantity"],
        0,  # Tradelist Count — not tracked; default 0 per docs/DESIGN.md
        entry["name"],
        (entry["set_code"] or "").upper(),
        entry["condition"],
        entry["language"],
        "foil" if entry["foil"] else "",
        _tag_for(entry),
        entry["collector_number"],
    ]


#: Foil-column values that mark a foil/etched printing (Moxfield emits ``foil``).
_FOIL_VALUES = frozenset({"foil", "etched", "true", "1", "yes"})


@dataclass(frozen=True)
class ImportRow:
    """One parsed Moxfield CSV row, ready for resolution + manual insertion.

    ``edition`` + ``collector_number`` identify the printing; the rest populate a
    manual entry. ``is_full_set`` flags a ``Full Set: <CODE>``-tagged row (import
    skips these — full sets are added via ``mtgsets add``). ``error`` is set when the
    row can't be used (missing Edition/Collector Number), so the caller can report it
    without aborting.
    """

    line: int
    edition: str
    collector_number: str
    quantity: int
    condition: str
    language: str
    foil: int
    is_full_set: bool
    error: str | None = None


def parse_moxfield_csv(lines: Iterable[str]) -> list[ImportRow]:
    """Parse Moxfield-format CSV lines into :class:`ImportRow` records.

    Header-based (``csv.DictReader``) and case-insensitive on column names, so
    reordered or extra columns are tolerated and only ``Edition`` + ``Collector
    Number`` are required. Never raises on a bad row — it marks ``error`` instead, so
    one malformed line doesn't abort an import. ``Count`` defaults to 1 when blank or
    unparseable; ``Condition``/``Language`` fall back to the export defaults.
    """
    reader = csv.DictReader(lines)
    rows: list[ImportRow] = []
    for i, raw in enumerate(reader, start=1):
        row = {(k or "").strip().lower(): (v or "").strip() for k, v in raw.items()}
        edition = row.get("edition", "")
        number = row.get("collector number", "")

        count = row.get("count", "")
        try:
            quantity = int(count) if count else 1
        except ValueError:
            quantity = 1
        quantity = max(quantity, 1)

        error = None if (edition and number) else "missing Edition or Collector Number"
        rows.append(
            ImportRow(
                line=i,
                edition=edition.lower(),
                collector_number=number,
                quantity=quantity,
                condition=row.get("condition", "") or "Near Mint",
                language=row.get("language", "") or "English",
                foil=1 if row.get("foil", "").lower() in _FOIL_VALUES else 0,
                is_full_set="full set" in row.get("tags", "").lower(),
                error=error,
            )
        )
    return rows


def write_moxfield_csv(entries: Iterable[Mapping[str, Any]], dest: Path) -> int:
    """Write entries to a Moxfield-importable CSV. Returns the number of rows.

    Uses csv's minimal quoting, so only fields containing commas/quotes are
    quoted (matching the docs/DESIGN.md example). Creates parent dirs as needed.
    """
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with dest.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(MOXFIELD_COLUMNS)
        for entry in entries:
            writer.writerow(entry_to_row(entry))
            count += 1
    return count
