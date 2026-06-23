"""Scryfall API client. Implemented in issue #3.

Fetches cards/sets by set code and caches raw objects into the `cards` table
(full_json). Start with per-set API calls; bulk data + caching can come later.
See docs/DESIGN.md 'Data source notes'.
"""

from __future__ import annotations

SCRYFALL_API_BASE = "https://api.scryfall.com"
