"""Shape exact Scryfall printings into copy-safe card-store want lists.

The CLI resolves cards; this module owns the pure item mapping and the two output
formats specified in docs/DESIGN.md. Like ``export.py``, filesystem writing is the
deliberate sink exception to the otherwise-pure formatting layer.
"""

from __future__ import annotations

import csv
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any
from urllib.parse import quote


class Finish(str, Enum):
    """Supported purchase finish preferences."""

    NONFOIL = "nonfoil"
    FOIL = "foil"
    ANY = "any"


WANT_LIST_COLUMNS = [
    "Quantity",
    "Card Name",
    "Set Name",
    "Set Code",
    "Collector Number",
    "Language",
    "Finish",
    "Condition",
    "Rarity",
    "Scryfall URL",
    "TCGplayer Product ID",
    "TCGplayer URL",
]


@dataclass(frozen=True)
class WantListItem:
    """One resolved printing plus the user's purchase preferences."""

    quantity: int
    name: str
    set_name: str
    set_code: str
    collector_number: str
    language: str
    finish: Finish
    condition: str
    rarity: str
    scryfall_url: str
    tcgplayer_id: int | str | None
    tcgplayer_url: str


def _scryfall_url(card: Mapping[str, Any], set_code: str, collector_number: str) -> str:
    """Return Scryfall's public card URL, with a stable exact-printing fallback."""
    if card.get("scryfall_uri"):
        return str(card["scryfall_uri"])
    code = quote(set_code.lower(), safe="")
    number = quote(collector_number, safe="")
    return f"https://scryfall.com/card/{code}/{number}"


def _tcgplayer_url(card: Mapping[str, Any], tcgplayer_id: int | str | None) -> str:
    """Return the supplied TCGplayer purchase URL, or synthesize one from its ID."""
    purchase_uris = card.get("purchase_uris") or {}
    related_uris = card.get("related_uris") or {}
    url = purchase_uris.get("tcgplayer") or related_uris.get("tcgplayer")
    if url:
        return str(url)
    if tcgplayer_id is not None:
        return f"https://www.tcgplayer.com/product/{tcgplayer_id}"
    return ""


def item_from_card(
    card: Mapping[str, Any],
    *,
    quantity: int = 1,
    language: str = "English",
    finish: Finish = Finish.NONFOIL,
    condition: str = "",
    set_name: str = "",
) -> WantListItem:
    """Map one raw Scryfall card to a want-list item."""
    code = str(card.get("set") or "").lower()
    number = str(card.get("collector_number") or "")
    tcgplayer_id = card.get("tcgplayer_id")
    return WantListItem(
        quantity=quantity,
        name=str(card.get("name") or "?"),
        set_name=str(card.get("set_name") or set_name or code.upper()),
        set_code=code,
        collector_number=number,
        language=language,
        finish=finish,
        condition=condition,
        rarity=str(card.get("rarity") or ""),
        scryfall_url=_scryfall_url(card, code, number),
        tcgplayer_id=tcgplayer_id,
        tcgplayer_url=_tcgplayer_url(card, tcgplayer_id),
    )


def render_plain(items: Iterable[WantListItem]) -> str:
    """Render line-oriented, copy-safe text with no terminal markup."""
    lines = [f"{item.name} {item.set_code.upper()} {item.collector_number}" for item in items]
    return "\n".join(lines) + ("\n" if lines else "")


def item_to_row(item: WantListItem) -> list[Any]:
    """Map an item to the fixed CSV column order."""
    return [
        item.quantity,
        item.name,
        item.set_name,
        item.set_code.upper(),
        item.collector_number,
        item.language,
        item.finish.value,
        item.condition,
        item.rarity,
        item.scryfall_url,
        item.tcgplayer_id if item.tcgplayer_id is not None else "",
        item.tcgplayer_url,
    ]


def write_csv(items: Iterable[WantListItem], dest: Path) -> int:
    """Write a UTF-8 want-list CSV and return its item count."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with dest.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(WANT_LIST_COLUMNS)
        for item in items:
            writer.writerow(item_to_row(item))
            count += 1
    return count
