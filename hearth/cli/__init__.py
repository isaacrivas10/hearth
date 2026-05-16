"""The `hearth` command-line interface."""

from __future__ import annotations

import functools
import importlib.metadata
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


_RESERVED_CLI_NAMES = frozenset({"plugins", "db"})


def _register_plugin_cli_groups() -> None:
    """Discover Typer subcommand groups contributed by plugins via the
    `[project.entry-points."hearth.cli"]` group.

    Independent of `Registry.build()`: plugin CLI loads via its own entry-point
    group so CLI diagnostics remain available even when the registry has
    problems. Failures are logged as warnings, not raised."""
    # Track names already registered (kernel-reserved plus prior plugins) so
    # two plugins declaring the same CLI name produce a warning instead of a
    # silent overwrite via the second add_typer call.
    registered: set[str] = set(_RESERVED_CLI_NAMES)
    for ep in importlib.metadata.entry_points(group="hearth.cli"):
        if ep.name in _RESERVED_CLI_NAMES:
            typer.echo(
                f"WARNING: plugin CLI name {ep.name!r} is reserved; skipping",
                err=True,
            )
            continue
        if ep.name in registered:
            typer.echo(
                f"WARNING: plugin CLI name {ep.name!r} already registered; skipping",
                err=True,
            )
            continue
        try:
            plugin_app = ep.load()
        except Exception as exc:
            typer.echo(
                f"WARNING: failed to load CLI from plugin {ep.name!r}: {type(exc).__name__}: {exc}",
                err=True,
            )
            continue
        app.add_typer(plugin_app, name=ep.name)
        registered.add(ep.name)


_register_plugin_cli_groups()
