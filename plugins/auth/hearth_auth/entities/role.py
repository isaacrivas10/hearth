"""Role entity — a named bundle of permissions, assignable to Users."""

from __future__ import annotations

from hearth_auth._base import Entity


class Role(Entity):
    name: str
    description: str | None = None
