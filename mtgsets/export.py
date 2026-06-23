"""Moxfield CSV export.

Columns and per-entry defaults are specified in docs/DESIGN.md 'Moxfield CSV export'.
Example row: ``1,0,"Boseiju, Who Endures",NEO,Near Mint,English,,Full Set: NEO,266``
"""

from __future__ import annotations

import csv
from collections.abc import Iterable, Mapping
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
