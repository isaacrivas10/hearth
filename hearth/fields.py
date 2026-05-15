"""Plugin-facing Field declarator — see docs/core/fields.md.

Most entity fields don't need this — a bare type annotation plus an optional
default value is enough. Use `Field(...)` only when a kwarg the annotation
can't carry is needed (e.g., `auto_now_add`, `description`, `server_default`).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Literal, overload

from sqlalchemy import func


class _FieldMarker:
    """Sentinel returned by Field(); the Entity metaclass combines its kwargs
    with the column type inferred from the type annotation."""

    __slots__ = ("kwargs",)

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs


@overload
def Field(
    *,
    auto_now_add: Literal[True],
    init: Literal[False] = False,
    description: str | None = None,
    server_default: Any = None,
) -> Any: ...
@overload
def Field(
    *,
    auto_now: Literal[True],
    init: Literal[False] = False,
    description: str | None = None,
    server_default: Any = None,
) -> Any: ...
@overload
def Field(
    *,
    default: Any = ...,
    default_factory: Callable[[], Any] | None = None,
    description: str | None = None,
    server_default: Any = None,
    init: bool = True,
    primary_key: bool = False,
) -> Any: ...
def Field(  # noqa: N802 — capitalized to match Pydantic's Field convention
    *,
    default: Any = ...,
    default_factory: Callable[[], Any] | None = None,
    description: str | None = None,
    auto_now_add: bool = False,
    auto_now: bool = False,
    server_default: Any = None,
    init: bool = True,
    primary_key: bool = False,
) -> Any:
    """Field declarator with kernel-managed kwargs.

    Returns an opaque marker the Entity metaclass turns into a SQLAlchemy
    `mapped_column(...)` with the right column type from the annotation.

    `auto_now_add` and `auto_now` imply `init=False` — kernel-managed fields
    are not user-settable at construction.
    """
    kernel_managed = auto_now_add or auto_now
    kwargs: dict[str, Any] = {
        "init": False if kernel_managed else init,
        "primary_key": primary_key,
    }
    if default is not ...:
        kwargs["default"] = default
    if default_factory is not None:
        kwargs["default_factory"] = default_factory
    if description is not None:
        kwargs["doc"] = description
    if server_default is not None:
        kwargs["server_default"] = server_default
    if auto_now_add:
        kwargs.setdefault("default_factory", lambda: datetime.now(UTC))
    if auto_now:
        kwargs["onupdate"] = func.now()
    return _FieldMarker(**kwargs)


def ForeignKey(  # noqa: N802 — capitalized to match Field convention
    *,
    init: Literal[False] = False,
    default: Literal[None] = None,
) -> Any:
    """Marker for a foreign-key column whose value is set by SA from the
    paired relationship at flush time. Excludes the column from __init__
    so plugin authors construct entities by passing the relationship attr.

    The `init` and `default` keyword params exist solely for PEP 681 — they
    tell type checkers to treat the field as init=False with default None.
    Plugin authors call `ForeignKey()` with no arguments."""
    _ = init
    _ = default
    return _FieldMarker(init=False, default=None)
