"""Plugin-facing References declarator — see docs/core/references.md (TBD).

Use `References()` to declare a foreign-key reference from one Entity to
another. The kernel synthesises the FK column and a relationship descriptor
from the marker; plugin authors never import from `sqlalchemy`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from hearth.primitives.entity import Entity


OnDelete = Literal["restrict", "cascade", "set_null"]

ON_DELETE_MAP: dict[OnDelete, str] = {
    "restrict": "RESTRICT",
    "cascade": "CASCADE",
    "set_null": "SET NULL",
}


class _ReferenceMarker:
    """Sentinel returned by References(); the Entity metaclass turns it into
    an FK column plus a relationship descriptor."""

    __slots__ = ("column", "on_delete", "target")

    target: type[Entity] | str
    on_delete: OnDelete
    column: str | None

    def __init__(
        self,
        target: type[Entity] | str,
        *,
        on_delete: OnDelete = "restrict",
        column: str | None = None,
    ) -> None:
        self.target = target
        self.on_delete = on_delete
        self.column = column


def References(  # noqa: N802 — capitalized to match Field convention
    target: type[Entity] | str,
    *,
    on_delete: OnDelete = "restrict",
    column: str | None = None,
) -> Any:
    return _ReferenceMarker(target, on_delete=on_delete, column=column)
