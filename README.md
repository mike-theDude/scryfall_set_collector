# scryfall_set_collector

A command-line app (`mtgsets`) for managing a Magic: The Gathering collection by **full set**.

I collect Magic cards by completing full sets. Rather than scanning or manually
entering every card, this app lets me mark a set as fully owned, automatically
expands it into individual card entries using the [Scryfall API](https://scryfall.com/docs/api),
and exports the result in a format I can import into [Moxfield](https://www.moxfield.com/)'s
collection/library feature.

## Core design principle

Store ownership at the **set level**, but generate, export, and check ownership at
the **card level**.

## What "full set" means

A full set includes:

- All regular main-set cards
- Basic lands from the main set
- One paper, English, nonfoil copy by default

A full set does **not** include borderless / showcase / extended-art variants,
Commander deck cards, tokens, art cards, promos, serialized cards, oversized cards,
digital-only cards, collector-booster alternate treatments, or any special variants
outside the regular main set.

Filter logic lives in one place (`mtgsets/filters.py`) so it can be tuned against
real set data.

## Tech stack

- **Language:** Python
- **CLI framework:** [Typer](https://typer.tiangolo.com/)
- **Terminal formatting:** [Rich](https://rich.readthedocs.io/)
- **HTTP client:** httpx (or requests)
- **Database:** SQLite
- **Data source:** [Scryfall API](https://scryfall.com/docs/api)

## Commands

```
mtgsets init                 Create the local SQLite database
mtgsets search <query>       Search Scryfall for sets
mtgsets preview <set_code>   Show what would be included/excluded before adding
mtgsets add <set_code>       Mark a set as fully owned and generate card entries
mtgsets list                 List owned sets
mtgsets remove <set_code>    Remove an owned set (and only its generated entries)
mtgsets export moxfield      Export a Moxfield-compatible CSV
```

Planned later: `refresh`, `stats`, `show`, `add-card`, `remove-card`, `check-deck`.

## Workflow

```
mtgsets init
mtgsets search neo
mtgsets preview NEO
mtgsets add NEO
# ...repeat for other owned sets
mtgsets export moxfield
```

The `preview` command shows exactly which cards will be included and excluded
*before* adding a set — important given how many variants and edge cases Magic
set data contains.

## Project layout

```
scryfall_set_collector/
  mtgsets/
    __init__.py
    cli.py          # Typer commands
    db.py           # SQLite schema + access
    scryfall.py     # Scryfall API client
    filters.py      # Inclusion/exclusion rules (kept in one place)
    collection.py   # Set -> card-level entry generation
    export.py       # Moxfield CSV export
  data/
    collection.db   # local database (gitignored)
  exports/          # generated CSVs (gitignored)
  pyproject.toml
  README.md
```

## Status

Early development — building the initial CLI scaffold and database schema.
