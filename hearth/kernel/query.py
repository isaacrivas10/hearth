"""Async Query builder — see docs/core/query.md (TBD).

Wraps SQLAlchemy 2.x `select()` with a chainable, awaitable surface modelled
after the SA 1.x Query API. Plugin authors get this through `uow.query(...)`
and never import from `sqlalchemy`.
"""

from __future__ import annotations

from typing import Any, Self, cast

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy.sql.expression import CTE, Subquery

from hearth.primitives.entity import Entity

# Kernel-internal type aliases for Query method signatures. These resolve to
# Any but document intent: plugin expressions like `Customer.name == "Alice"`
# are SA `ColumnElement`s at runtime, but pyright sees them as the field's
# value type (because dataclass_transform doesn't expose `Mapped[T]`). The
# aliases name what each parameter is *meant* to receive.
type WhereClause = Any
type ColumnExpression = Any
type RelationshipAttr = Any
type JoinTarget = type[Entity] | Subquery | CTE


class Query[E]:
    """Async, chainable query builder bound to a UoW's session.

    Construct via `uow.query(...)`. Chainable methods return Self; terminal
    methods are awaitables that execute the underlying `select()` and shape
    the result.

    Shape rules:
    - `query(SomeEntity)` returns entity instances from terminal methods.
    - `query(SomeEntity.col, other_col, agg.sum(...))` returns Row tuples.
    """

    __slots__ = ("_scalar_shape", "_session", "_stmt")

    def __init__(self, session: AsyncSession, *cols_or_entities: Any) -> None:
        self._session = session
        self._stmt: Select[Any] = select(*cols_or_entities)
        self._scalar_shape = (
            len(cols_or_entities) == 1
            and isinstance(cols_or_entities[0], type)
            and issubclass(cols_or_entities[0], Entity)
        )

    # ---- chainable ----

    def where(self, *clauses: WhereClause) -> Self:
        self._stmt = self._stmt.where(*clauses)
        return self

    def order_by(self, *cols: ColumnExpression) -> Self:
        self._stmt = self._stmt.order_by(*cols)
        return self

    def group_by(self, *cols: ColumnExpression) -> Self:
        self._stmt = self._stmt.group_by(*cols)
        return self

    def having(self, *clauses: WhereClause) -> Self:
        self._stmt = self._stmt.having(*clauses)
        return self

    def join(
        self,
        target: JoinTarget,
        onclause: WhereClause = None,
        *,
        isouter: bool = False,
        full: bool = False,
    ) -> Self:
        if onclause is None:
            self._stmt = self._stmt.join(target, isouter=isouter, full=full)
        else:
            self._stmt = self._stmt.join(target, onclause, isouter=isouter, full=full)
        return self

    def limit(self, n: int) -> Self:
        self._stmt = self._stmt.limit(n)
        return self

    def offset(self, n: int) -> Self:
        self._stmt = self._stmt.offset(n)
        return self

    def distinct(self, *cols: ColumnExpression) -> Self:
        self._stmt = self._stmt.distinct(*cols)
        return self

    def with_loaded(self, *relationships: RelationshipAttr) -> Self:
        for rel in relationships:
            self._stmt = self._stmt.options(selectinload(rel))
        return self

    # ---- composition ----

    def subquery(self, name: str | None = None) -> Subquery:
        return self._stmt.subquery(name) if name else self._stmt.subquery()

    def cte(self, name: str | None = None) -> CTE:
        return self._stmt.cte(name) if name else self._stmt.cte()

    # ---- terminal awaitables ----

    async def all(self) -> list[E]:
        # When _scalar_shape is True, E is the Entity class and scalars().all()
        # returns list[E]. Otherwise E is Any (column projection); the cast
        # covers the row-tuple case where pyright can't prove the invariant.
        result = await self._session.execute(self._stmt)
        if self._scalar_shape:
            return list(result.scalars().all())
        return cast("list[E]", list(result.all()))

    async def one(self) -> E:
        result = await self._session.execute(self._stmt)
        if self._scalar_shape:
            return result.scalar_one()
        return cast("E", result.one())

    async def one_or_none(self) -> E | None:
        result = await self._session.execute(self._stmt)
        if self._scalar_shape:
            return result.scalar_one_or_none()
        return cast("E | None", result.one_or_none())

    async def first(self) -> E | None:
        # select() is immutable — .limit(1) returns a new statement.
        stmt = self._stmt.limit(1)
        result = await self._session.execute(stmt)
        if self._scalar_shape:
            return result.scalars().first()
        return cast("E | None", result.first())

    async def scalar(self) -> Any:
        return await self._session.scalar(self._stmt)

    async def count(self) -> int:
        # Wrap in a count-over-subquery so group_by/joins are honored.
        count_stmt = select(func.count()).select_from(self._stmt.subquery())
        result = await self._session.scalar(count_stmt)
        return result or 0

    async def exists(self) -> bool:
        # Cheaper than count(): one row at most.
        sub = self._stmt.limit(1).subquery()
        count_stmt = select(func.count()).select_from(sub)
        result = await self._session.scalar(count_stmt)
        return (result or 0) > 0
