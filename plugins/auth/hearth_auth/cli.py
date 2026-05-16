"""hearth auth ... CLI subcommands. Registered via the `hearth.cli`
entry-point group declared in pyproject.toml."""

from __future__ import annotations

import asyncio
import os
from typing import Annotated

import typer

import hearth
from hearth import IntegrityError, System
from hearth_auth.actions import (
    AssignPermissionToRole,
    AssignRoleToUser,
    CreatePermission,
    CreateRole,
    CreateUser,
)
from hearth_auth.entities.permission import Permission
from hearth_auth.values import PermissionName

auth_app = typer.Typer(
    help="User accounts, roles, and authentication.",
    no_args_is_help=True,
)


@auth_app.callback()
def _root() -> None:  # pyright: ignore[reportUnusedFunction]
    """hearth-auth: user accounts and authentication."""


@auth_app.command("bootstrap")
def bootstrap_cmd(
    email: Annotated[str, typer.Option(help="Admin user's email.")],
    password: Annotated[
        str,
        typer.Option(
            prompt=True,
            hide_input=True,
            confirmation_prompt=True,
            help="Admin password (prompted; will be hidden and confirmed).",
        ),
    ],
) -> None:
    """Create the first admin user with the wildcard '*:*' permission.

    Refuses to run if the wildcard Permission row already exists."""
    url = os.environ.get("DATABASE_URL")
    if not url:
        typer.echo(
            "ERROR: DATABASE_URL environment variable is required for bootstrap.",
            err=True,
        )
        raise typer.Exit(1)
    asyncio.run(_bootstrap_impl(url, email, password))


async def _bootstrap_impl(url: str, email: str, password: str) -> None:
    """Run the bootstrap inside a single transaction via `hearth.scope`.

    The wildcard-exists guard and the create-admin path run in one transaction
    so concurrent invocations can't both pass the guard. The composite UNIQUE
    constraint on `Permission(name_resource, name_action)` is the second line
    of defense: if a second invocation slips past the read-side guard, its
    INSERT for the wildcard permission fails and we surface a clean error.
    """
    from pydantic import SecretStr

    from hearth_commons import EmailAddress

    try:
        async with hearth.scope(url, actor=System()) as uow:
            wildcard_exists = await (
                uow.query(Permission)
                .where(Permission.name.resource == "*")
                .where(Permission.name.action == "*")
                .exists()
            )
            if wildcard_exists:
                typer.echo(
                    "ERROR: hearth-auth is already bootstrapped "
                    "(wildcard permission exists). Manage users via the Action surface.",
                    err=True,
                )
                raise typer.Exit(1)

            actor = System()
            user = await CreateUser(
                email=EmailAddress(raw=email),
                password=SecretStr(password),
                display_name="Admin",
            ).handle(uow, actor)
            role = await CreateRole(
                name="admin", description="Bootstrapped administrator role."
            ).handle(uow, actor)
            wildcard = await CreatePermission(
                name=PermissionName(resource="*", action="*"),
                description="Wildcard — grants all permissions.",
            ).handle(uow, actor)
            await AssignPermissionToRole(role_id=role.id, permission_id=wildcard.id).handle(
                uow, actor
            )
            await AssignRoleToUser(user_id=user.id, role_id=role.id).handle(uow, actor)
    except IntegrityError:
        typer.echo(
            "ERROR: hearth-auth bootstrap raced with another process; "
            "the wildcard permission was created concurrently. Re-run only "
            "if you intended to bootstrap.",
            err=True,
        )
        raise typer.Exit(1) from None

    typer.echo("Bootstrapped admin user:")
    typer.echo(f"  email: {email}")
    typer.echo("  role:  admin (wildcard *:*)")
    typer.echo(f"  id:    {user.id}")
