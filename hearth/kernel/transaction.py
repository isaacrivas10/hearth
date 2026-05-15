"""SQLAlchemy-backed UnitOfWork using AsyncSession."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, TypeVar, overload

from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import AsyncSession, async_object_session

from hearth.errors import EntityNotFoundError
from hearth.identifiers import EntityId
from hearth.kernel.persistence import OUTBOX_TABLE
from hearth.kernel.query import Query
from hearth.primitives.entity import Entity
from hearth.primitives.event import Event
from hearth.primitives.identity import Identity

E = TypeVar("E", bound=Entity)


class _UnitOfWork:  # pyright: ignore[reportUnusedClass]
    """SQLAlchemy-backed UnitOfWork.

    Dialect-agnostic — wraps an AsyncSession that's already inside a
    transaction; the same class backs both Postgres and SQLite. The harness
    owns the session lifecycle. Events emitted via `emit` are buffered in
    memory and flushed as outbox rows just before the harness commits, so
    they land in the same SQL transaction as entity mutations (ADR-0007).
    """

    def __init__(self, session: AsyncSession, identity: Identity) -> None:
        self._session = session
        self._identity = identity
        self._event_buffer: list[Event] = []

    async def get(self, cls: type[E], id: EntityId) -> E:
        result = await self._session.get(cls, id)
        if result is None:
            raise EntityNotFoundError(cls, str(id))
        return result

    @overload
    def query(self, entity_class: type[E], /) -> Query[E]: ...
    @overload
    def query(self, *cols_or_entities: Any) -> Query[Any]: ...
    def query(self, *cols_or_entities: Any) -> Query[Any]:
        """Return a chainable Query bound to this UoW's session."""
        return Query(self._session, *cols_or_entities)

    async def save(self, entity: Entity) -> None:
        if async_object_session(entity) is self._session:
            return  # already tracked; mutations auto-flush
        state = inspect(entity)
        if state.persistent:
            # Detached but persistent — re-attach with merge.
            await self._session.merge(entity)
            return
        # Transient (new, untracked).
        self._session.add(entity)

    async def delete(self, entity: Entity) -> None:
        if async_object_session(entity) is not self._session:
            entity = await self._session.merge(entity)
        await self._session.delete(entity)

    def emit(self, event: Event) -> None:
        self._event_buffer.append(event)

    async def _flush_events(self) -> None:
        if not self._event_buffer:
            return
        rows = [
            {
                "id": str(EntityId.new()),
                "event_type": type(e).__name__,
                "payload": e.model_dump(mode="json"),
                "actor": self._identity.model_dump(mode="json"),
                "created_at": datetime.now(UTC),
            }
            for e in self._event_buffer
        ]
        await self._session.execute(OUTBOX_TABLE.insert(), rows)
        self._event_buffer.clear()
