"""mtgsets command-line interface.

A thin Typer layer. Command implementations live in the sibling modules
(db, scryfall, filters, collection, export). See docs/DESIGN.md for the spec.

This module is the scaffold (issue #1): every command is registered so the CLI
runs end to end, but the bodies are stubs filled in by issues #2-#10.
"""

from __future__ import annotations

from datetime import datetime, timezone
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
def search(query: str = typer.Argument(..., help="Set name or code substring.")) -> None:
    """Search Scryfall for sets by name or code."""
    try:
        with scryfall.ScryfallClient() as client:
            all_sets = client.get_sets()
    except scryfall.ScryfallError as exc:
        console.print(f"[red]Scryfall request failed:[/red] {exc}")
        raise typer.Exit(1) from exc

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


@app.command(name="stats")
def stats_command(
    db_path: Path = typer.Option(db.DB_PATH, "--db-path", help="Database file location."),
    no_remote: bool = typer.Option(
        False, "--no-remote", help="Skip Scryfall; omit the total-sets comparison."
    ),
) -> None:
    """Show collection statistics — sets owned vs. the total number of sets."""
    if not Path(db_path).exists():
        console.print(
            "No collection database yet. Run [bold]mtgsets init[/bold] and "
            "[bold]mtgsets add <set>[/bold] first."
        )
        raise typer.Exit(1)

    conn = db.get_connection(db_path)
    try:
        rows = db.list_owned_sets(conn)
    finally:
        conn.close()

    owned_codes = [r["set_code"] for r in rows]
    card_entries = sum(r["entry_count"] for r in rows)

    # The total-sets denominator needs the live Scryfall set list; degrade gracefully
    # (still report owned totals) when it's unavailable or explicitly skipped.
    release_codes: list[str] | None = None
    if not no_remote:
        try:
            with scryfall.ScryfallClient() as client:
                release_codes = [s["code"] for s in scryfall.release_sets(client.get_sets())]
        except scryfall.ScryfallError as exc:
            console.print(
                f"[yellow]Note:[/yellow] could not reach Scryfall for the total set "
                f"count ({exc}); showing owned totals only."
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

    if not s.owned_total:
        console.print(
            "\n[dim]No sets owned yet. Add one with [bold]mtgsets add <set>[/bold].[/dim]"
        )


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
