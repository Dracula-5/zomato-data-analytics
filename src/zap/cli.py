"""`zap` command-line interface. The Makefile is a thin wrapper around these commands."""

from pathlib import Path
from typing import Annotated

import typer

from zap.config import get_settings
from zap.fixtures import FixtureSpec, generate_fixture

app = typer.Typer(help="Zomato AI Data Platform CLI.", no_args_is_help=True)


@app.command()
def info() -> None:
    """Show the resolved settings (secrets masked)."""
    for name, value in get_settings().redacted().items():
        typer.echo(f"{name:<16} {value}")


@app.command()
def fixtures(
    out: Annotated[Path, typer.Option(help="Folder to write the CSVs to.")] = Path(
        ".cache/fixture"
    ),
    orders: Annotated[int, typer.Option(min=100, help="Number of orders.")] = 5_000,
    seed: Annotated[int, typer.Option(help="RNG seed; same seed gives identical files.")] = 42,
    defects: Annotated[bool, typer.Option(help="Inject the known data defects.")] = True,
) -> None:
    """Generate the SYNTHETIC fixture dataset used by tests, CI and `make demo`."""
    result = generate_fixture(out, FixtureSpec(n_orders=orders, seed=seed, defects=defects))
    typer.echo(f"Synthetic fixture written to {out}")
    for name, n in result.rows.items():
        typer.echo(f"  {name:<12} {n:>8,} rows")
    injected = {k: v for k, v in result.defects.as_dict().items() if v}
    typer.echo(f"  injected defects: {injected or 'none'}")


if __name__ == "__main__":  # pragma: no cover
    app()
