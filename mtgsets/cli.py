"""mtgsets command-line interface.

A thin Typer layer. Command implementations live in the sibling modules
(db, scryfall, filters, collection, export). See docs/DESIGN.md for the spec.

This module is the scaffold (issue #1): every command is registered so the CLI
runs end to end, but the bodies are stubs filled in by issues #2-#10.
"""

from __future__ import annotations

import typer
from rich.console import Console

app = typer.Typer(
    name="mtgsets",
    help="Manage a Magic: The Gathering collection by full set; export to Moxfield.",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()

_TODO = "[yellow]not yet implemented[/yellow]"


@app.command()
def init() -> None:
    """Create the local SQLite database (issue #2)."""
    console.print(f"init: {_TODO}")


@app.command()
def search(query: str = typer.Argument(..., help="Set search query.")) -> None:
    """Search Scryfall for sets (issue #5)."""
    console.print(f"search {query!r}: {_TODO}")


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
