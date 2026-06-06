"""`hearth db ...` subcommand group."""

from __future__ import annotations

import asyncio
import os

import typer

from hearth.cli import _wrap_command  # pyright: ignore[reportPrivateUsage]
from hearth.cli._plugins import _print_registry_build_error  # pyright: ignore[reportPrivateUsage]
from hearth.kernel._engine import make_async_engine  # pyright: ignore[reportPrivateUsage]
from hearth.kernel.migrations.alembic_config import build_config
from hearth.kernel.migrations.applier import AppliedReport, apply
from hearth.kernel.migrations.audit_log import SCHEMA_LOG_METADATA, read_applied_revisions
from hearth.kernel.migrations.planner import PlannedRevision, compute_plan
from hearth.kernel.persistence import METADATA
from hearth.kernel.registry import Registry, RegistryBuildError
from hearth.migrations import OpSummary
from hearth.primitives.actor import System

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
        async with engine.begin() as conn:
            await conn.run_sync(SCHEMA_LOG_METADATA.create_all)
        typer.echo("  [OK] _hearth_schema_log")
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
    try:
        registry = Registry.build()
    except RegistryBuildError as err:
        _print_registry_build_error(err)
        raise typer.Exit(1) from err
    engine = make_async_engine(url)
    try:
        cfg = build_config(registry, url)
        plan = await compute_plan(engine, cfg, registry)

        typer.echo(f"Database: {url}")
        typer.echo("")
        typer.echo("Plugins:")
        for alias in sorted(registry.plugins):
            applied_count = len(await read_applied_revisions(engine, plugin=alias))
            pending = [r for r in plan.revisions if r.plugin == alias]
            typer.echo(f"  {alias}: {applied_count} applied, {len(pending)} pending")

        if plan.has_destructive:
            typer.echo("")
            typer.echo("WARNING: pending plan contains destructive operations.")
    finally:
        await engine.dispose()


@db_app.command("plan")
@_wrap_command
def plan_cmd() -> None:
    """Show pending migrations without applying."""
    url = _require_database_url()
    asyncio.run(_plan_impl(url))


async def _plan_impl(url: str) -> None:
    try:
        registry = Registry.build()
    except RegistryBuildError as err:
        _print_registry_build_error(err)
        raise typer.Exit(1) from err
    engine = make_async_engine(url)
    try:
        cfg = build_config(registry, url)
        plan = await compute_plan(engine, cfg, registry)
        typer.echo(plan.format())
        if plan.has_destructive:
            typer.echo("")
            typer.echo(
                "Plan contains destructive operations — "
                "will require interactive confirmation."
            )
    finally:
        await engine.dispose()


@db_app.command("migrate")
@_wrap_command
def migrate_cmd(
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Same as `plan`; show without applying."
    ),
) -> None:
    """Apply pending migrations. Destructive ops prompt interactively."""
    url = _require_database_url()
    if dry_run:
        asyncio.run(_plan_impl(url))
        return
    asyncio.run(_migrate_impl(url))


async def _migrate_impl(url: str) -> None:
    try:
        registry = Registry.build()
    except RegistryBuildError as err:
        _print_registry_build_error(err)
        raise typer.Exit(1) from err
    engine = make_async_engine(url)
    try:
        cfg = build_config(registry, url)
        plan = await compute_plan(engine, cfg, registry)
        if not plan.revisions:
            typer.echo("No pending migrations.")
            return
        typer.echo(plan.format())
        typer.echo("")
        report = await apply(
            plan,
            config=cfg,
            engine=engine,
            actor=System(),
            registry=registry,
            confirm=_tty_confirm,
        )
        _print_report(report)
    finally:
        await engine.dispose()


async def _tty_confirm(revision: PlannedRevision, op: OpSummary) -> bool:
    typer.echo("")
    typer.echo(f"DESTRUCTIVE: plugin={revision.plugin} revision={revision.revision_id}")
    typer.echo(f"  description: {revision.description}")
    typer.echo(f"  op: {op.desc}")
    answer = typer.prompt("Apply this op? [y/N]", default="N", show_default=False)
    return answer.strip().lower() in {"y", "yes"}


def _print_report(report: AppliedReport) -> None:
    typer.echo("")
    typer.echo(f"Applied {len(report.applied_revisions)} revision(s):")
    for plugin, rev in report.applied_revisions:
        typer.echo(f"  [OK] {plugin}:{rev}")
    if report.aborted_at is not None:
        plugin, rev = report.aborted_at
        typer.echo(f"Aborted at {plugin}:{rev}.")


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
