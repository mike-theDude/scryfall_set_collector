# Design & Spec

Canonical specification for `mtgsets`. Until the corresponding code exists, this
document is the source of truth for the database schema, export format, and
filtering rules. Once a concern is implemented, the code becomes authoritative and
this doc should be kept in sync (or trimmed to rationale only).

---

## Definition of a "full set"

The unit of ownership is a **complete set**. Expanding a full set yields one paper,
English, nonfoil copy of every card in the regular main set, including basic lands.

**Included**

- All regular main-set cards
- Basic lands from the main set
- One paper, English, nonfoil copy by default

**Excluded**

- Borderless / showcase / extended-art variants
- Commander deck cards
- Tokens
- Art cards
- Promos
- Serialized cards
- Oversized cards
- Digital-only cards
- Collector-booster alternate treatments
- Any special variants outside the regular main set

---

## Filtering rules

All inclusion/exclusion logic lives in **one place** (`mtgsets/filters.py`) so it can
be tuned after testing against real set data. Do not scatter filter conditions
across the codebase, and do not over-hard-code early. **`filters.py` is the
authoritative source for these rules**; this section is kept in sync as rationale.

A printing is included **iff** none of the ordered exclusion checks below fire
(`filters.exclusion_reason` returns `None`); `filters.is_main_set_card` is the
boolean form. Checks are ordered so the reason returned is the most informative
bucket for `preview` (a borderless promo reports as a promo; a boosterfun showcase
as a variant rather than merely "not in set").

Exclusion checks, in priority order:

1. **Non-English / non-paper / digital-only** — `lang != "en"`, `digital == true`,
   or `paper` not in `games`.
2. **Tokens / art cards / non-playable** — `layout` in `{token, double_faced_token,
   emblem, art_series, vanguard, scheme, planar}`.
3. **Oversized** — `oversized == true`.
4. **Serialized** — `promo_types` contains `serialized`.
5. **Promos** — `promo == true`.
6. **Borderless / showcase / extended-art variants** — `border_color ==
   "borderless"`, a `frame_effects` of `showcase`/`extendedart`/`inverted`,
   `promo_types` containing `boosterfun`, or a `finishes` lacking `nonfoil`
   (foil-only / etched-only collector printings).
7. **Alternate printing / variation** — `variation == true`.
8. **Not in the regular set** — `booster != true`, **but only when the set has any
   `booster == true` printing at all** (`filters.set_uses_booster`). This is the
   membership gate that removes deck-exclusive / Jumpstart / promo-only extras
   carrying no other marker. It is skipped for sets where *every* printing is
   `booster == false` (see tuning notes), so the gate is evaluated per-set, not
   per-card in isolation.

**Tuning notes** (validated live against NEO → 292, MOM → 291, SOS → 281, TMT → 205):

- `booster == true` separates the regular set from deck-exclusive extras and already
  excludes every `boosterfun` collector treatment. Cards like MOM #323–337 are plain
  `normal` printings distinguishable only by `booster == false`.
