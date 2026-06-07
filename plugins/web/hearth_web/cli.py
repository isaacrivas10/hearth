"""hearth web ... CLI subcommands. Registered via the `hearth.cli` entry point."""

from __future__ import annotations

import os
from typing import Annotated

import typer

web_app = typer.Typer(help="Serve the Hearth web console.", no_args_is_help=True)


@web_app.callback()
def _root() -> None:  # pyright: ignore[reportUnusedFunction]
    """hearth-web: browser console and plugin web framework."""


@web_app.command("serve")
def serve(
    host: Annotated[str, typer.Option(help="Bind host.")] = "127.0.0.1",
    port: Annotated[int, typer.Option(help="Bind port.")] = 8000,
    reload: Annotated[bool, typer.Option(help="Auto-reload (dev only).")] = False,
) -> None:
    """Run the web console with uvicorn."""
    if not os.environ.get("HEARTH_WEB_SECRET_KEY"):
        typer.echo("ERROR: HEARTH_WEB_SECRET_KEY environment variable is required.", err=True)
        raise typer.Exit(1)
    if not os.environ.get("DATABASE_URL"):
        typer.echo("ERROR: DATABASE_URL environment variable is required.", err=True)
        raise typer.Exit(1)
    import uvicorn

    uvicorn.run("hearth_web.app:create_app", factory=True, host=host, port=port, reload=reload)
