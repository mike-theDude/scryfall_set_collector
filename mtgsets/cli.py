"""mtgsets command-line interface.

A thin Typer layer. Command implementations live in the sibling modules
(db, scryfall, filters, collection, export). See docs/DESIGN.md for the spec.

This module is the scaffold (issue #1): every command is registered so the CLI
runs end to end, but the bodies are stubs filled in by issues #2-#10.
"""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from . import collection, db, scryfall

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
    db_path: Path = typer.Option(
        db.DB_PATH, "--db-path", help="Database file location."
    ),
) -> None:
    """Create the local SQLite database and schema."""
    created = db.init_db(db_path)
    if created:
        console.print(f"[green]Initialized[/green] database at [bold]{db_path}[/bold]")
    else:
        console.print(
            f"[yellow]Database already exists[/yellow] at [bold]{db_path}[/bold] "
            "(schema ensured)"
        )


@app.command()
def search(query: str = typer.Argument(..., help="Set name or code substring.")) -> None:
    """Search Scryfall for sets by name or code."""
    try:
        with scryfall.ScryfallClient() as client:
            all_sets = client.get_sets()
    except scryfall.ScryfallError as exc:
        console.print(f"[red]Scryfall request failed:[/red] {exc}")
        raise typer.Exit(1)

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
        raise typer.Exit(1)
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
            "may be unreleased or boosterless."
        )


@app.command()
def add(set_code: str = typer.Argument(..., help="Set code, e.g. NEO.")) -> None:
    """Mark a set as fully owned and generate card entries (issue #7)."""
    console.print(f"add {set_code.upper()}: {_TODO}")


@app.command(name="list")
def list_sets() -> None:
    """List owned sets (issue #8)."""
    console.print(f"list: {_TODO}")


@app.command()
def remove(set_code: str = typer.Argument(..., help="Set code, e.g. NEO.")) -> None:
    """Remove an owned set and only its generated entries (issue #9)."""
    console.print(f"remove {set_code.upper()}: {_TODO}")


export_app = typer.Typer(help="Export the collection.", no_args_is_help=True)
app.add_typer(export_app, name="export")


@export_app.command("moxfield")
def export_moxfield() -> None:
    """Export a Moxfield-compatible CSV (issue #10)."""
    console.print(f"export moxfield: {_TODO}")


def main() -> None:
    """Console-script entry point (see pyproject.toml [project.scripts])."""
    app()


if __name__ == "__main__":
    main()
