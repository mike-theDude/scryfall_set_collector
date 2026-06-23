"""Shared pytest fixtures for the mtgsets test suite.

The card fixtures under ``tests/fixtures/`` are trimmed but otherwise real
Scryfall objects (see ``tests/fixtures/README.md``). Loaders here parse them once
and hand tests plain ``dict`` cards — the same shape ``mtgsets`` passes around.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> list[dict]:
    """Parse a fixture JSON file (``name`` without the .json suffix)."""
    return json.loads((FIXTURES_DIR / f"{name}.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def neo_cards() -> list[dict]:
    """A trimmed real NEO subset: included main cards, basics, and excluded prints."""
    return load_fixture("neo_cards")


@pytest.fixture(scope="session")
def exclusion_samples() -> list[dict]:
    """Real cards covering the exclusion reasons NEO's print data lacks.

    Token, serialized, oversized, variation, and not-in-the-regular-set, pulled
    from across sets so every filter branch has a real representative.
    """
    return load_fixture("exclusion_samples")


@pytest.fixture(scope="session")
def all_sample_cards(neo_cards, exclusion_samples) -> list[dict]:
    """Every fixture card — NEO subset plus the cross-set exclusion samples."""
    return [*neo_cards, *exclusion_samples]


def card_by_name(cards: list[dict], name: str) -> dict:
    """Return the single fixture card whose name starts with ``name``.

    Convenience for tests that target a specific printing (e.g. a basic land).
    """
    matches = [c for c in cards if c.get("name", "").startswith(name)]
    if len(matches) != 1:
        raise LookupError(f"expected exactly one card matching {name!r}, got {len(matches)}")
    return matches[0]
