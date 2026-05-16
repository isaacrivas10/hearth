"""Plugin-facing table-constraint markers.

Plugins declare constraints that span multiple columns (e.g., composite
unique indexes) via a `__hearth_table_args__` class attribute on the entity.
The kernel metaclass translates each marker into the equivalent SQLAlchemy
construct so plugins never import from `sqlalchemy`.
"""

from __future__ import annotations


class Unique:
    """Declare a (composite) UNIQUE constraint across one or more columns.

    Use in `__hearth_table_args__` on the entity:

        class Permission(Entity):
            name: PermissionName

            __hearth_table_args__ = (
                Unique("name_resource", "name_action"),
            )

    For composite Values, the sub-column names follow the kernel convention
    `<field>_<subfield>` (e.g., a `name: PermissionName` field with
    `resource` and `action` sub-fields produces `name_resource`,
    `name_action`).
    """

    __slots__ = ("columns",)

    def __init__(self, *columns: str) -> None:
        if not columns:
            raise ValueError("Unique requires at least one column name")
        self.columns = columns
