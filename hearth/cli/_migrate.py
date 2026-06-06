"""`hearth migrate ...` subcommand group — plugin-developer verbs."""

from __future__ import annotations

import asyncio
import importlib.util
import os
from pathlib import Path
from types import ModuleType

import typer
from alembic.operations.ops import MigrationScript

from hearth.cli import _wrap_command  # pyright: ignore[reportPrivateUsage]
from hearth.cli._plugins import _print_registry_build_error  # pyright: ignore[reportPrivateUsage]
from hearth.kernel._engine import make_async_engine  # pyright: ignore[reportPrivateUsage]
from hearth.kernel.migrations._codegen import (
    post_process_generated_file,  # pyright: ignore[reportPrivateUsage]
)
from hearth.kernel.migrations.alembic_config import build_config
from hearth.kernel.registry import PluginInfo, Registry, RegistryBuildError

migrate_app = typer.Typer(
    help="Plugin-developer migration commands.",
    no_args_is_help=True,
)


@migrate_app.command("generate")
@_wrap_command
def generate_cmd(
    plugin: str = typer.Argument(..., help="Plugin alias (e.g. 'auth')."),
    refresh: bool = typer.Option(
        False,
        "--refresh",
        help=(
            "Re-emit HEARTH_UPGRADE_OPS for the latest revision instead of "
            "creating a new one. (Post-processing implemented in F2.)"
        ),
    ),
) -> None:
    """Autogenerate the next revision file for <plugin>."""
    asyncio.run(_generate_impl(plugin, refresh))


async def _generate_impl(plugin: str, refresh: bool) -> None:
    try:
        registry = Registry.build()
    except RegistryBuildError as err:
        _print_registry_build_error(err)
        raise typer.Exit(1) from err

    if plugin not in registry.plugins:
        installed = ", ".join(sorted(registry.plugins)) or "(none)"
        typer.echo(
            f"ERROR: plugin {plugin!r} is not installed. Installed: {installed}",
            err=True,
        )
        raise typer.Exit(1)

    plugin_info = registry.plugins[plugin]
    _require_editable_install(plugin_info)

    # F1 acknowledges --refresh but defers post-processing to F2.
    _ = refresh

    url = os.environ.get("DATABASE_URL", "sqlite:///:memory:")
    engine = make_async_engine(url)
    # `process_revision_directives` is Alembic's documented hook for capturing
    # the in-memory `MigrationScript` (which carries `upgrade_ops` /
    # `downgrade_ops`) before it is rendered to disk. The function returned by
    # `command.revision(...)` is only an `Optional[Script]` exposing `.path`,
    # so we collect the richer `MigrationScript` here for post-processing.
    captured: list[MigrationScript] = []

    def _capture(
        _context: object, _revision: object, directives: list[MigrationScript]
    ) -> None:
        captured.extend(directives)

    try:
        cfg = build_config(registry, url)
        version_path = os.path.join(
            os.path.dirname(plugin_info.install_path),
            "migrations",
        )
        # Autogenerate is sync — offload to a thread.
        from alembic import command as alembic_command

        scripts = await asyncio.to_thread(
            alembic_command.revision,
            cfg,
            autogenerate=True,
            branch_label=plugin,
            version_path=version_path,
            head=f"{plugin}@head",
            process_revision_directives=_capture,
        )
    finally:
        await engine.dispose()

    script = scripts[0] if isinstance(scripts, list) else scripts
    if script is None or not captured:
        typer.echo(f"No schema changes detected for plugin {plugin!r}.")
        return
    ms = captured[0]
    post_process_generated_file(
        file_path=Path(script.path),
        upgrade_ops=ms.upgrade_ops.ops if ms.upgrade_ops else [],
        downgrade_ops=ms.downgrade_ops.ops if ms.downgrade_ops else [],
    )
    typer.echo(f"Generated: {script.path}")


def _require_editable_install(plugin_info: PluginInfo) -> None:
    """Detect non-editable install (PEP 660). Abort with actionable error."""
    install_path = plugin_info.install_path
    if "/site-packages/" in install_path:
        typer.echo(
            f"ERROR: plugin {plugin_info.alias!r} is not an editable install.\n"
            f"  Revision generation must run from the plugin source tree.\n"
            f"  Try: pip install -e {plugin_info.install_path}",
            err=True,
        )
        raise typer.Exit(1)


@migrate_app.command("check")
@_wrap_command
def check_cmd(plugin: str = typer.Argument(..., help="Plugin alias")) -> None:
    """CI gate: report drift between entity declarations and revision files."""
    asyncio.run(_check_impl(plugin))


