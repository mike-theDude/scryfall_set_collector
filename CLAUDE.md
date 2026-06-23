# CLAUDE.md

Guidance for working in this repo. Read alongside [README.md](README.md) (user-facing
usage), [docs/DESIGN.md](docs/DESIGN.md) (the canonical schema / export / filter spec),
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) (layering and module responsibilities), and
[docs/STYLE.md](docs/STYLE.md) (coding conventions).

## Follow the design doc (enforced)

**Read [docs/DESIGN.md](docs/DESIGN.md) before writing or changing any code, and build
to it.** It is the canonical spec for the database schema, the Moxfield export format,
and the full-set filtering rules. Implementations must match it — same table/column
definitions, same export columns and defaults, same include/exclude logic.

- Do not invent schema, export columns, or filter conditions that contradict the doc.
- If the design needs to change, **update `docs/DESIGN.md` first** (in the same PR),
  then implement — never let code and spec silently diverge.
- Once code becomes the source of truth for a concern, keep the doc in sync (or trim it
  to rationale). The doc and the code must always agree.

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
  stats.py        # collection statistics (sets owned vs. total)
  export.py       # Moxfield CSV export + import
data/collection.db  # local db (gitignored)
exports/            # generated CSVs (gitignored)
```

## Workflow — issue branches (enforced)

All issue work happens on a dedicated branch. **Do not commit issue work directly to
`main`.**

- **One branch per issue**, cut from an up-to-date `main`.
- **Branch name:** `<issue#>-<short-kebab-context>` — the issue number first, then a
  few words of context from the issue title. Examples:
  - `4-filters-full-set-rules`  (issue #4)
  - `2-init-sqlite-schema`  (issue #2)
  - `9-safe-set-removal`  (issue #9)
- Before starting an issue: `git checkout main && git pull`, then
  `git checkout -b <issue#>-<context>`.
- Reference the issue in commits, and open a PR whose description closes it
  (`Closes #<issue#>`) so merging auto-closes the issue.
- Keep a branch scoped to its single issue; spin up a new branch for a new issue.

## Merging — CI must be fully green (enforced)

**A PR may not be merged until CI is fully passing.** This is an absolute condition,
not a judgement call: never merge a PR with a failing, pending, or skipped required
check, and never bypass it.

- All five required checks must pass: `lint (ruff)` and `test (py3.10/3.11/3.12/3.13)`.
  This includes `ruff format --check` — run `ruff format .` before pushing.
- `main` is protected on GitHub with these required status checks, `strict` mode
  (the branch must be up to date with `main`), and `enforce_admins` (no override).
  `gh pr merge` will refuse until every check is green — do not work around it.
- Before merging, confirm green CI (e.g. `gh pr checks <#>`); prefer
  `gh pr merge <#> --squash --auto` so the merge happens only once checks pass.

## Conventions

- Keep the schema and export format in sync with `docs/DESIGN.md`; once code is the
  source of truth for a concern, update the doc to match (or trim it to rationale).
- Roadmap and feature work are tracked as GitHub issues/milestones, not in this file.
