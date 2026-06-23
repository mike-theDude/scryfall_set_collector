"""Moxfield CSV export. Implemented in issue #10.

Columns and per-entry defaults are specified in docs/DESIGN.md 'Moxfield CSV export'.
"""

from __future__ import annotations

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
