# Style Guide

Coding conventions for `mtgsets`. Read alongside [ARCHITECTURE.md](ARCHITECTURE.md)
(layering), [DESIGN.md](DESIGN.md) (data spec), and [../CLAUDE.md](../CLAUDE.md)
(workflow).

This document is **descriptive** — it captures conventions the code already follows, so
new contributions read like the surrounding code. Tooling enforces formatting; the rest
is convention.

---

## Tooling (enforced by CI)

- **Python 3.10+.** `from __future__ import annotations` at the top of every module
  (lets us write `X | None`, `list[str]`, etc. uniformly).
- **ruff** for lint and format, line length **100**. Run `ruff format .` and
  `ruff check .` before pushing — CI runs `ruff format --check` and will fail otherwise.
- Lint rule sets: `E`, `W`, `F`, `I` (import sorting), `UP` (pyupgrade), `B` (bugbear).
- **pytest**, no network — the Scryfall client is driven by `httpx.MockTransport`.

## Database access

- **Functions take an open `conn` as their first argument** and do their work on it.
- **Helpers do not commit.** The docstring says so explicitly: *"Does not commit (caller
  owns the transaction)."* The **CLI owns the transaction** — it wraps the write calls in
  `try/except`, calls `conn.commit()` on success, and `conn.rollback()` on failure. The
  exceptions are the few functions documented as committing themselves
  (`upsert_cards`, `remove_owned_set`, `remove_manual_card`); follow the existing pattern
  for the module you're editing rather than introducing a new one.
- Connections are opened with `db.get_connection` (row factory + `PRAGMA foreign_keys =
  ON`) and closed in a `finally`.
- Keep SQL in `db.py`. The CLI has a couple of tiny inline `SELECT`s; prefer a named
  `db` function for anything non-trivial or reused.
- Cards must be cached (`upsert_cards`) before `collection_entries` reference them — the
  foreign key requires it.

## Rich output conventions

Colors carry consistent meaning across commands — match them:

| Markup | Meaning |
|---|---|
| `[green]` | success / the affirmative count |
| `[yellow]` | warning, skip, "already owned", "not owned" |
| `[red]` | failure / error |
| `[cyan]` | set codes |
| `[bold]` | labels, command names in hints |
| `[dim]` | secondary detail, parenthetical counts, dates |

- Point the user at the next command in `[bold]`, e.g. *"Add it with
  `mtgsets add NEO`."*
- Best-effort multi-item commands print **one line per item** plus a final summary line.

## Set codes

- **Stored and compared lowercase**; **displayed uppercase** (`code.upper()`, often as
  `display_code`). Normalize input with `.strip().lower()` at the boundary.

## Naming and structure

- **Module-private helpers are `_prefixed`** (`_load_set`, `_add_one_set`,
  `_is_basic_land`, `_print_breakdown`).
- **Structured results are dataclasses**, not ad-hoc tuples/dicts, when they cross a
  function boundary or get rendered: `SetBreakdown`, `GeneratedEntry`, `ImportRow`,
  `CollectionStats`, `ProgressStats`, `YearStats`, `ValueStats`. Computed views are
  `@property`; frozen where immutable.
- **User-facing category labels are stable module constants**, not inline strings — the
  `REASON_*` pattern in `filters.py`. This keeps `preview` buckets and tests in sync.
- **Best-effort operations return a status string** (`"added"` / `"skipped"` /
  `"failed"`, or `("added", new_qty)`) rather than raising, so a multi-item caller can
  tally outcomes and keep going. Reserve exceptions for genuine errors.

## Docstrings and comments

- **Every module opens with a docstring** stating its job and linking the relevant spec
  (`See docs/DESIGN.md ...`). Keep these links accurate when responsibilities move.
- Public functions get a one-line summary; add a short paragraph when the *why* isn't
  obvious (transaction ownership, an invariant being protected, a Scryfall quirk).
- Comments explain **why**, not what. The valuable comments in this codebase flag
  non-obvious data realities (e.g. the `booster`-flag tuning notes, the `unique=prints`
  rationale) — that's the bar.

## Tests

- One test module per source module (`test_filters.py` ↔ `filters.py`, …).
- Pure-logic tests use **no mocks**. I/O tests use `httpx.MockTransport` (Scryfall) or a
  temp SQLite db (`db`). Never hit the real network.
- When you touch an invariant (set-removal safety, manual/full_set/override coexistence,
  export byte-for-byte format), add or extend the test that pins it.
