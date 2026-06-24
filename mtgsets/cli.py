"""mtgsets command-line interface.

A thin Typer layer. Command implementations live in the sibling modules
(db, scryfall, filters, collection, export). See docs/DESIGN.md for the spec.

This module is the scaffold (issue #1): every command is registered so the CLI
runs end to end, but the bodies are stubs filled in by issues #2-#10.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from . import collection, db, export, scryfall, stats

app = typer.Typer(
    name="mtgsets",
    help="Manage a Magic: The Gathering collection by full set; export to Moxfield.",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()

_TODO = "[yellow]not yet implemented[/yellow]"

#: How long the locally cached Scryfall set list stays fresh before a re-fetch (#68).
SETS_CACHE_TTL_HOURS = 24


def _sets_cache_is_fresh(fetched_at: str | None, now: datetime, ttl_hours: int) -> bool:
    """True if a cache stamped ``fetched_at`` is younger than ``ttl_hours``."""
    if not fetched_at:
        return False
    try:
        stamped = datetime.fromisoformat(fetched_at)
    except ValueError:
        return False
    return now - stamped < timedelta(hours=ttl_hours)


def _load_all_sets(
    conn,
    *,
    force_refresh: bool = False,
    ttl_hours: int = SETS_CACHE_TTL_HOURS,
) -> list[dict]:
    """Return Scryfall's full set list, served from the local cache when fresh (#68).

    The list (~1000 sets, paginated) is expensive to fetch, so it's cached in the db
    (``scryfall_sets``). A fetch happens only when the cache is empty, older than
    ``ttl_hours``, or ``force_refresh`` is set; otherwise the cached snapshot is
    returned with no network call, which is what lets ``search``/``stats`` run offline
    on a warm cache. If a needed fetch fails but a (stale) cache exists, the stale copy
    is returned rather than failing. :class:`scryfall.ScryfallError` propagates only
    when a fetch is required and there is no cache to fall back on.
    """
    cached = db.get_cached_sets(conn)
    now = datetime.now(timezone.utc)
    if (
        cached
        and not force_refresh
        and _sets_cache_is_fresh(db.get_sets_fetched_at(conn), now, ttl_hours)
    ):
        return cached
    try:
        with scryfall.ScryfallClient() as client:
            sets = client.get_sets()
    except scryfall.ScryfallError:
        if cached:
            return cached  # network down but a stale cache beats no data
        raise
    db.replace_cached_sets(conn, sets, now.isoformat())
    return sets


@app.command()
def init(
    db_path: Path = typer.Option(db.DB_PATH, "--db-path", help="Database file location."),
) -> None:
    """Create the local SQLite database and schema."""
    created = db.init_db(db_path)
    if created:
        console.print(f"[green]Initialized[/green] database at [bold]{db_path}[/bold]")
    else:
        console.print(
            f"[yellow]Database already exists[/yellow] at [bold]{db_path}[/bold] (schema ensured)"
        )


@app.command()
def search(
    query: str = typer.Argument(..., help="Set name or code substring."),
    db_path: Path = typer.Option(db.DB_PATH, "--db-path", help="Database file location."),
    refresh_sets: bool = typer.Option(
        False,
        "--refresh-sets",
        help="Force a re-fetch of the Scryfall set list, ignoring the cache.",
    ),
) -> None:
    """Search Scryfall for sets by name or code.

    The set list is cached locally (24h TTL), so repeated searches are instant and
    work offline once warm. Use ``--refresh-sets`` to force a re-fetch.
    """
    db.init_db(db_path)
    conn = db.get_connection(db_path)
    try:
        all_sets = _load_all_sets(conn, force_refresh=refresh_sets)
    except scryfall.ScryfallError as exc:
        console.print(f"[red]Scryfall request failed:[/red] {exc}")
        raise typer.Exit(1) from exc
    finally:
        conn.close()

    matches = scryfall.match_sets(all_sets, query)
    if not matches:
        console.print(f"No sets match [bold]{query!r}[/bold].")
        raise typer.Exit()

    table = Table(title=f"Sets matching {query!r}")
    table.add_column("Code", style="bold cyan")
    table.add_column("Name")
    table.add_column("Type", style="dim")
    table.add_column("Released")
    table.add_column("Cards", justify="right")
    for s in matches:
        digital = s.get("digital")
        style = "dim" if digital else None
        name = s.get("name", "")
        if digital:
            name += " [dim](digital)[/dim]"
        table.add_row(
            (s.get("code") or "").upper(),
            name,
            (s.get("set_type") or "").replace("_", " "),
            s.get("released_at") or "—",
            str(s.get("card_count", 0)),
            style=style,
        )
    console.print(table)
    console.print(f"[green]{len(matches)}[/green] set(s) found.")


def _load_set(set_code: str) -> tuple[dict, list[dict]]:
    """Fetch a set object and all its printings, or exit with a message."""
    code = set_code.strip().lower()
    try:
        with scryfall.ScryfallClient() as client:
            set_obj = client.get_set(code)
            cards = client.get_set_cards(code)
    except scryfall.ScryfallError as exc:
        if exc.status_code == 404:
            console.print(
                f"[red]No set found[/red] with code [bold]{set_code.upper()}[/bold]. "
                "Try [bold]mtgsets search[/bold]."
            )
        else:
            console.print(f"[red]Scryfall request failed:[/red] {exc}")
        raise typer.Exit(1) from exc
    return set_obj, cards


@app.command()
def preview(set_code: str = typer.Argument(..., help="Set code, e.g. NEO.")) -> None:
    """Show the included/excluded breakdown before adding a set."""
    set_obj, cards = _load_set(set_code)
    breakdown = collection.build_breakdown(cards)

    code = (set_obj.get("code") or set_code).upper()
    console.print(f"\n[bold]Set:[/bold] {set_obj.get('name', '?')} ([cyan]{code}[/cyan])\n")

    console.print("[green]Included:[/green]")
    console.print(f"  Regular main-set cards   {len(breakdown.main_cards):>4}")
    console.print(f"  Main-set basic lands     {len(breakdown.basics):>4}")

    console.print("\n[yellow]Excluded:[/yellow]")
    if breakdown.excluded_count:
        for reason, count in breakdown.excluded_by_count():
            console.print(f"  {reason:<48} {count:>4}")
    else:
        console.print("  [dim](none)[/dim]")

    console.print(
        f"\n[bold]Included count:[/bold] [green]{breakdown.included_count}[/green]   "
        f"[bold]Excluded count:[/bold] [yellow]{breakdown.excluded_count}[/yellow]   "
        f"[dim](of {breakdown.total} printings)[/dim]"
    )
    if not breakdown.included_count:
        console.print(
            "\n[yellow]Warning:[/yellow] no cards qualify for the full set — this set "
            "may be unreleased, or its printings may not match the full-set rules."
        )


def _add_one_set(conn, set_code: str) -> str:
    """Add a single set in its own transaction, best-effort.

    Fetches, filters and writes one set exactly like the single-set path, but
    never raises ``typer.Exit`` — each outcome (success, skip, failure) prints
    its own line and returns a status so a multi-set ``add`` can keep going.

    Returns one of ``"added"``, ``"skipped"``, or ``"failed"``.
    """
    display_code = set_code.strip().upper()

    # -- fetch (network boundary) -------------------------------------------
    try:
        with scryfall.ScryfallClient() as client:
            code = set_code.strip().lower()
            set_obj = client.get_set(code)
            cards = client.get_set_cards(code)
    except scryfall.ScryfallError as exc:
        if exc.status_code == 404:
            console.print(
                f"[red]Failed {display_code}:[/red] No set found (try [bold]mtgsets search[/bold])."
            )
        else:
            console.print(f"[red]Failed {display_code}:[/red] Scryfall request failed: {exc}")
        return "failed"

    code = (set_obj.get("code") or set_code).lower()
    display_code = code.upper()
    set_name = set_obj.get("name", display_code)
    breakdown = collection.build_breakdown(cards)

    if not breakdown.included_count:
        console.print(
            f"[yellow]Skipped {display_code}:[/yellow] no cards qualify for the full set "
            "(unreleased, or printings don't match the full-set rules?)."
        )
        return "skipped"

    if db.is_set_owned(conn, code):
        console.print(f"[yellow]Skipped {display_code}:[/yellow] already owned.")
        return "skipped"

    # -- write (own transaction) --------------------------------------------
    entries = collection.generate_full_set_entries(code, breakdown.included)
    added_at = datetime.now(timezone.utc).isoformat()
    try:
        db.upsert_cards(conn, breakdown.included)
        db.insert_owned_set(conn, set_code=code, set_name=set_name, added_at=added_at)
        written = db.insert_collection_entries(conn, (e.as_row() for e in entries))
        conn.commit()
    except Exception as exc:
        conn.rollback()
        console.print(f"[red]Failed {display_code}:[/red] {exc}")
        return "failed"

    console.print(
        f"[green]Added[/green] {set_name} ([cyan]{display_code}[/cyan]): "
        f"[green]{written}[/green] entries "
        f"([dim]{len(breakdown.main_cards)} main + {len(breakdown.basics)} basics[/dim])."
    )
    return "added"


@app.command()
def add(
    set_code: str = typer.Argument(..., help="Set code, e.g. NEO."),
    db_path: Path = typer.Option(db.DB_PATH, "--db-path", help="Database file location."),
) -> None:
    """Mark a set as fully owned and generate its card-level entries."""
    set_obj, cards = _load_set(set_code)
    breakdown = collection.build_breakdown(cards)
    code = (set_obj.get("code") or set_code).lower()
    display_code = code.upper()

    if not breakdown.included_count:
        console.print(
            f"[yellow]Nothing to add[/yellow] for [bold]{display_code}[/bold] — no cards "
            "qualify for the full set (unreleased or boosterless?)."
        )
        raise typer.Exit(1)

    db.init_db(db_path)
    conn = db.get_connection(db_path)
    try:
        if db.is_set_owned(conn, code):
            console.print(
                f"[yellow]{display_code} is already owned.[/yellow] Use "
                f"[bold]mtgsets remove {display_code}[/bold] first to re-add it."
            )
            raise typer.Exit(1)

        entries = collection.generate_full_set_entries(code, breakdown.included)
        added_at = datetime.now(timezone.utc).isoformat()
        try:
            db.upsert_cards(conn, breakdown.included)
            db.insert_owned_set(
                conn,
                set_code=code,
                set_name=set_obj.get("name", display_code),
                added_at=added_at,
            )
            written = db.insert_collection_entries(conn, (e.as_row() for e in entries))
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    finally:
        conn.close()

    console.print(
        f"[green]Added[/green] {set_obj.get('name', display_code)} "
        f"([cyan]{display_code}[/cyan]): generated [green]{written}[/green] entries "
        f"([dim]{len(breakdown.main_cards)} main + {len(breakdown.basics)} basics[/dim])."
    )


@app.command(name="add-multi")
def add_multi(
    set_codes: list[str] = typer.Argument(..., help="One or more set codes, e.g. NEO DFT."),
    db_path: Path = typer.Option(db.DB_PATH, "--db-path", help="Database file location."),
) -> None:
    """Mark several sets as fully owned in one run, best-effort.

    Each set is processed independently: one set failing (unknown code, already
    owned, nothing qualifies) does not abort the others. Repeated codes are
    de-duplicated. Exits non-zero if any set was skipped or failed.
    """
    # De-duplicate repeated codes, preserving first-seen order.
    seen: set[str] = set()
    codes: list[str] = []
    for raw in set_codes:
        code = raw.strip().lower()
        if code and code not in seen:
            seen.add(code)
            codes.append(raw)

    db.init_db(db_path)
    conn = db.get_connection(db_path)
    counts = {"added": 0, "skipped": 0, "failed": 0}
    try:
        for raw in codes:
            counts[_add_one_set(conn, raw)] += 1
    finally:
        conn.close()

    summary = ", ".join(f"{counts[k]} {k}" for k in ("added", "skipped", "failed") if counts[k])
    console.print(f"\n{summary}.")

    if counts["skipped"] or counts["failed"]:
        raise typer.Exit(1)


@app.command(name="add-card")
def add_card(
    set_code: str = typer.Argument(..., help="Set code, e.g. NEO."),
    collector_number: str = typer.Argument(..., help="Collector number within the set, e.g. 266."),
    db_path: Path = typer.Option(db.DB_PATH, "--db-path", help="Database file location."),
    quantity: int = typer.Option(1, "--quantity", "-q", min=1, help="How many copies to add."),
    foil: bool = typer.Option(False, "--foil/--nonfoil", help="Track the copies as foil."),
    condition: str = typer.Option("Near Mint", "--condition", help="Card condition."),
    language: str = typer.Option("English", "--language", help="Card language."),
) -> None:
    """Manually add an individual card as a single (source_type=manual).

    Identifies the exact printing by set code + collector number. Manual singles
    coexist with full-set entries — adding a card you already own via a full set is
    allowed and never alters the generated rows. Re-adding the same printing/finish
    stacks onto the existing manual quantity instead of duplicating it.
    """
    display_code = set_code.strip().upper()

    # -- fetch the exact printing (network boundary) ------------------------
    try:
        with scryfall.ScryfallClient() as client:
            card = client.get_card(set_code.strip().lower(), collector_number.strip())
    except scryfall.ScryfallError as exc:
        if exc.status_code == 404:
            console.print(
                f"[red]No card found[/red] at [bold]{display_code} {collector_number}[/bold]. "
                "Check the set code and collector number."
            )
        else:
            console.print(f"[red]Scryfall request failed:[/red] {exc}")
        raise typer.Exit(1) from exc

    card_set = (card.get("set") or set_code).lower()
    name = card.get("name", "?")

    db.init_db(db_path)
    conn = db.get_connection(db_path)
    try:
        also_full_set = (
            conn.execute(
                "SELECT 1 FROM collection_entries "
                "WHERE source_type = 'full_set' AND scryfall_id = ? LIMIT 1",
                (card["id"],),
            ).fetchone()
            is not None
        )
        try:
            db.upsert_cards(conn, [card])
            action, new_qty = db.add_manual_entry(
                conn,
                scryfall_id=card["id"],
                set_code=card_set,
                quantity=quantity,
                condition=condition,
                language=language,
                foil=int(foil),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    finally:
        conn.close()

    finish = "foil" if foil else "nonfoil"
    verb = "Added" if action == "added" else "Updated"
    console.print(
        f"[green]{verb}[/green] manual single: [bold]{name}[/bold] "
        f"([cyan]{card_set.upper()}[/cyan] {card.get('collector_number')}, {finish}) — "
        f"quantity now [green]{new_qty}[/green]."
    )
    if also_full_set:
        console.print(
            "  [dim]Note: you also own this card via a full set; the manual single is "
            "tracked separately.[/dim]"
        )


@app.command(name="remove-card")
def remove_card(
    set_code: str = typer.Argument(..., help="Set code, e.g. NEO."),
    collector_number: str = typer.Argument(..., help="Collector number within the set, e.g. 266."),
    db_path: Path = typer.Option(db.DB_PATH, "--db-path", help="Database file location."),
) -> None:
    """Remove manually added single(s) for a printing (source_type=manual only).

    Identifies the printing by set code + collector number and deletes every manual
    copy of it. Full-set entries and overrides for the same card are never touched,
    so removing a manual single never disturbs a set you own.
    """
    if not Path(db_path).exists():
        console.print("No collection database yet — nothing to remove.")
        raise typer.Exit(1)

    code = set_code.strip().lower()
    display_code = code.upper()
    number = collector_number.strip()
    conn = db.get_connection(db_path)
    try:
        rows_deleted, quantity_removed = db.remove_manual_card(conn, code, number)
    finally:
        conn.close()

    if not rows_deleted:
        console.print(
            f"[yellow]No manual single found[/yellow] for "
            f"[bold]{display_code} {number}[/bold]. "
            "(Full-set cards are removed with [bold]mtgsets remove[/bold].)"
        )
        raise typer.Exit(1)

    console.print(
        f"[green]Removed[/green] [green]{quantity_removed}[/green] manual cop(y/ies) of "
        f"[cyan]{display_code}[/cyan] {number}."
    )


@app.command(name="override-card")
def override_card(
    set_code: str = typer.Argument(..., help="Set code of the owned full set, e.g. NEO."),
    collector_number: str = typer.Argument(..., help="Collector number within the set, e.g. 266."),
    db_path: Path = typer.Option(db.DB_PATH, "--db-path", help="Database file location."),
    quantity: int = typer.Option(1, "--quantity", "-q", min=1, help="How many copies you have."),
    foil: bool = typer.Option(False, "--foil/--nonfoil", help="Your copy is foil."),
    condition: str = typer.Option("Near Mint", "--condition", help="Your copy's condition."),
    language: str = typer.Option("English", "--language", help="Your copy's language."),
    clear: bool = typer.Option(
        False, "--clear", help="Remove the override and revert to full-set defaults."
    ),
) -> None:
    """Correct one printing's details within an owned full set (source_type=override).

    A full set generates one nonfoil, English, Near Mint, quantity-1 entry per card.
    Use this when *your* copy of a single printing differs — it's foil, played, a
    different language, or you have several. The card must already be part of an owned
    full set (resolved from the local cache; no Scryfall call). The override replaces
    the generated row's values on export and survives ``refresh``/``remove``. Re-running
    replaces the override; ``--clear`` deletes it.
    """
    if not Path(db_path).exists():
        console.print(
            "No collection database yet. Add a set with [bold]mtgsets add <set>[/bold] first."
        )
        raise typer.Exit(1)

    code = set_code.strip().lower()
    display_code = code.upper()
    number = collector_number.strip()

    conn = db.get_connection(db_path)
    try:
        if clear:
            deleted = db.clear_override(conn, code, number)
            if not deleted:
                console.print(
                    f"[yellow]No override found[/yellow] for [bold]{display_code} {number}[/bold]."
                )
                raise typer.Exit(1)
            console.print(
                f"[green]Cleared[/green] override for [cyan]{display_code}[/cyan] {number} — "
                "reverted to full-set defaults."
            )
            return

        printing = db.get_full_set_printing(conn, code, number)
        if printing is None:
            console.print(
                f"[yellow]{display_code} {number} isn't part of an owned full set[/yellow], so "
                "there's nothing to override. Add the set with "
                f"[bold]mtgsets add {display_code}[/bold], or track a one-off copy with "
                f"[bold]mtgsets add-card {display_code} {number}[/bold]."
            )
            raise typer.Exit(1)

        try:
            action = db.set_override(
                conn,
                scryfall_id=printing["scryfall_id"],
                set_code=printing["set_code"],
                source_set_code=code,
                quantity=quantity,
                condition=condition,
                language=language,
                foil=int(foil),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    finally:
        conn.close()

    finish = "foil" if foil else "nonfoil"
    verb = "Set" if action == "created" else "Updated"
    console.print(
        f"[green]{verb}[/green] override: [bold]{printing['name']}[/bold] "
        f"([cyan]{display_code}[/cyan] {number}) — "
        f"{quantity}x {condition}, {language}, {finish}."
    )


@app.command(name="import-csv")
def import_csv(
    path: Path = typer.Argument(..., help="Path to a Moxfield-format CSV."),
    db_path: Path = typer.Option(db.DB_PATH, "--db-path", help="Database file location."),
) -> None:
    """Bulk-add individual cards from a Moxfield-format CSV as manual singles.

    Reads the same columns ``export moxfield`` emits, so a collection round-trips.
    Each row is added as a ``manual`` single (Edition + Collector Number identify the
    printing). ``Full Set:``-tagged rows are skipped — re-add whole sets with
    ``mtgsets add``. Re-importing a printing stacks onto its existing manual quantity.
    Unresolvable or malformed rows are reported but never abort the run.
    """
    if not path.exists():
        console.print(f"[red]No such file:[/red] [bold]{path}[/bold]")
        raise typer.Exit(1)

    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        console.print(f"[red]Could not read[/red] [bold]{path}[/bold]: {exc}")
        raise typer.Exit(1) from exc

    rows = export.parse_moxfield_csv(lines)
    if not rows:
        console.print(f"[yellow]No card rows found[/yellow] in [bold]{path}[/bold].")
        raise typer.Exit(1)

    added_rows = 0
    added_qty = 0
    skipped_full_set = 0
    malformed: list[export.ImportRow] = []
    unresolved: list[export.ImportRow] = []
    cache: dict[tuple[str, str], dict | None] = {}

    db.init_db(db_path)
    conn = db.get_connection(db_path)
    try:
        with scryfall.ScryfallClient() as client:
            for r in rows:
                if r.error:
                    malformed.append(r)
                    continue
                if r.is_full_set:
                    skipped_full_set += 1
                    continue

                key = (r.edition, r.collector_number)
                if key not in cache:
                    try:
                        cache[key] = client.get_card(r.edition, r.collector_number)
                    except scryfall.ScryfallError:
                        cache[key] = None
                card = cache[key]
                if card is None:
                    unresolved.append(r)
                    continue

                try:
                    db.upsert_cards(conn, [card])
                    db.add_manual_entry(
                        conn,
                        scryfall_id=card["id"],
                        set_code=(card.get("set") or r.edition),
                        quantity=r.quantity,
                        condition=r.condition,
                        language=r.language,
                        foil=r.foil,
                    )
                    conn.commit()
                except Exception:
                    conn.rollback()
                    unresolved.append(r)
                    continue
                added_rows += 1
                added_qty += r.quantity
    finally:
        conn.close()

    console.print(
        f"\n[green]Imported[/green] [green]{added_rows}[/green] card(s) "
        f"([green]{added_qty}[/green] cop(y/ies)) as manual singles."
    )
    if skipped_full_set:
        console.print(
            f"  [dim]Skipped {skipped_full_set} full-set row(s) — add whole sets with "
            "[bold]mtgsets add[/bold].[/dim]"
        )
    if unresolved:
        console.print(
            f"  [yellow]Unresolved {len(unresolved)} row(s)[/yellow] (set/number not found):"
        )
        for r in unresolved[:10]:
            console.print(f"    [dim]line {r.line}:[/dim] {r.edition.upper()} {r.collector_number}")
        if len(unresolved) > 10:
            console.print(f"    [dim]…and {len(unresolved) - 10} more[/dim]")
    if malformed:
        lines_str = ", ".join(str(r.line) for r in malformed[:10])
        more = f" …(+{len(malformed) - 10})" if len(malformed) > 10 else ""
        console.print(
            f"  [yellow]Malformed {len(malformed)} row(s)[/yellow] "
            f"(missing Edition/Collector Number) — line(s) {lines_str}{more}"
        )

    if unresolved or malformed:
        raise typer.Exit(1)


@app.command()
def refresh(
    set_code: str = typer.Argument(..., help="Set code, e.g. NEO."),
    db_path: Path = typer.Option(db.DB_PATH, "--db-path", help="Database file location."),
) -> None:
    """Re-fetch an owned set from Scryfall and refresh its cached data.

    Updates the cached card data (prices, type lines, etc.) and regenerates the
    set's full-set entries against the current filter rules, so newly added or
    removed printings are reconciled. Only ``full_set`` entries for this set are
    touched — manual singles and overrides, and the original ownership date, are
    preserved.
    """
    if not Path(db_path).exists():
        console.print(
            "No collection database yet. Run [bold]mtgsets init[/bold] and "
            "[bold]mtgsets add <set>[/bold] first."
        )
        raise typer.Exit(1)

    code = set_code.strip().lower()
    display_code = code.upper()

    conn = db.get_connection(db_path)
    try:
        if not db.is_set_owned(conn, code):
            console.print(
                f"[yellow]{display_code} is not owned.[/yellow] Add it with "
                f"[bold]mtgsets add {display_code}[/bold]."
            )
            raise typer.Exit(1)
        before = db.count_full_set_entries(conn, code)
    finally:
        conn.close()

    # -- re-fetch (network boundary); _load_set exits with a message on failure ---
    set_obj, cards = _load_set(code)
    code = (set_obj.get("code") or code).lower()
    display_code = code.upper()
    set_name = set_obj.get("name", display_code)
    breakdown = collection.build_breakdown(cards)

    if not breakdown.included_count:
        console.print(
            f"[yellow]Not refreshed:[/yellow] no cards qualify for the full set of "
            f"[bold]{display_code}[/bold] (unreleased, or printings don't match the "
            "full-set rules?). The existing entries were left untouched."
        )
        raise typer.Exit(1)

    entries = collection.generate_full_set_entries(code, breakdown.included)
    conn = db.get_connection(db_path)
    try:
        try:
            cards_written = db.upsert_cards(conn, breakdown.included)
            db.delete_full_set_entries(conn, code)
            after = db.insert_collection_entries(conn, (e.as_row() for e in entries))
            db.update_owned_set_name(conn, code, set_name)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    finally:
        conn.close()

    delta = after - before
    if delta:
        sign = "+" if delta > 0 else ""
        change = f" [yellow]({sign}{delta} vs. before)[/yellow]"
    else:
        change = " [dim](unchanged)[/dim]"
    console.print(
        f"[green]Refreshed[/green] {set_name} ([cyan]{display_code}[/cyan]): "
        f"[green]{cards_written}[/green] cards cached, [green]{after}[/green] entries"
        f"{change} ([dim]{len(breakdown.main_cards)} main + {len(breakdown.basics)} basics[/dim])."
    )


@app.command(name="list")
def list_sets(
    db_path: Path = typer.Option(db.DB_PATH, "--db-path", help="Database file location."),
) -> None:
    """List owned sets and their generated entry counts."""
    if not Path(db_path).exists():
        console.print(
            "No collection database yet. Run [bold]mtgsets init[/bold] and "
            "[bold]mtgsets add <set>[/bold] first."
        )
        raise typer.Exit()

    conn = db.get_connection(db_path)
    try:
        rows = db.list_owned_sets(conn)
    finally:
        conn.close()

    if not rows:
        console.print("No owned sets yet. Add one with [bold]mtgsets add <set>[/bold].")
        raise typer.Exit()

    table = Table(title="Owned sets")
    table.add_column("Code", style="bold cyan")
    table.add_column("Name")
    table.add_column("Cards", justify="right")
    table.add_column("Foil")
    table.add_column("Condition", style="dim")
    table.add_column("Language", style="dim")
    table.add_column("Added", style="dim")
    total_entries = 0
    for r in rows:
        total_entries += r["entry_count"]
        table.add_row(
            r["set_code"].upper(),
            r["set_name"],
            str(r["entry_count"]),
            "yes" if r["foil"] else "no",
            r["condition"],
            r["language"],
            (r["added_at"] or "")[:10],
        )
    console.print(table)
    console.print(
        f"[green]{len(rows)}[/green] set(s), [green]{total_entries}[/green] generated card entries."
    )


def _collector_sort_key(card: dict) -> tuple[int, str]:
    """Sort key ordering printings by collector number numerically then as text.

    Mirrors the export ordering (db.get_export_entries): '2' before '10' before
    '100', with non-numeric numbers (e.g. '266a') falling back to text after the
    numeric ones.
    """
    cn = str(card.get("collector_number") or "")
    lead = "".join(c for c in cn if c.isdigit())
    return (int(lead) if lead else 1_000_000, cn)


@app.command()
def show(
    set_code: str = typer.Argument(..., help="Set code, e.g. NEO."),
    db_path: Path = typer.Option(db.DB_PATH, "--db-path", help="Database file location."),
    cards: bool = typer.Option(
        True, "--cards/--no-cards", help="List the individual cards (default), or just the summary."
    ),
) -> None:
    """Show the details and contents of an owned set.

    Reads only the locally cached data (no network), so it reflects the snapshot
    from when the set was added or last refreshed. Lists the generated full-set
    cards; manual singles and overrides are not part of a set's contents.
    """
    if not Path(db_path).exists():
        console.print(
            "No collection database yet. Run [bold]mtgsets init[/bold] and "
            "[bold]mtgsets add <set>[/bold] first."
        )
        raise typer.Exit(1)

    code = set_code.strip().lower()
    display_code = code.upper()
    conn = db.get_connection(db_path)
    try:
        owned = db.get_owned_set(conn, code)
        if owned is None:
            console.print(
                f"[yellow]{display_code} is not owned.[/yellow] Add it with "
                f"[bold]mtgsets add {display_code}[/bold], or see [bold]mtgsets list[/bold]."
            )
            raise typer.Exit(1)
        set_cards = db.get_owned_cards(conn, source_set_code=code)
    finally:
        conn.close()

    basics = [c for _, c in set_cards if "Basic Land" in (c.get("type_line") or "")]
    n_total = len(set_cards)
    n_basics = len(basics)
    n_main = n_total - n_basics

    console.print(f"\n[bold]Set:[/bold] {owned['set_name']} ([cyan]{display_code}[/cyan])")
    console.print(f"  Owned since   [dim]{(owned['added_at'] or '')[:10]}[/dim]")
    console.print(
        f"  Cards         [green]{n_total}[/green]  [dim]({n_main} main + {n_basics} basics)[/dim]"
    )

    if not set_cards:
        console.print(
            "\n[yellow]No cached cards for this set.[/yellow] Run "
            f"[bold]mtgsets refresh {display_code}[/bold] to fetch them."
        )
        return

    value = stats.build_value(set_cards)
    console.print(
        f"  Value         [green]${value.total_usd:,.2f}[/green]  "
        "[dim](cached USD prices; run [bold]mtgsets refresh[/bold] to update)[/dim]"
    )

    _print_breakdown("By rarity", stats.composition_by_rarity(set_cards))

    if not cards:
        return

    table = Table(title=f"\n{display_code} cards")
    table.add_column("#", justify="right", style="dim")
    table.add_column("Name")
    table.add_column("Rarity")
    table.add_column("USD", justify="right")
    for _, card in sorted(set_cards, key=lambda qc: _collector_sort_key(qc[1])):
        usd = stats.card_usd(card)
        table.add_row(
            str(card.get("collector_number") or ""),
            card.get("name") or "",
            (card.get("rarity") or "").capitalize(),
            f"${usd:,.2f}" if usd is not None else "[dim]—[/dim]",
        )
    console.print(table)


def _print_breakdown(title: str, pairs: list[tuple[str, int]]) -> None:
    """Render a ``[(label, count), ...]`` composition section."""
    console.print(f"\n[bold]{title}[/bold]")
    if not pairs:
        console.print("  [dim](none)[/dim]")
        return
    width = max(len(label) for label, _ in pairs)
    for label, count in pairs:
        console.print(f"  {label:<{width}}  [green]{count}[/green]")


def _print_progress(p: stats.ProgressStats) -> None:
    console.print("\n[bold]Progress[/bold]")
    if p.newest_owned and p.newest_owned.released_at:
        console.print(
            f"  Newest owned   [cyan]{p.newest_owned.code.upper()}[/cyan] "
            f"({p.newest_owned.released_at}) — {p.newest_owned.name}"
        )
    if p.oldest_owned and p.oldest_owned.released_at:
        console.print(
            f"  Oldest owned   [cyan]{p.oldest_owned.code.upper()}[/cyan] "
            f"({p.oldest_owned.released_at}) — {p.oldest_owned.name}"
        )
    if p.sets_behind == 0:
        console.print("  [green]Up to date[/green] with the latest core/expansion release.")
    elif p.sets_behind is not None:
        latest = f" (latest {p.latest_release.code.upper()})" if p.latest_release else ""
        console.print(
            f"  Sets behind    [yellow]{p.sets_behind}[/yellow] core/expansion release(s){latest}"
        )
    console.print(
        f"  Added         {p.added_last_30} in 30d · {p.added_last_90} in 90d · "
        f"{p.added_last_365} in 365d"
    )
    if p.coverage_by_year:
        years = "  ".join(f"{year}:{count}" for year, count in p.coverage_by_year)
        console.print(f"  By year       {years}")


def _print_year(y: stats.YearStats) -> None:
    summary = f"  [green]{len(y.owned)}[/green] / [bold]{y.total}[/bold] owned"
    if y.pct is not None:
        summary += f"  [dim]({y.pct:.1f}%)[/dim]"
    console.print(f"\n[bold]{y.year} sets[/bold]{summary}")

    if not y.total:
        console.print(f"  [dim]No core/expansion sets released in {y.year}.[/dim]")
    if y.owned:
        console.print("  Owned:")
        for r in y.owned:
            console.print(
                f"    [green]✓[/green] [cyan]{r.code.upper()}[/cyan] ({r.released_at}) — {r.name}"
            )
    if y.missing:
        console.print("  Missing:")
        for r in y.missing:
            console.print(
                f"    [red]✗[/red] [cyan]{r.code.upper()}[/cyan] ({r.released_at}) — {r.name}"
            )
    if y.upcoming:
        console.print("  Upcoming [dim](not released yet)[/dim]:")
        for r in y.upcoming:
            console.print(
                f"    [yellow]…[/yellow] [cyan]{r.code.upper()}[/cyan] ({r.released_at}) — {r.name}"
            )
    if y.owned_other:
        console.print("  Other owned [dim](Commander, Masters, etc.)[/dim]:")
        for r in y.owned_other:
            console.print(f"    [cyan]{r.code.upper()}[/cyan] ({r.released_at}) — {r.name}")


def _print_range(r: stats.RangeStats) -> None:
    for y in r.years:
        _print_year(y)
    pct = r.pct
    summary = f"  [green]{r.owned_total}[/green] / [bold]{r.release_total}[/bold] owned"
    if pct is not None:
        summary += f"  [dim]({pct:.1f}%)[/dim]"
    console.print(f"\n[bold]{r.from_year}–{r.to_year} total[/bold]{summary}")


def _print_value(v: stats.ValueStats) -> None:
    console.print(
        f"\n[bold]Estimated value[/bold]  [green]${v.total_usd:,.2f}[/green]  "
        "[dim](cached USD prices; run [bold]mtgsets refresh <set>[/bold] to update)[/dim]"
    )
    if v.unpriced_count:
        console.print(f"  [dim]{v.unpriced_count} card(s) had no USD price[/dim]")
    if v.top_cards:
        console.print("  Top cards:")
        for name, set_code, value in v.top_cards:
            console.print(f"    [green]${value:>8,.2f}[/green]  {name} ([cyan]{set_code}[/cyan])")
    if v.top_sets:
        console.print("  Top sets:")
        for set_code, value in v.top_sets:
            console.print(f"    [green]${value:>8,.2f}[/green]  [cyan]{set_code}[/cyan]")


@app.command(name="stats")
def stats_command(
    db_path: Path = typer.Option(db.DB_PATH, "--db-path", help="Database file location."),
    no_remote: bool = typer.Option(
        False, "--no-remote", help="Skip Scryfall; omit the total-sets/progress/year sections."
    ),
    rarity: bool = typer.Option(False, "--rarity", help="Break down owned cards by rarity."),
    colors: bool = typer.Option(False, "--colors", help="Break down owned cards by color."),
    types: bool = typer.Option(False, "--types", help="Break down owned cards by card type."),
    curve: bool = typer.Option(False, "--curve", help="Show the owned mana-value curve."),
    progress: bool = typer.Option(
        False, "--progress", help="Show release-timeline progress (needs Scryfall)."
    ),
    refresh_sets: bool = typer.Option(
        False,
        "--refresh-sets",
        help="Force a re-fetch of the Scryfall set list, ignoring the cache.",
    ),
    value: bool = typer.Option(
        False, "--value", help="Estimate collection value from cached Scryfall prices."
    ),
    year: int | None = typer.Option(
        None, "--year", help="List owned vs. missing core/expansion sets from a release year."
    ),
    from_year: int | None = typer.Option(
        None, "--from-year", help="Start of a release-year range (use with --to-year)."
    ),
    to_year: int | None = typer.Option(
        None, "--to-year", help="End of a release-year range (use with --from-year)."
    ),
    show_all: bool = typer.Option(False, "--all", help="Show every breakdown section."),
) -> None:
    """Show collection statistics — sets owned vs. total, with optional breakdowns.

    The default output is compact; add flags (or ``--all``) for composition, progress,
    and value breakdowns.
    """
    if show_all:
        rarity = colors = types = curve = progress = value = True

    # Validate the year range up front so bad input fails before any Scryfall fetch.
    if (from_year is None) != (to_year is None):
        console.print(
            "[red]--from-year and --to-year must be used together.[/red] "
            "Use [bold]--year[/bold] for a single year."
        )
        raise typer.Exit(1)
    if from_year is not None and to_year is not None and from_year > to_year:
        console.print(
            f"[red]--from-year ({from_year}) must not be after --to-year ({to_year}).[/red]"
        )
        raise typer.Exit(1)

    if not Path(db_path).exists():
        console.print(
            "No collection database yet. Run [bold]mtgsets init[/bold] and "
            "[bold]mtgsets add <set>[/bold] first."
        )
        raise typer.Exit(1)

    need_cards = rarity or colors or types or curve or value
    conn = db.get_connection(db_path)
    try:
        rows = db.list_owned_sets(conn)
        owned_cards = db.get_owned_cards(conn) if need_cards else []
    finally:
        conn.close()

    owned_codes = [r["set_code"] for r in rows]
    owned_added = [(r["set_code"], r["added_at"]) for r in rows]
    card_entries = sum(r["entry_count"] for r in rows)

    # The only networked section left is the Scryfall set list (sets-owned total,
    # --progress, --year), and it's served from the local cache when fresh (#68).
    # Value reads cached prices from the db, so no fetch here. --no-remote is
    # authoritative: it suppresses every networked section, cache included.
    all_sets: list[dict] | None = None
    if not no_remote:
        conn = db.get_connection(db_path)
        try:
            all_sets = _load_all_sets(conn, force_refresh=refresh_sets)
        except scryfall.ScryfallError as exc:
            console.print(
                f"[yellow]Note:[/yellow] could not reach Scryfall ({exc}); "
                "showing local stats only."
            )
        finally:
            conn.close()

    # Release denominator only when not explicitly skipped (and the fetch succeeded).
    release_codes = (
        [s["code"] for s in scryfall.release_sets(all_sets)]
        if all_sets is not None and not no_remote
        else None
    )
    s = stats.build_stats(owned_codes, card_entries, release_codes)

    console.print("\n[bold]Collection stats[/bold]\n")
    if s.release_total is not None:
        pct = s.release_pct
        console.print(
            f"  Sets owned      [green]{s.owned_release}[/green] / "
            f"[bold]{s.release_total}[/bold] core+expansion"
            + (f"  [dim]({pct:.1f}%)[/dim]" if pct is not None else "")
        )
        if s.owned_other:
            console.print(
                f"  Other sets owned [green]{s.owned_other}[/green]  "
                "[dim](Commander, Masters, etc. — outside core/expansion)[/dim]"
            )
    else:
        console.print(f"  Sets owned      [green]{s.owned_total}[/green]")

    console.print(f"  Card entries    [green]{card_entries}[/green]")

    # -- per-year breakdown (needs the Scryfall set list) -------------------
    if year is not None:
        if all_sets is None:
            console.print(
                f"\n[yellow]{year} sets[/yellow] needs Scryfall; skipped "
                "(unreachable or [bold]--no-remote[/bold])."
            )
        else:
            today = datetime.now(timezone.utc).date()
            _print_year(
                stats.build_year_stats(
                    owned_codes, all_sets, scryfall.release_sets(all_sets), str(year), today
                )
            )

    # -- year-range breakdown (needs the Scryfall set list) -----------------
    if from_year is not None and to_year is not None:
        if all_sets is None:
            console.print(
                f"\n[yellow]{from_year}–{to_year} sets[/yellow] needs Scryfall; skipped "
                "(unreachable or [bold]--no-remote[/bold])."
            )
        else:
            today = datetime.now(timezone.utc).date()
            _print_range(
                stats.build_range_stats(
                    owned_codes,
                    all_sets,
                    scryfall.release_sets(all_sets),
                    from_year,
                    to_year,
                    today,
                )
            )

    if not s.owned_total:
        console.print(
            "\n[dim]No sets owned yet. Add one with [bold]mtgsets add <set>[/bold].[/dim]"
        )
        return

    # -- composition sections (local, no network) --------------------------
    if rarity:
        _print_breakdown("By rarity", stats.composition_by_rarity(owned_cards))
    if colors:
        _print_breakdown("By color", stats.composition_by_color(owned_cards))
    if types:
        _print_breakdown("By type", stats.composition_by_type(owned_cards))
    if curve:
        _print_breakdown("Mana curve (nonland)", stats.mana_curve(owned_cards))

    # -- progress section --------------------------------------------------
    if progress:
        if all_sets is None:
            console.print(
                "\n[yellow]Progress[/yellow] needs Scryfall; skipped "
                "(unreachable or [bold]--no-remote[/bold])."
            )
        else:
            today = datetime.now(timezone.utc).date()
            _print_progress(
                stats.build_progress(owned_added, all_sets, scryfall.release_sets(all_sets), today)
            )

    # -- value section -----------------------------------------------------
    # Uses the prices cached in the db (no network), so it works offline too.
    if value:
        _print_value(stats.build_value(owned_cards))


@app.command()
def remove(
    set_code: str = typer.Argument(..., help="Set code, e.g. NEO."),
    db_path: Path = typer.Option(db.DB_PATH, "--db-path", help="Database file location."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
) -> None:
    """Remove an owned set and only its generated full-set entries.

    Manual singles and overrides are never touched.
    """
    if not Path(db_path).exists():
        console.print("No collection database yet — nothing to remove.")
        raise typer.Exit(1)

    code = set_code.strip().lower()
    display_code = code.upper()
    conn = db.get_connection(db_path)
    try:
        if not db.is_set_owned(conn, code):
            console.print(
                f"[yellow]{display_code} is not owned.[/yellow] See [bold]mtgsets list[/bold]."
            )
            raise typer.Exit(1)

        count = db.count_full_set_entries(conn, code)
        if not yes:
            confirmed = typer.confirm(
                f"Remove {display_code} and its {count} generated entries? "
                "(manual singles and overrides are kept)"
            )
            if not confirmed:
                console.print("Aborted.")
                raise typer.Exit()

        entries_deleted, _ = db.remove_owned_set(conn, code)
    finally:
        conn.close()

    console.print(
        f"[green]Removed[/green] [cyan]{display_code}[/cyan] and "
        f"[green]{entries_deleted}[/green] generated entries."
    )


export_app = typer.Typer(help="Export the collection.", no_args_is_help=True)
app.add_typer(export_app, name="export")


@export_app.command("moxfield")
def export_moxfield(
    db_path: Path = typer.Option(db.DB_PATH, "--db-path", help="Database file location."),
    output: Path = typer.Option(
        Path("exports") / "moxfield.csv", "--output", "-o", help="CSV output path."
    ),
) -> None:
    """Export the collection as a Moxfield-importable CSV."""
    if not Path(db_path).exists():
        console.print("No collection database yet. Run [bold]mtgsets add <set>[/bold] first.")
        raise typer.Exit(1)

    conn = db.get_connection(db_path)
    try:
        entries = db.get_export_entries(conn)
    finally:
        conn.close()

    if not entries:
        console.print("Nothing to export — the collection is empty.")
        raise typer.Exit(1)

    written = export.write_moxfield_csv(entries, output)
    console.print(
        f"[green]Exported[/green] [green]{written}[/green] cards to [bold]{output}[/bold]."
    )


def main() -> None:
    """Console-script entry point (see pyproject.toml [project.scripts])."""
    app()


if __name__ == "__main__":
    main()
