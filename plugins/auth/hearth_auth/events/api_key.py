"""Events emitted by ApiKey-lifecycle and authentication actions."""

from __future__ import annotations

from hearth import EntityId
from hearth_auth._base import Event


class ApiKeyCreated(Event):
    api_key_id: EntityId
    key_prefix: str
    owner_id: EntityId | None


class ApiKeyAuthenticated(Event):
    api_key_id: EntityId


class ApiKeyRevoked(Event):
    api_key_id: EntityId
