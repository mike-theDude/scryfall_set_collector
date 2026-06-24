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

The **set list** is already cached locally (`scryfall_sets`, 24h TTL) so `search` and
`stats` don't re-paginate every set on each run and work offline once warm — see the
schema above. Per-set card data is still fetched on demand (cached in `cards`).
