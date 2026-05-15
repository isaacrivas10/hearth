"""`hearth plugins ...` subcommand group."""

from __future__ import annotations

import typer

from hearth.cli import _wrap_command  # pyright: ignore[reportPrivateUsage]
from hearth.kernel.registry import Registry, RegistryBuildError

plugins_app = typer.Typer(help="Inspect installed plugins.", no_args_is_help=True)


def _format_deps(deps: list[str]) -> str:
    return ", ".join(deps) if deps else "(none)"


def _print_registry_build_error(err: RegistryBuildError) -> None:
    typer.echo("ERROR: cannot build plugin registry.\n", err=True)
    for issue in err.issues:
        typer.echo(f"  - {issue}", err=True)
    typer.echo("\nFix the above and rerun.", err=True)


@plugins_app.command("list")
@_wrap_command
def list_cmd() -> None:
    """List installed plugins."""
    try:
        registry = Registry.build()
    except RegistryBuildError as err:
        _print_registry_build_error(err)
        raise typer.Exit(1) from err

    aliases = sorted(registry.plugins)
    typer.echo(f"Installed plugins ({len(aliases)}):")
    if not aliases:
        typer.echo("  (none)")
        return
    for alias in aliases:
        info = registry.get(alias)
        typer.echo(
            f"  {alias:<14}{info.version:<8}{info.module:<24}"
            f"deps: {_format_deps(info.depends_on):<18}"
            f"extends: {_format_deps(info.extends)}",
        )


@plugins_app.command("deps")
@_wrap_command
def deps_cmd() -> None:
    """Show plugin dependency graph.

    v1 limitation: renders only direct dependents of each root plugin
    (two levels deep). Deeper chains (A → B → C) show all three plugins
    but C-under-B nesting is not visualised."""
    try:
        registry = Registry.build()
    except RegistryBuildError as err:
        _print_registry_build_error(err)
        raise typer.Exit(1) from err

    typer.echo("Dependency graph:")
    aliases = registry.topological_order()
    if not aliases:
        typer.echo("  (no plugins installed)")
        return

    dependents: dict[str, list[str]] = {a: [] for a in aliases}
    for a in aliases:
        for dep in registry.get(a).depends_on:
            dependents.setdefault(dep, []).append(a)

    roots = [a for a in aliases if not registry.get(a).depends_on]
    for root in roots:
        typer.echo(f"  {root}")
        for kid in dependents.get(root, []):
            relation = "extends" if root in registry.get(kid).extends else "uses"
            typer.echo(f"  └── {kid}       ({relation})")


@plugins_app.command("show")
@_wrap_command
def show_cmd(alias: str) -> None:
    """Show detailed info for one plugin."""
    try:
        registry = Registry.build()
    except RegistryBuildError as err:
        _print_registry_build_error(err)
        raise typer.Exit(1) from err

    if alias not in registry.plugins:
        installed = ", ".join(sorted(registry.plugins)) or "(none)"
        typer.echo(f"ERROR: no plugin named {alias!r}. Installed: {installed}", err=True)
        raise typer.Exit(1)

    info = registry.get(alias)
    extended_by = sorted(a for a, p in registry.plugins.items() if alias in p.extends)
    used_by = sorted(a for a, p in registry.plugins.items() if alias in p.depends_on)

    typer.echo(f"Plugin: {info.alias}")
    typer.echo(f"  Package:       {info.package} {info.version}")
    typer.echo(f"  Module:        {info.module}")
    typer.echo(f"  Install path:  {info.install_path}")
    typer.echo(f"  Depends on:    {_format_deps(info.depends_on)}")
    typer.echo(f"  Extends:       {_format_deps(info.extends)}")
    typer.echo(f"  Extended by:   {_format_deps(extended_by)}")
    typer.echo(f"  Used by:       {_format_deps(used_by)}")
    typer.echo("")
    entities_str = ", ".join(e.__name__ for e in info.entities) or "(none)"
    actions_str = ", ".join(a.__name__ for a in info.actions) or "(none)"
    events_str = ", ".join(e.__name__ for e in info.events) or "(none)"
    typer.echo(f"  Entities ({len(info.entities)}):  {entities_str}")
    typer.echo(f"  Actions ({len(info.actions)}):   {actions_str}")
    typer.echo(f"  Events ({len(info.events)}):    {events_str}")