async def _check_impl(plugin: str) -> None:
    try:
        registry = Registry.build()
    except RegistryBuildError as err:
        _print_registry_build_error(err)
        raise typer.Exit(1) from err
    if plugin not in registry.plugins:
        typer.echo(f"ERROR: plugin '{plugin}' is not installed.", err=True)
        raise typer.Exit(1)

    # 1. Drift detection: replay revisions in a temp sqlite DB (no production
    #    connection needed) and compare against declared target_metadata. Per
    #    the spec, `check` is a pure code-vs-revisions operation — it does not
    #    connect to the operator's real DB.
    diff = await asyncio.to_thread(_compute_drift, registry, plugin)
    if diff:
        typer.echo(f"Drift detected in plugin '{plugin}':", err=True)
        for change in diff:
            typer.echo(f"  - {change}", err=True)
        typer.echo("Run `hearth migrate generate` to refresh.", err=True)
        raise typer.Exit(1)

    # 2. OpSummary correspondence
    issues = _check_op_summary_correspondence(registry.plugins[plugin])
    if issues:
        for issue in issues:
            typer.echo(f"  - {issue}", err=True)
        raise typer.Exit(1)

    typer.echo(f"plugin '{plugin}': no drift; revisions are current.")


def _compute_drift(registry: Registry, plugin: str) -> list[str]:
    """Replay the plugin's revisions against a scratch in-memory sqlite DB,
    then compare the resulting schema against the plugin's declared
    `target_metadata`. Non-empty diff = drift.

    Pure code-vs-revisions; does not touch the operator's real DB.
    """
    import tempfile

    import sqlalchemy as sa
    from alembic import command as alembic_command
    from alembic.autogenerate.api import compare_metadata
    from alembic.runtime.migration import MigrationContext

    # Scratch DB. File-backed so Alembic's stamping works; tmp tears down.
    with tempfile.TemporaryDirectory() as tmp:
        scratch_url = f"sqlite:///{tmp}/check.db"
        cfg = build_config(registry, scratch_url)
        # Replay every revision so the scratch DB has the schema the
        # revision chain produces.
        try:
            alembic_command.upgrade(cfg, f"{plugin}@head")
        except Exception as err:
            return [f"failed to replay revisions: {err}"]

        # Build the *declared* metadata for this plugin only.
        target = sa.MetaData()
        for ent in registry.plugins[plugin].entities:
            table = getattr(ent, "__table__", None)
            if isinstance(table, sa.Table):
                table.to_metadata(target)

        engine = sa.create_engine(scratch_url)
        try:
            with engine.connect() as conn:
                ctx = MigrationContext.configure(connection=conn)
                changes = compare_metadata(ctx, target)
        finally:
            engine.dispose()

    return [repr(c) for c in changes]


def _check_op_summary_correspondence(plugin_info: PluginInfo) -> list[str]:
    """Walk every revision file and check data hook ↔ OpSummary correspondence.

    Enforces: presence of an `async def data_upgrade` ⟺ exactly one
    `OpSummary(kind="DataMigration")` in `HEARTH_UPGRADE_OPS`. Same for the
    downgrade pair. Files missing both are fine (pure-schema revisions).
    """
    issues: list[str] = []
    migrations_dir = Path(os.path.dirname(plugin_info.install_path)) / "migrations"
    for path in sorted(migrations_dir.glob("*.py")):
        if path.name == "__init__.py":
            continue
        module = _load_module_from_path(path)
        upgrade_has_data_op = any(
            getattr(op, "kind", None) == "DataMigration"
            for op in getattr(module, "HEARTH_UPGRADE_OPS", [])
        )
        if hasattr(module, "data_upgrade") and not upgrade_has_data_op:
            issues.append(
                f"{path.name}: defines data_upgrade but HEARTH_UPGRADE_OPS "
                f"has no DataMigration entry"
            )
        if upgrade_has_data_op and not hasattr(module, "data_upgrade"):
            issues.append(
                f"{path.name}: HEARTH_UPGRADE_OPS has DataMigration entry "
                f"but no data_upgrade function"
            )
        # Symmetric for downgrade.
        downgrade_has_data_op = any(
            getattr(op, "kind", None) == "DataMigration"
            for op in getattr(module, "HEARTH_DOWNGRADE_OPS", [])
        )
        if hasattr(module, "data_downgrade") and not downgrade_has_data_op:
            issues.append(
                f"{path.name}: data_downgrade defined but HEARTH_DOWNGRADE_OPS "
                f"missing DataMigration entry"
            )
        if downgrade_has_data_op and not hasattr(module, "data_downgrade"):
            issues.append(
                f"{path.name}: HEARTH_DOWNGRADE_OPS has DataMigration but no "
                f"data_downgrade function"
            )
    return issues


def _load_module_from_path(path: Path) -> ModuleType:
    """Load a Python module from a filesystem path without registering it
    in `sys.modules`. Used by `_check_op_summary_correspondence` to introspect
    revision files without importing them through the normal package path."""
    spec = importlib.util.spec_from_file_location(f"_check_{path.stem}", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"could not load module from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
