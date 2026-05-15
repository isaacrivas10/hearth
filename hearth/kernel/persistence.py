"""Persistence layer — annotation-to-column dispatch + outbox table.

Plugins never import from this module; it's the kernel-internal adapter
that makes the walled garden hold (ADR-0006). See docs/core/kernel/persistence.md.
"""

from __future__ import annotations

import types
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Union, get_args, get_origin

from pydantic import BaseModel
from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Dialect,
    Float,
    MetaData,
    String,
    Table,
    Text,
    TypeDecorator,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import CompositeProperty, DeclarativeBase, MappedAsDataclass, mapped_column

from hearth.identifiers import EntityId

# Shared metadata: every Entity subclass and the outbox share it so a single
# `create_all(tables=[...])` covers everything the kernel needs.
METADATA = MetaData()


class HearthBase(MappedAsDataclass, DeclarativeBase):
    """Internal SQLAlchemy base. Plugins inherit from `Entity`, not this."""

    metadata = METADATA


# Sentinel: instances of mapped_column()'s return type. Used by the Entity
# metaclass to detect "plugin author already wrote a column descriptor here."
_MAPPED_COLUMN_TYPE = type(mapped_column())


class _UtcDateTime(TypeDecorator[datetime]):
    """TZ-aware datetime portable across Postgres TIMESTAMPTZ and SQLite
    (which has no native tz type). Normalizes to UTC on write; re-attaches
    or converts to UTC on read."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: Any, dialect: Dialect) -> Any:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("naive datetime not allowed; Hearth requires tz-aware")
        return value.astimezone(UTC)

    def process_result_value(self, value: Any, dialect: Dialect) -> Any:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)  # SQLite: we stored UTC
        return value.astimezone(UTC)  # Postgres: tz-aware already


def is_optional(annotation: Any) -> bool:
    origin = get_origin(annotation)
    if origin in (types.UnionType, Union):
        return type(None) in get_args(annotation)
    return False


def strip_optional(annotation: Any) -> Any:
    origin = get_origin(annotation)
    if origin in (types.UnionType, Union):
        non_none = tuple(a for a in get_args(annotation) if a is not type(None))
        if len(non_none) == 1:
            return non_none[0]
    return annotation


def _value_column_type(value_class: type[BaseModel]) -> TypeDecorator[Any]:
    """A TypeDecorator that JSON-roundtrips a Pydantic Value subclass through JSONB."""

    class _T(TypeDecorator[Any]):
        impl = JSON
        cache_ok = True

        def load_dialect_impl(self, dialect: Dialect) -> Any:
            if dialect.name == "postgresql":
                return dialect.type_descriptor(JSONB())
            return dialect.type_descriptor(JSON())

        def process_bind_param(self, value: Any, dialect: Dialect) -> Any:
            return None if value is None else value.model_dump(mode="json")

        def process_result_value(self, value: Any, dialect: Dialect) -> Any:
            return None if value is None else value_class.model_validate(value)

    return _T()


def _is_composable_value(  # pyright: ignore[reportUnusedFunction]
    value_cls: type[BaseModel],
) -> bool:
    """True iff every field of value_cls is a single-column primitive that
    column_for_annotation can dispatch — i.e. no nested Values, dicts of
    Values, etc. Composable Values get expanded into composite columns by
    the metaclass; non-composable ones still fall back to JSONB."""
    for field_info in value_cls.model_fields.values():
        annot = field_info.annotation
        inner = strip_optional(annot)
        if isinstance(inner, type) and issubclass(inner, BaseModel):
            return False
        try:
            column_for_annotation(annot)
        except TypeError:
            return False
    return True


def _make_composite_factory(  # pyright: ignore[reportUnusedFunction]
    value_cls: type[BaseModel],
    field_names: list[str],
) -> Callable[..., BaseModel | None]:
    """Adapter so SA's composite() can construct Pydantic v2 models, which
    are kw-only by default. Returns None when every sub-column is NULL —
    that's how a nullable composite encodes 'no value'."""

    def factory(*values: Any) -> BaseModel | None:
        if all(v is None for v in values):
            return None
        return value_cls(**dict(zip(field_names, values, strict=True)))

    return factory


def _make_composite_comparator_factory(  # pyright: ignore[reportUnusedFunction]
    field_names: list[str],
) -> type[CompositeProperty.Comparator[Any]]:
    """Custom Comparator so `Order.total.amount_cents` resolves to the underlying
    sub-column for filters, group_by, and aggregates."""

    class _SubFieldComparator(CompositeProperty.Comparator[Any]):
        def __getattr__(self, name: str) -> Any:
            if name in field_names:
                return self.__clause_element__().clauses[field_names.index(name)]
            raise AttributeError(name)

    return _SubFieldComparator


def column_for_annotation(annotation: Any) -> Any:
    """Map a Python type annotation to a SQLAlchemy column type."""
    inner = strip_optional(annotation)
    if inner is str:
        return Text
    if inner is int:
        return BigInteger
    if inner is bool:
        return Boolean
    if inner is float:
        return Float
    if inner is datetime:
        return _UtcDateTime()
    if inner is dict or inner is list:
        return JSON().with_variant(JSONB(), "postgresql")
    if inner is EntityId:
        return String
    if isinstance(inner, type) and issubclass(inner, BaseModel):
        return _value_column_type(inner)
    raise TypeError(f"unsupported field annotation: {annotation!r}")


# ---- outbox table (ADR-0007) ----
OUTBOX_TABLE = Table(
    "_hearth_outbox",
    METADATA,
    Column("id", Text, primary_key=True),
    Column("event_type", Text, nullable=False),
    Column("payload", JSON().with_variant(JSONB(), "postgresql"), nullable=False),
    Column("actor", JSON().with_variant(JSONB(), "postgresql"), nullable=False),
    Column("created_at", _UtcDateTime(), nullable=False),
)
