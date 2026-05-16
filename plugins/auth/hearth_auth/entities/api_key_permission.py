"""ApiKeyPermission — direct grant from ApiKey to Permission (no role layer)."""

from __future__ import annotations

from hearth import EntityId, ForeignKey, References
from hearth_auth._base import Entity
from hearth_auth.entities.api_key import ApiKey
from hearth_auth.entities.permission import Permission


class ApiKeyPermission(Entity):
    api_key_id: EntityId = ForeignKey()
    api_key: ApiKey = References(ApiKey)
    permission_id: EntityId = ForeignKey()
    permission: Permission = References(Permission)
