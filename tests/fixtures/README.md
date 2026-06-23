# Test fixtures

These JSON files are **real Scryfall card objects**, captured live and then
*trimmed* — the bulky fields the app never reads (`image_uris`, `prices`,
`*_uris`, `legalities`, `rulings_uri`, …) are stripped so the committed fixtures
stay small. Every gameplay/treatment field the code does read is preserved
verbatim: `lang`, `games`, `digital`, `layout`, `oversized`, `promo`,
`promo_types`, `border_color`, `frame_effects`, `finishes`, `variation`,
`booster`, `type_line`, plus identity fields (`id`, `name`, `set`,
`collector_number`, `rarity`).

We use **real** data on purpose: the full-set filter rules in
`mtgsets/filters.py` were tuned against live Scryfall output, so synthetic cards
could mask a regression the real shapes would catch.

## Files

| File | Contents |
|---|---|
| `neo_cards.json` | A trimmed subset of Kamigawa: Neon Dynasty (NEO) `unique=prints`: included main cards (plain + intrinsic `legendary`/`enchantment`/`fandfc` frames), a basic land, and the excluded reasons NEO actually contains (non-English, borderless/showcase variant, promo). |
| `exclusion_samples.json` | One real card per exclusion reason NEO's print data lacks — token (TNEO), serialized (BRR), oversized (OLEP), variation (AER), and not-in-the-regular-set (MOM #332). Pulled from across sets so every filter branch has a real representative. |
| `booster_false_sets.json` | Cards from sets where *every* printing is `booster == false` (SOS, TMT) — issue #50. Main cards + a basic that must be **included** despite `booster == false`, plus a borderless variant (SOS) and a surgefoil foil-only basic (TMT #305) that must still be **excluded**. Exercises the set-relative membership gate (`filters.set_uses_booster`). |

Between the two files, every branch of `filters.exclusion_reason` is exercised by
a real card.

## Refreshing

The capture scripts live in the PR history for issue #27. To regenerate, fetch
the relevant printings from the Scryfall API, run them through
`filters.exclusion_reason` to bucket them, pick one representative per reason
(plus the included cases), and drop the heavy keys listed above. Keep the set
small and reviewable — a dozen cards, not a whole set.
