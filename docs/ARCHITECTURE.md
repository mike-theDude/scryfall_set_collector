# Architecture

How `mtgsets` is put together and why. Read alongside [DESIGN.md](DESIGN.md) (the
data spec: schema, filter rules, export format), [STYLE.md](STYLE.md) (coding
conventions), and [../CLAUDE.md](../CLAUDE.md) (workflow rules).

This document is **descriptive** — it captures the structure the code already
follows. If you change the structure, update this doc in the same PR.

---

## Layers

The package is three layers with a strict, one-directional dependency rule:

```
        cli.py                 ← Typer commands + Rich rendering (the only I/O orchestrator)
          │
          ├── collection.py    ┐
          ├── stats.py         │  pure logic — no I/O
          ├── filters.py       │  (set expansion, statistics, include/exclude, CSV shaping)
          ├── wantlist.py      │
          └── export.py        ┘
          │
          ├── db.py            ┐  I/O
          └── scryfall.py      ┘  (SQLite, Scryfall HTTP)
```

- **`cli.py` depends on everything; nothing depends on `cli.py`.**
- **Pure-logic modules depend only on each other and the standard library** — never on
  `db` or `scryfall`.
- **I/O modules (`db`, `scryfall`) depend on nothing else in the package** (besides
  `__init__` for the version string).

## The CLI is thin

`cli.py` orchestrates and renders; it holds **no business logic**. A command's job is:

1. Parse arguments (Typer).
2. Call into the I/O layer to fetch/read.
3. Call pure-logic functions to compute.
4. Call the I/O layer to write (inside one transaction — see below).
5. Render the result with Rich.

Anything that decides *what a full set is*, *how a set expands into entries*, *how a
stat is computed*, or *what a CSV row looks like* belongs in a pure-logic module, not in
`cli.py`. The test for "is this in the right place": pure-logic functions are unit-tested
with **no mocks and no I/O** (see `build_breakdown`, `build_stats`, `entry_to_row`,
`exclusion_reason`). If a new function would need a mock to test, it's probably I/O and
belongs in `db`/`scryfall`, or it's mixing layers.

## Pure logic does no I/O

`collection`, `stats`, `filters`, `wantlist`, and `export` perform no network or database
access. They take plain data in (raw Scryfall dicts, owned-set rows, lists) and return
plain data or dataclasses out. This is what makes the arithmetic-heavy parts (`stats.py`)
and the intricate parts (`filters.py`) cheap and deterministic to test.

`export.write_moxfield_csv` and `wantlist.write_csv` touch the filesystem — those are
deliberate format *sinks*; their row-shaping logic stays pure.

## The network boundary

All Scryfall access goes through `scryfall.ScryfallClient`, and every failure surfaces as
a single exception type, `ScryfallError` (carrying an optional `status_code`). The CLI is
the **only** place that catches it: it maps the error to a friendly message and
`raise typer.Exit(1)`, special-casing `404` ("no set found — try `mtgsets search`").

Transient failures are absorbed below that boundary: `_get` retries `429` and `5xx` with
bounded exponential backoff (honouring `Retry-After`) before giving up and raising
`ScryfallError`. `404` and other `4xx` are non-retryable and surface immediately, so the
CLI's `404` handling is unaffected.

Pure-logic and I/O modules never print and never call `typer.Exit`. Keeping the catch at
the CLI edge means one consistent place decides how a network failure looks to the user.

The client is constructed with an injectable `transport` (defaulting to `None` =
real network). Tests pass an `httpx.MockTransport`, so **the suite makes no real network
calls.**

## Storage and the core invariant

The SQLite schema and the full-set filter rules are specified in [DESIGN.md](DESIGN.md);
that doc is canonical for them — this section only links the rationale.

Two invariants drive the architecture and must be preserved by any code touching
`collection_entries`:

- **`source_type` makes set removal safe.** Generated rows are tagged `full_set` with a
  `source_set_code`; removing a set deletes *only* those rows, never `manual` singles or
  `override` rows. See `db.remove_owned_set` / `db.delete_full_set_entries`.
- **All include/exclude logic lives in `filters.py`** — the single source of truth, tuned
  against real Scryfall data. Do not scatter filter conditions elsewhere.

Reference card snapshots are a separate concern from ownership. `card_cache_sets` and
`card_cache_entries` describe the current filtered contents known for a set, while the
shared `cards` rows hold each raw Scryfall printing. Only `owned_sets` and
`collection_entries` claim ownership. This lets `sync-cards` replace an unowned set's
reference snapshot without changing anything consumed by list, stats, or exports.

## Command flow (example: `add`)

```
cli.add
  → scryfall.get_set / get_set_cards        (I/O: fetch)
  → collection.build_breakdown              (pure: apply filters, partition)
  → collection.generate_full_set_entries    (pure: one entry per included card)
  → db.upsert_cards / insert_owned_set /     (I/O: one transaction, cli commits)
    insert_collection_entries
  → console.print(...)                       (render)
```

`preview` runs the same fetch + `build_breakdown` and stops before any write — which is
why `preview`-before-`add` is cheap and consistent: they share the pure core.

`sync-cards` shares the fetch and filter stages, then stops at
`db.replace_cached_set_cards`: it updates snapshot membership and card fields but never
calls an ownership or collection-entry write. Each requested set has its own transaction
so a failed batch item cannot roll back a successful one.

`want-list` reads exact printings from that cache first and sends only misses through
`scryfall.get_card`. Resolved raw cards are mapped and rendered by `wantlist.py`; the CLI
does not call any database write helper. Plain output bypasses Rich entirely so stdout is
copy/redirect-safe, while per-number errors and the partial-failure summary use stderr.
