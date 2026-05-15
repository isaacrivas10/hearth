"""The `hearth` command-line interface."""

from __future__ import annotations

import functools
import os
import sys
from collections.abc import Callable
from importlib.metadata import version as _pkg_version
from typing import Annotated, Any

import typer

from hearth.kernel.registry import RegistryBuildError

app = typer.Typer(
    no_args_is_help=True,
    help="Hearth kernel command-line interface.",
    add_completion=False,
)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"hearth {_pkg_version('hearth')}")
        raise typer.Exit()


@app.callback()
def main(
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            callback=_version_callback,
            is_eager=True,
            help="Print the kernel version and exit.",
        ),
    ] = False,
) -> None:
    """Hearth kernel CLI."""


def _wrap_command(fn: Callable[..., Any]) -> Callable[..., Any]:  # pyright: ignore[reportUnusedFunction]
    """Wrap a Typer command body so unhandled exceptions render cleanly."""

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return fn(*args, **kwargs)
        except (RegistryBuildError, typer.BadParameter, typer.Exit):
            raise
        except Exception as exc:
            if os.environ.get("HEARTH_DEBUG"):
                raise
            typer.echo(
                f"ERROR: unexpected {type(exc).__name__}: {exc}\n"
                f"Re-run with HEARTH_DEBUG=1 for a full traceback.",
                err=True,
            )
            sys.exit(1)

    return wrapper


from hearth.cli._plugins import plugins_app  # noqa: E402  pyright: ignore[reportPrivateUsage]

app.add_typer(plugins_app, name="plugins")

from hearth.cli._db import db_app  # noqa: E402  pyright: ignore[reportPrivateUsage]

app.add_typer(db_app, name="db")
