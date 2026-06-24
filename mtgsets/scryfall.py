"""Scryfall API client.

Fetches sets and cards from the Scryfall API. Raw card objects are cached into the
`cards` table (full_json) via :func:`mtgsets.db.upsert_cards`. Start with per-set API
calls; bulk data + caching can come later. See docs/DESIGN.md 'Data source notes'.

Scryfall request etiquette: identify via User-Agent/Accept headers and keep
50-100ms between requests. https://scryfall.com/docs/api

Transient failures (HTTP 429 and 5xx) are retried with bounded exponential backoff
(honouring Retry-After); 404 and other 4xx are surfaced immediately. See ``_get``.
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

#: Bounded retry on transient failures (issue #69). Total attempts = 1 try + retries.
_MAX_ATTEMPTS = 3
#: Base seconds for exponential backoff between retries: 0.5, 1.0, ...
_RETRY_BACKOFF = 0.5
#: Hard cap on any single wait, including a server-supplied Retry-After.
_MAX_BACKOFF = 8.0


class ScryfallError(RuntimeError):
    """Raised when the Scryfall API returns an error or is unreachable."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


def _is_retryable_status(status_code: int) -> bool:
    """True for transient HTTP statuses worth retrying: 429 and any 5xx."""
    return status_code == 429 or status_code >= 500


def _retry_wait(attempt: int, retry_after: str | None) -> float:
    """Seconds to wait before the next attempt (0-indexed ``attempt``).

    Honours a numeric ``Retry-After`` header when present; otherwise exponential
    backoff (``_RETRY_BACKOFF * 2**attempt``). Either way the wait is capped at
    ``_MAX_BACKOFF``. A non-numeric Retry-After (HTTP-date form) falls back to backoff.
    """
    if retry_after:
        try:
            return min(float(retry_after), _MAX_BACKOFF)
        except ValueError:
            pass
    return min(_RETRY_BACKOFF * (2**attempt), _MAX_BACKOFF)


def _error_detail(resp: httpx.Response) -> str:
    """Best-effort human-readable detail from a Scryfall error response body."""
    try:
        return resp.json().get("details", resp.text)
    except ValueError:
        return resp.text


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
        """GET a Scryfall JSON endpoint, with bounded retry on transient failures.

        Waits the polite pre-request delay, then issues the request. A ``429`` or
        ``5xx`` is retried with exponential backoff (honouring ``Retry-After``) up to
        :data:`_MAX_ATTEMPTS` times; ``404`` and other ``4xx`` are non-retryable and
        raise :class:`ScryfallError` immediately, as does a final exhausted retry.
        Connection-level errors raise immediately (a transient blip is out of scope).
        """
        for attempt in range(_MAX_ATTEMPTS):
            time.sleep(_REQUEST_DELAY)
            try:
                resp = self._client.get(url, params=params)
            except httpx.HTTPError as exc:
                raise ScryfallError(f"request to {url} failed: {exc}") from exc
            if resp.status_code < 400:
                return resp.json()
            if _is_retryable_status(resp.status_code) and attempt + 1 < _MAX_ATTEMPTS:
                time.sleep(_retry_wait(attempt, resp.headers.get("Retry-After")))
                continue
            raise ScryfallError(
                f"Scryfall {resp.status_code} for {url}: {_error_detail(resp)}",
                status_code=resp.status_code,
            )
        # The final retryable attempt raises above, so the loop never falls through.
        raise AssertionError(
            "unreachable: retry loop exited without return/raise"
        )  # pragma: no cover

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
    def get_card(self, set_code: str, collector_number: str) -> dict[str, Any]:
        """Return one exact printing by set code and collector number.

        Uses ``/cards/:code/:number``, which identifies a single printing
        unambiguously (the same key Moxfield's Edition + Collector Number use).
        Raises :class:`ScryfallError` with ``status_code == 404`` if not found.
        """
        return self._get(f"/cards/{set_code.lower()}/{collector_number}")

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
