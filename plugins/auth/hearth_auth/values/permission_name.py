"""PermissionName Value — the (resource, action) discriminator.

When persisted on a Permission entity, the metaclass expands this Value
into two composite columns: `name_resource` and `name_action`.
"""

from __future__ import annotations

from hearth import Value, field_validator


class PermissionName(Value):
    """The (resource, action) pair identifying a permission.

    Both components must be non-empty and free of leading/trailing whitespace.
    This prevents pathological grants like `PermissionName(resource="", action="")`
    from being persisted; such a row would otherwise match a `has_permission(":")`
    lookup and silently grant unintended access.
    """

    resource: str
    action: str

    @field_validator("resource", "action")
    @classmethod
    def must_be_non_empty_token(cls, v: str) -> str:
        if not v or not v.strip() or v != v.strip():
            raise ValueError(
                "PermissionName components must be non-empty and free of "
                "leading/trailing whitespace",
            )
        return v
