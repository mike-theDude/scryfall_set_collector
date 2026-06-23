# scryfall_set_collector

`mtgsets` — a command-line app for managing a Magic: The Gathering collection by
**full set**.

I collect Magic by completing whole sets. Rather than scanning or hand-entering every
card, this app lets me mark a set as fully owned, automatically expands it into
individual card entries using the [Scryfall API](https://scryfall.com/docs/api), and
exports the result as a CSV I can import into
[Moxfield](https://www.moxfield.com/)'s collection.

```
mtgsets add NEO        →  generates 292 card entries (282 main + 10 basics)
mtgsets export moxfield →  exports/moxfield.csv, ready for Moxfield import
```

## How it works

The guiding principle is: **store ownership at the set level, but generate, export,
and check ownership at the card level.** Owning is recorded as "I have all of NEO";
everything downstream operates on individual cards.

1. **Fetch** — `add`/`preview` pull every printing in a set from Scryfall
   (`/cards/search?unique=prints`), variants included.
2. **Filter** — `mtgsets/filters.py` keeps only the regular main set: one paper,
   English, nonfoil copy of each card, basic lands included. It excludes borderless /
   showcase / extended-art variants, promos, serialized cards, tokens, art cards,
   oversized and digital-only cards, and deck-exclusive extras. The rules are tuned
   against real Scryfall data and live in **one place** so they can be adjusted.
3. **Store** — owned sets go in an `owned_sets` row; the filtered cards are cached in
   `cards` and expanded into card-level `collection_entries` (tagged
   `source_type=full_set`). SQLite, stored at `data/collection.db`.
4. **Export** — `collection_entries` are written to a Moxfield-importable CSV.

Generated entries are tagged with their source set, so `remove` deletes **only** the
cards a set generated and never touches manually added singles or overrides.

The full schema, filter rules, and export format are specified in
[docs/DESIGN.md](docs/DESIGN.md).

## What "full set" means

**Included:** all regular main-set cards, basic lands from the main set, one paper /
English / nonfoil copy each.

**Excluded:** borderless / showcase / extended-art variants, Commander and other
deck-exclusive cards, tokens, art cards, promos, serialized cards, oversized cards,
digital-only cards, and collector-booster alternate treatments.

Run `mtgsets preview <set_code>` to see the exact included/excluded breakdown for a
set *before* adding it.

## Requirements

- Python 3.10+
- Network access to the Scryfall API

## Setup

Clone the repo and install it into a virtual environment (editable install, so the
`mtgsets` command tracks your local code):

```bash
git clone https://github.com/mike-theDude/scryfall_set_collector.git
cd scryfall_set_collector

python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

pip install -e .
```

Verify it's installed:

```bash
mtgsets --help
```

## Usage

Create the local database once, then add the sets you own and export:

```bash
mtgsets init                 # create data/collection.db
mtgsets search neo           # find a set's code
mtgsets preview NEO          # check what will be included/excluded
mtgsets add NEO              # mark owned + generate card entries
mtgsets list                 # review what you own
mtgsets export moxfield      # write exports/moxfield.csv
```

Then import `exports/moxfield.csv` into Moxfield via your collection's
**Import** option.

### Commands

| Command | Description |
|---|---|
| `mtgsets init` | Create the local SQLite database and schema. |
| `mtgsets search <query>` | Search Scryfall sets by name or code substring. |
| `mtgsets preview <set_code>` | Show the included/excluded breakdown before adding. |
| `mtgsets add <set_code>` | Mark a set fully owned and generate its card entries. |
| `mtgsets list` | List owned sets and their generated entry counts. |
| `mtgsets remove <set_code>` | Remove a set and **only** its generated entries. |
| `mtgsets export moxfield` | Export the collection as a Moxfield CSV. |

Useful options:

- `--db-path PATH` — use an alternate database file (all DB commands).
- `mtgsets remove <set> --yes` / `-y` — skip the confirmation prompt.
- `mtgsets export moxfield --output PATH` / `-o PATH` — write the CSV elsewhere
  (default `exports/moxfield.csv`).

### Example: preview

```
$ mtgsets preview NEO

Set: Kamigawa: Neon Dynasty (NEO)

Included:
  Regular main-set cards    282
  Main-set basic lands       10

Excluded:
  Borderless / showcase / extended-art variants     201
  Non-English / non-paper / digital-only             31
  Promos                                              7

Included count: 292   Excluded count: 239   (of 531 printings)
```

### Example: export output

A Moxfield-importable CSV (`Count, Tradelist Count, Name, Edition, Condition,
Language, Foil, Tags, Collector Number`):

```csv
Count,Tradelist Count,Name,Edition,Condition,Language,Foil,Tags,Collector Number
1,0,"Boseiju, Who Endures",NEO,Near Mint,English,,Full Set: NEO,266
```

## Data & files

- `data/collection.db` — your SQLite collection (gitignored).
- `exports/*.csv` — generated exports (gitignored).

Both directories are kept in the repo but their contents stay local to you.

## Project layout

```
scryfall_set_collector/
  mtgsets/
    cli.py          # Typer commands
    db.py           # SQLite schema + access
    scryfall.py     # Scryfall API client
    filters.py      # Inclusion/exclusion rules (single source of truth)
    collection.py   # Set -> card-level entry generation
    export.py       # Moxfield CSV export
  data/             # local database (gitignored)
  exports/          # generated CSVs (gitignored)
  docs/DESIGN.md    # canonical schema / filter / export spec
  pyproject.toml
```

## Tech stack

Python · [Typer](https://typer.tiangolo.com/) (CLI) ·
[Rich](https://rich.readthedocs.io/) (output) · httpx (HTTP) · SQLite ·
[Scryfall API](https://scryfall.com/docs/api)

## Roadmap

The initial CLI is complete: `init`, `search`, `preview`, `add`, `list`, `remove`,
and `export moxfield`. Planned next: `refresh`, `stats`, `show`, single-card
`add-card`/`remove-card`, and `check-deck` (compare a decklist against your
collection). Feature work is tracked as GitHub issues.
