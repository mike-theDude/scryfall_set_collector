# CLAUDE.md

Guidance for working in this repo. Read alongside [README.md](README.md) (user-facing
usage) and [docs/DESIGN.md](docs/DESIGN.md) (the canonical schema / export / filter spec).

## What this is

`mtgsets` — a Python CLI for managing a Magic: The Gathering collection by **full
set**. The user collects complete sets; the app expands an owned set into card-level
entries and exports them for import into Moxfield's collection library.

## Core design principle

**Store ownership at the set level, but generate, export, and check ownership at the
card level.** Owning is recorded as "I have all of NEO"; everything downstream
(expansion, export, deck checks) operates on individual cards.

## Architectural decisions (the "why")

- **All filter logic lives in `mtgsets/filters.py`.** The "full set = main set + basic
  lands, minus variants/promos/tokens/etc." rules are intricate and will need tuning
  against real Scryfall data. Keep them in one place; do not scatter conditions or
  over-hard-code early.
- **`collection_entries.source_type` makes set removal safe.** Generated entries are
  tagged `full_set` with a `source_set_code`. Removing a set deletes **only** those
  entries — never `manual` singles or `override` entries. Preserve this invariant in
  any code that touches `collection_entries`.
- **Cache Scryfall data in the `cards` table** (raw object in `full_json`). Start with
  per-set API calls; bulk data + caching can come later.
- **`preview` before `add`.** Adding a set should be predictable — `preview` shows the
  included/excluded breakdown and counts so the user can sanity-check before writing.

## Stack

- Python · [Typer](https://typer.tiangolo.com/) (CLI) · [Rich](https://rich.readthedocs.io/) (output)
- httpx (or requests) for HTTP · SQLite for storage
- Data source: [Scryfall API](https://scryfall.com/docs/api)

## Layout

```
mtgsets/
  cli.py          # Typer commands
  db.py           # SQLite schema + access  (schema spec: docs/DESIGN.md)
  scryfall.py     # Scryfall API client
  filters.py      # inclusion/exclusion rules — single source of truth
  collection.py   # set -> card-level entry generation
  export.py       # Moxfield CSV export
data/collection.db  # local db (gitignored)
exports/            # generated CSVs (gitignored)
```

## Conventions

- Keep the schema and export format in sync with `docs/DESIGN.md`; once code is the
  source of truth for a concern, update the doc to match (or trim it to rationale).
- Roadmap and feature work are tracked as GitHub issues/milestones, not in this file.
