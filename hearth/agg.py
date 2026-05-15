"""Aggregation helpers for Query — see docs/core/query.md (TBD).

Thin wrappers over SQLAlchemy's `func.*` aggregates. We type-erase the
argument because plugin authors pass entity attributes whose static types
look like the field's value type (e.g. `Order.id: EntityId`) — SA's strict
overloads on `func.count(...)` would reject them, even though at runtime
they're SA `InstrumentedAttribute` descriptors. Plugin authors don't import
sqlalchemy; they import `from hearth import agg` and use these.
"""

from typing import Any

from sqlalchemy import func


def count(expr: Any = None) -> Any:
    return func.count() if expr is None else func.count(expr)


def sum(expr: Any) -> Any:
    return func.sum(expr)


def min(expr: Any) -> Any:
    return func.min(expr)


def max(expr: Any) -> Any:
    return func.max(expr)


def avg(expr: Any) -> Any:
    return func.avg(expr)


__all__ = ["avg", "count", "max", "min", "sum"]