- The `booster` flag is **only meaningful when the set actually uses it.** Some recent
  sets (Secrets of Strixhaven `SOS`, Teenage Mutant Ninja Turtles `TMT`) mark *every*
  printing `booster == false`; applying the gate unconditionally filtered them to zero
  cards (issue #50). So check 8 fires only when `set_uses_booster` is true for the set.
- A `finishes` without `nonfoil` (foil-only or etched-only) marks a collector treatment,
  not a regular card — e.g. TMT's surgefoil basics `#305–314`. The regular set always
  has a nonfoil copy, so these are excluded at check 6.
- Most `frame_effects` are **intrinsic** to the regular printing and must NOT be
  treated as variants: `legendary`, `enchantment`, `fandfc`, `fullart`, etc. Only
  `showcase`, `extendedart`, `inverted` mark alternate treatments.
- Basic lands of the main set are kept (they are `booster == true`). NEO's Japanese
  full-art "ukiyo-e" basics arrive as `lang == "ja"` and drop out at check 1.

**Scryfall fields used:** `lang`, `games`, `digital`, `layout`, `oversized`,
`promo`, `promo_types`, `variation`, `booster`, `border_color`, `frame_effects`,
`finishes` (plus `id`, `name`, `set`, `collector_number`, `type_line`, `rarity` for
storage/display and the full JSON object).

---

## Database schema (SQLite)

### `owned_sets`

| Column | Type | Notes |
|---|---|---|
| `set_code` | TEXT | PRIMARY KEY |
| `set_name` | TEXT | NOT NULL |
| `quantity` | INTEGER | NOT NULL DEFAULT 1 |
| `language` | TEXT | NOT NULL DEFAULT 'English' |
| `condition` | TEXT | NOT NULL DEFAULT 'Near Mint' |
| `foil` | INTEGER | NOT NULL DEFAULT 0 |
| `profile` | TEXT | NOT NULL DEFAULT 'main_set_plus_basics' |
| `added_at` | TEXT | NOT NULL |

### `cards` (cached Scryfall data)

| Column | Type | Notes |
|---|---|---|
| `scryfall_id` | TEXT | PRIMARY KEY |
| `name` | TEXT | NOT NULL |
| `set_code` | TEXT | NOT NULL |
| `collector_number` | TEXT | NOT NULL |
| `lang` | TEXT | |
| `rarity` | TEXT | |
| `type_line` | TEXT | |
| `digital` | INTEGER | NOT NULL |
| `promo` | INTEGER | NOT NULL |
| `variation` | INTEGER | NOT NULL |
| `booster` | INTEGER | |
| `full_json` | TEXT | NOT NULL (raw Scryfall object) |

`cards` holds the shared printing records used by both reference snapshots and owned
collection entries. A row in this table does **not** imply ownership. Likewise, a card
may remain here after it leaves a current reference snapshot when a manual, full-set, or
override entry still references that printing.

### `card_cache_sets` (per-set card snapshot metadata)

One row records that the app has a complete, filtered reference snapshot for a set.
This is independent of `owned_sets`: syncing an unowned set creates this row without
claiming that the user owns it.

| Column | Type | Notes |
|---|---|---|
| `set_code` | TEXT | PRIMARY KEY |
| `set_name` | TEXT | NOT NULL |
| `synced_at` | TEXT | NOT NULL (ISO 8601; when this set's cards were fetched) |

### `card_cache_entries` (membership in a card snapshot)

| Column | Type | Notes |
|---|---|---|
| `set_code` | TEXT | NOT NULL, FK -> `card_cache_sets(set_code)` ON DELETE CASCADE |
| `scryfall_id` | TEXT | NOT NULL, FK -> `cards(scryfall_id)` |

The primary key is `(set_code, scryfall_id)`. Re-syncing a set replaces these
memberships with the printings that currently pass the normal full-set filter. Added
printings are inserted, removed printings leave the snapshot, and existing `cards`
rows are updated from the latest Scryfall objects. A removed printing is deleted from
`cards` only when neither a cache snapshot nor a `collection_entries` row still
references it.

### `scryfall_sets` (cached Scryfall set list)

A local cache of Scryfall's full set list, so `search` and `stats` don't paginate
~1000 sets over the network on every run (and keep working offline). One row per set;
all rows share a single `fetched_at` so the cache is an atomic snapshot with one age.

| Column | Type | Notes |
|---|---|---|
| `code` | TEXT | PRIMARY KEY (Scryfall set code) |
| `full_json` | TEXT | NOT NULL (raw Scryfall set object) |
| `fetched_at` | TEXT | NOT NULL (ISO 8601; when the snapshot was fetched) |

Consumers read the cache when it's younger than the **24h TTL** and re-fetch (replacing
the whole snapshot) when it's stale, empty, or `--refresh-sets` is passed. If a needed
re-fetch fails but a stale cache exists, the stale copy is used rather than failing.

### `collection_entries` (generated card-level entries)

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER | PRIMARY KEY AUTOINCREMENT |
| `scryfall_id` | TEXT | NOT NULL, FK -> `cards(scryfall_id)` |
| `set_code` | TEXT | NOT NULL |
| `quantity` | INTEGER | NOT NULL DEFAULT 1 |
| `condition` | TEXT | NOT NULL DEFAULT 'Near Mint' |
| `language` | TEXT | NOT NULL DEFAULT 'English' |
| `foil` | INTEGER | NOT NULL DEFAULT 0 |
| `source_type` | TEXT | NOT NULL — see below |
| `source_set_code` | TEXT | set code that generated this entry |
| `exported_at` | TEXT | NULL until the entry is written to a Moxfield CSV (issue #76); ISO 8601 timestamp once exported |

`exported_at` is **entry-level** (not set-level) so manual `add-card` singles take part
in the incremental export uniformly alongside `full_set` entries, and a partial export
stays consistent. It backs the delta export (see "Moxfield CSV export").

**`source_type` values**

- `full_set` — generated by expanding an owned set (`source_set_code = <set code>`)
- `manual` — a manually added single (`mtgsets add-card`; `source_set_code = NULL`)
- `override` — a per-printing correction to a generated full-set card
  (`mtgsets override-card`; `source_set_code = <set code>`)

> **Why this matters:** removing a full set must delete **only** the
> `collection_entries` with `source_type = full_set` and `source_set_code = <set>`.
> Manually added singles and overrides must never be touched by a set removal.

`manual` singles **coexist** with `full_set` entries for the same card — they are
separate rows and are never merged. `add-card` stacks the quantity of an existing
identical manual row (same printing, finish, condition, language) instead of inserting
a duplicate; `remove-card` deletes only `manual` rows for a printing, never `full_set`
or `override`. This is the symmetric counterpart to the set-removal invariant above.

### `override` — correcting a single full-set printing

A full set generates one nonfoil, English, Near Mint, quantity-1 entry per included
printing. An `override` records that **your** copy of one of those printings differs —
e.g. it's foil, played, a different language, or you have several. It is keyed by
`(scryfall_id, source_set_code)`: at most one override per printing per set.

- **Created/updated** by `mtgsets override-card <SET> <NUMBER>` with any of
  `--quantity`, `--foil/--nonfoil`, `--condition`, `--language`. The printing is
  resolved from the locally cached full-set entry, so the card **must already be part
  of an owned full set** (no Scryfall call); otherwise the command errors and points
  you at `add` / `add-card`. Re-running on the same printing **replaces** the override
  (it is not stacked — an override is a correction, not an additive single).
- **Cleared** by `mtgsets override-card <SET> <NUMBER> --clear`, which deletes the
  override row. The generated `full_set` row is untouched, so the printing reverts to
  the full-set defaults.
- **Coexists** with the generated `full_set` row (both rows remain in the table). The
  override does not delete or mutate the `full_set` row, which keeps `refresh` and
  `remove` simple: they only ever target `full_set` rows, so an override survives both
  with no special-casing (mirroring the `manual` invariant).
- **Export** suppresses the generated `full_set` row whenever an `override` exists for
  the same `(scryfall_id, source_set_code)`, emitting the override's corrected values
  in its place (with the same `Full Set: <CODE>` tag). Without this suppression the
  printing would export twice. See "Moxfield CSV export" below.

---

## Moxfield CSV export

**Columns**

`Count`, `Tradelist Count`, `Name`, `Edition`, `Condition`, `Language`, `Foil`,
`Tags`, `Collector Number`

**Example row**

```
1,0,"Boseiju, Who Endures",NEO,Near Mint,English,,Full Set: NEO,266
```

**Defaults for generated full-set entries**

- Quantity: `1`
- Condition: `Near Mint`
- Language: `English`
- Foil: false / nonfoil
- Tags: `Full Set: <SET_CODE>`

**Overrides** (`source_type = override`) export with the user-supplied
quantity/condition/language/foil and the same `Full Set: <CODE>` tag. The generated
`full_set` row for that same `(scryfall_id, source_set_code)` is **suppressed** from
the export so the printing is emitted once, with the corrected values. See
"`override` — correcting a single full-set printing" above.

The app should also store the Scryfall ID internally for traceability even though
Moxfield ignores it.

**Import** (`mtgsets import-csv`) reads this **same format** — there is no separate
import schema, so an exported collection round-trips. Parsing is header-based and
case-insensitive, so reordered/extra columns are tolerated; only `Edition` +
`Collector Number` (the printing key) are required, and `Count`/`Condition`/
`Language`/`Foil` populate the entry. Imported cards are inserted as `manual` singles
(`source_type = manual`). Rows tagged `Full Set: <CODE>` are **skipped** (whole sets
are added via `add`), and re-importing a printing stacks onto its existing manual
quantity rather than duplicating it.

### Incremental export (delta, issue #76)

Moxfield's collection import is **additive** — re-importing a card stacks its quantity
rather than replacing it. So `export moxfield` is a **delta by default**: it emits only
entries with `exported_at IS NULL` and, on a successful write, stamps those rows with
the current timestamp. Re-running after adding more sets therefore pushes just the new
cards instead of doubling what's already in Moxfield. The delta is idempotent — a second
export immediately after the first emits nothing.

Flags (each stamps the rows it emits):

- *(default)* — new entries only (`exported_at IS NULL`). If nothing is new, print
  "nothing new to export" and exit 0 without writing; a genuinely empty collection still
  errors ("nothing to export — the collection is empty").
- `--all` — the whole collection, regardless of state (first import, or rebuilding a
  wiped Moxfield library). Re-stamps every emitted row.
- `--set <CODE>` — just that set's entries (its `full_set` rows and any `override`s),
  regardless of state.

Override suppression applies in every mode (an overridden `full_set` row is omitted in
favour of its `override`).

**Removals don't round-trip.** A CSV import can't delete or decrement, so removing a set
in mtgsets after exporting it can't pull those cards out of Moxfield. To help (issue #85),
`mtgsets remove <CODE>` reports how many of the set's cards were already exported and, if
any were, tells you to filter on the `Full Set: <CODE>` tag in Moxfield and bulk-delete.
The deletion is still manual on Moxfield's side.

---

## JSON export

`mtgsets export json` writes a structured snapshot of the whole collection — for
backups and scripting, where the Moxfield CSV (shaped for re-import) is too lossy. It
captures the owned sets **and** every card-level entry, including `source_type` and
`source_set_code`, which the CSV collapses into a `Full Set:` tag.

Output is deterministic: fixed key order, 2-space indent, a trailing newline, and
`ensure_ascii=False` so non-ASCII card names stay readable. `version` is bumped if the
shape changes. The same override suppression as the CSV applies (a `full_set` entry
shadowed by an `override` is omitted), so the snapshot mirrors the real collection.

```json
{
  "version": 1,
  "owned_sets": [
    { "set_code": "neo", "set_name": "Kamigawa: Neon Dynasty", "added_at": "2024-02-01T00:00:00+00:00" }
  ],
  "entries": [
    {
      "name": "Boseiju, Who Endures",
      "set_code": "neo",
      "collector_number": "266",
      "quantity": 1,
      "condition": "Near Mint",
      "language": "English",
      "foil": false,
      "source_type": "full_set",
      "source_set_code": "neo"
    }
  ]
}
```

---

## Preview behavior

`mtgsets preview <set_code>` must show what will be **included** and **excluded**
before a set is added, because Magic set data has many variants and edge cases.

Example:

```
Set: Kamigawa: Neon Dynasty (NEO)

Included:
- Regular main-set cards
- Main-set basic lands

Excluded:
- Tokens
- Art cards
- Commander cards
- Promos
- Borderless/showcase/extended art variants

Included count: ___
Excluded count: ___
```

---

## Data source notes

Use the [Scryfall API](https://scryfall.com/docs/api). Initially the app can make
search/API calls by set code. Later it can switch to Scryfall **bulk data** with
local caching if needed.

There are three deliberately separate kinds of local state:

- The **set-list cache** (`scryfall_sets`, 24h TTL) is Scryfall catalog metadata used by
  `search` and `stats`; it contains no card contents and says nothing about ownership.
- The **card-data cache** (`card_cache_sets`, `card_cache_entries`, and the shared
  `cards` rows) stores the latest filtered contents of individually fetched sets.
  `mtgsets sync-cards <SET>...`, `add`, and `refresh` replace these per-set snapshots.
- **Owned collection data** (`owned_sets` and `collection_entries`) is changed only by
  explicit collection commands such as `add`, `add-card`, `override-card`, and
  `remove`. Merely syncing card data never creates, removes, or regenerates ownership
  rows and therefore never affects collection stats or exports.
