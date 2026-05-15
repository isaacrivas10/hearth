"""UnitOfWork Protocol — see docs/core/unit-of-work.md."""

from __future__ import annotations

from typing import Any, Protocol, TypeVar, overload

from hearth.identifiers import EntityId
from hearth.kernel.query import Query
from hearth.primitives.entity import Entity
from hearth.primitives.event import Event

E = TypeVar("E", bound=Entity)


class UnitOfWork(Protocol):
    """Plugin-facing transactional handle.

    Provided to actions by the kernel; plugins use it for all entity
    persistence and event emission within an action's transaction.
    """

    async def get(self, cls: type[E], id: EntityId) -> E:
        """Load an entity by id. Raises EntityNotFoundError if missing."""
        ...

    @overload
    def query(self, entity_class: type[E], /) -> Query[E]: ...
    @overload
    def query(self, *cols_or_entities: Any) -> Query[Any]: ...
    def query(self, *cols_or_entities: Any) -> Query[Any]:
        """Return a chainable Query for filtering, joining, aggregating."""
        ...

    async def save(self, entity: Entity) -> None:
        """Persist an entity. Assigns id if not set."""
        ...

    async def delete(self, entity: Entity) -> None:
        """Mark an entity for deletion."""
        ...

    def emit(self, event: Event) -> None:
        """Append an event to the in-transaction outbox buffer.

        Synchronous: no I/O. Events are flushed to durable storage on
        transaction commit.
        """
        ...
