"""`hearth db ...` subcommand group."""

from __future__ import annotations

import asyncio
import os

import typer
from sqlalchemy import inspect

from hearth.cli import _wrap_command  # pyright: ignore[reportPrivateUsage]
from hearth.cli._plugins import _print_registry_build_error  # pyright: ignore[reportPrivateUsage]
from hearth.kernel._engine import make_async_engine  # pyright: ignore[reportPrivateUsage]
from hearth.kernel.persistence import METADATA
from hearth.kernel.registry import Registry, RegistryBuildError

db_app = typer.Typer(help="Database operations.", no_args_is_help=True)


def _require_database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        typer.echo(
            "ERROR: DATABASE_URL environment variable is required for `db` commands.",
            err=True,
        )
        raise typer.Exit(1)
    if url.endswith(":memory:") or url.endswith(":memory:?cache=shared"):
        typer.echo(
            "ERROR: in-memory SQLite (:memory:) is not supported for `db` commands.\n"
            "Use a file URL like `sqlite:///hearth.db` instead.",
            err=True,
        )
        raise typer.Exit(1)
    return url


@db_app.command("init")
@_wrap_command
def init_cmd() -> None:
    """Create the kernel's internal tables (`_hearth_outbox`)."""
    url = _require_database_url()
    asyncio.run(_init_impl(url))


async def _init_impl(url: str) -> None:
    typer.echo(f"Connecting to: {url}")
    # Build the registry first so plugin entity modules import and their
    # tables register with METADATA before create_all runs.
    try:
        registry = Registry.build()
    except RegistryBuildError as err:
        _print_registry_build_error(err)
        raise typer.Exit(1) from err
    engine = make_async_engine(url)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(METADATA.create_all)
        typer.echo("Created tables:")
        typer.echo("  [OK] _hearth_outbox")
        for alias in sorted(registry.plugins):
            info = registry.plugins[alias]
            for ent in info.entities:
                tablename = getattr(ent, "__tablename__", None)
                if tablename:
                    typer.echo(f"  [OK] {tablename}")
        typer.echo("Done.")
    finally:
        await engine.dispose()


@db_app.command("status")
@_wrap_command
def status_cmd() -> None:
    """Show database connection status and kernel-table presence."""
    url = _require_database_url()
    asyncio.run(_status_impl(url))


async def _status_impl(url: str) -> None:
    typer.echo(f"Database: {url}")
    engine = make_async_engine(url)
    try:
        async with engine.connect() as conn:
            existing = await conn.run_sync(
                lambda sync_conn: set(inspect(sync_conn).get_table_names()),
            )
        typer.echo("  Connection:     OK")
        outbox_status = (
            "[present]" if "_hearth_outbox" in existing else "[missing - run `hearth db init`]"
        )
        typer.echo(f"  Kernel tables:  _hearth_outbox {outbox_status}")
        typer.echo("  Plugin tables:  0 tracked  (schema log not yet initialized)")
        typer.echo("  Orphan tables:  (none detected)")
    finally:
        await engine.dispose()


@db_app.command("graph")
@_wrap_command
def graph_cmd() -> None:
    """Print a text-based schema graph (tables and FKs) from declared entities."""
    try:
        registry = Registry.build()
    except RegistryBuildError as err:
        typer.echo("ERROR: cannot build plugin registry.", err=True)
        for issue in err.issues:
            typer.echo(f"  - {issue}", err=True)
        raise typer.Exit(1) from err

    all_tables: set[str] = set()
    children_of: dict[str, list[tuple[str, str, str, str]]] = {}
    for info in registry.plugins.values():
        for ent in info.entities:
            table = getattr(ent, "__table__", None)
            if table is None:
                continue
            all_tables.add(table.name)
            for col in table.columns:
                for fk in col.foreign_keys:
                    parent_table = fk.column.table.name
                    all_tables.add(parent_table)
                    children_of.setdefault(parent_table, []).append(
                        (col.name, table.name, fk.column.name, fk.ondelete or "NO ACTION"),
                    )

    typer.echo(f"Schema graph ({len(registry.plugins)} plugins, {len(all_tables)} tables):")
    typer.echo("")
    for table in sorted(all_tables):
        typer.echo(f"  {table}")
        for child_col, child_table, parent_col, ondelete in children_of.get(table, []):
            typer.echo(
                f"  └── {child_table}    ({child_col} → {table}.{parent_col}, {ondelete})",
            )
        typer.echo("")
