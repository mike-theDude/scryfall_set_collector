"""Scryfall API client.

Fetches sets and cards from the Scryfall API. Raw card objects are cached into the
`cards` table (full_json) via :func:`mtgsets.db.upsert_cards`. Start with per-set API
calls; bulk data + caching can come later. See docs/DESIGN.md 'Data source notes'.

Scryfall request etiquette: identify via User-Agent/Accept headers and keep
50-100ms between requests. https://scryfall.com/docs/api
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from typing import Any

import httpx

from . import __version__

SCRYFALL_API_BASE = "https://api.scryfall.com"

_HEADERS = {
    "User-Agent": (
        f"mtgsets/{__version__} (https://github.com/mike-theDude/scryfall_set_collector)"
    ),
    "Accept": "application/json",
}

#: Scryfall asks for 50-100ms between requests; be polite.
_REQUEST_DELAY = 0.1


class ScryfallError(RuntimeError):
    """Raised when the Scryfall API returns an error or is unreachable."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class ScryfallClient:
    """Thin synchronous client over the Scryfall REST API."""

    def __init__(
        self,
        base_url: str = SCRYFALL_API_BASE,
        timeout: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        # ``transport`` is a test seam (e.g. httpx.MockTransport); it defaults to
        # None so production uses httpx's real network transport unchanged.
        self._client = httpx.Client(
            base_url=base_url, headers=_HEADERS, timeout=timeout, transport=transport
        )

    def __enter__(self) -> ScryfallClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    # -- low level --------------------------------------------------------
    def _get(self, url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        time.sleep(_REQUEST_DELAY)
        try:
            resp = self._client.get(url, params=params)
        except httpx.HTTPError as exc:
            raise ScryfallError(f"request to {url} failed: {exc}") from exc
        if resp.status_code >= 400:
            try:
                detail = resp.json().get("details", resp.text)
            except ValueError:
                detail = resp.text
            raise ScryfallError(
                f"Scryfall {resp.status_code} for {url}: {detail}",
                status_code=resp.status_code,
            )
        return resp.json()

    def _paginate(self, url: str, params: dict[str, Any] | None = None) -> Iterator[dict[str, Any]]:
        """Yield every item across a paginated Scryfall list response."""
        page = self._get(url, params=params)
        while True:
            yield from page.get("data", [])
            if not page.get("has_more"):
                break
            # next_page is an absolute URL; httpx uses it as-is.
            page = self._get(page["next_page"])

    # -- sets -------------------------------------------------------------
    def get_set(self, set_code: str) -> dict[str, Any]:
        """Return the set object for a set code (e.g. 'neo')."""
        return self._get(f"/sets/{set_code.lower()}")

    def get_sets(self) -> list[dict[str, Any]]:
        """Return every set known to Scryfall."""
        return list(self._paginate("/sets"))

    # -- cards ------------------------------------------------------------
    def get_set_cards(self, set_code: str) -> list[dict[str, Any]]:
        """Return every printing in a set, variants included.

        Uses ``unique=prints`` so alternate treatments are returned and can be
        excluded downstream by filters.py. Returns ``[]`` if the set has no cards.
        """
        params = {"q": f"set:{set_code.lower()}", "unique": "prints", "order": "set"}
        try:
            return list(self._paginate("/cards/search", params=params))
        except ScryfallError as exc:
            # /cards/search returns 404 when the query matches nothing.
            if exc.status_code == 404:
                return []
            raise


#: ``set_type`` values that count as a collectible "release" — the numbered core
#: and expansion sets this app is built around. Commander/Masters/duel decks,
#: tokens, promos, memorabilia, and digital-only (Alchemy) sets are excluded so the
#: "sets owned vs. total" denominator (issue #12) matches what people mean by "a set".
RELEASE_SET_TYPES = frozenset({"core", "expansion"})


def is_release_set(set_obj: dict[str, Any]) -> bool:
    """True if a Scryfall set object is a paper core/expansion release."""
    return not set_obj.get("digital") and set_obj.get("set_type") in RELEASE_SET_TYPES


def release_sets(sets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Filter a Scryfall set list to paper core/expansion releases (see #12)."""
    return [s for s in sets if is_release_set(s)]


def match_sets(sets: list[dict[str, Any]], query: str) -> list[dict[str, Any]]:
    """Filter sets whose code or name contains ``query`` (case-insensitive).

    Scryfall has no server-side set search, so the caller fetches all sets via
    :meth:`ScryfallClient.get_sets` and narrows them here. Results are sorted by
    release date, newest first (undated sets last).
    """
    needle = query.strip().lower()
    matches = [
        s
        for s in sets
        if needle in (s.get("code") or "").lower() or needle in (s.get("name") or "").lower()
    ]
    matches.sort(key=lambda s: s.get("released_at") or "", reverse=True)
    return matches
