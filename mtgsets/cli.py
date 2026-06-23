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

from . import db, scryfall

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


@app.command()
def preview(set_code: str = typer.Argument(..., help="Set code, e.g. NEO.")) -> None:
    """Show included/excluded cards before adding a set (issue #6)."""
    console.print(f"preview {set_code.upper()}: {_TODO}")


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
