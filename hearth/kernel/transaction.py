"""SQLAlchemy-backed UnitOfWork using AsyncSession."""

from __future__ import annotations

from collections.abc import AsyncGenerator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from datetime import UTC, datetime
from typing import Any, TypeVar, overload

from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError as _SAIntegrityError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_object_session,
    async_sessionmaker,
)

from hearth.errors import EntityNotFoundError, IntegrityError
from hearth.identifiers import EntityId
from hearth.kernel._engine import make_async_engine  # pyright: ignore[reportPrivateUsage]
from hearth.kernel.persistence import OUTBOX_TABLE
from hearth.kernel.query import Query
from hearth.primitives.actor import Actor
from hearth.primitives.entity import Entity
from hearth.primitives.event import Event

E = TypeVar("E", bound=Entity)


class _UnitOfWork:  # pyright: ignore[reportUnusedClass]
    """SQLAlchemy-backed UnitOfWork.

    Dialect-agnostic — wraps an AsyncSession that's already inside a
    transaction; the same class backs both Postgres and SQLite. The harness
    owns the session lifecycle. Events emitted via `emit` are buffered in
    memory and flushed as outbox rows just before the harness commits, so
    they land in the same SQL transaction as entity mutations (ADR-0007).
    """

    def __init__(self, session: AsyncSession, actor: Actor) -> None:
        self._session = session
        self._actor = actor
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
                "actor": self._serialize_actor(),
                "created_at": datetime.now(UTC),
            }
            for e in self._event_buffer
        ]
        await self._session.execute(OUTBOX_TABLE.insert(), rows)
        self._event_buffer.clear()

    def _serialize_actor(self) -> dict[str, Any]:
        actor = self._actor
        from hearth.primitives.actor import PluginActor

        meta: dict[str, Any] = {}
        if isinstance(actor, PluginActor):
            meta["alias"] = actor.alias
        actor_id = getattr(actor, "id", None)
        return {
            "kind": actor.actor_kind,
            "id": str(actor_id) if actor_id is not None else None,
            "meta": meta,
        }


@asynccontextmanager
async def transaction(
    engine: AsyncEngine,
    actor: Actor,
) -> AsyncGenerator[_UnitOfWork]:
    """Open a single UoW-scoped transaction against the given engine.

    Yields a `_UnitOfWork` typed as the public `UnitOfWork` Protocol. The
    context manager wraps `session.begin()`, so committing happens on
    successful exit and rollback happens on exception. Outbox events
    buffered via `uow.emit(...)` are flushed before commit.

    For repeated transactions against the same engine, prefer
    `transaction_factory(engine)`. For one-shot CLI scripts that create
    and dispose an engine themselves, prefer `scope(url, actor)`.
    """
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessionmaker() as session, session.begin():
            uow = _UnitOfWork(session, actor)
            yield uow
            await uow._flush_events()  # pyright: ignore[reportPrivateUsage]
    except _SAIntegrityError as exc:
        raise IntegrityError(str(exc.orig)) from exc


def transaction_factory(
    engine: AsyncEngine,
) -> Callable[..., AbstractAsyncContextManager[_UnitOfWork]]:
    """Bind a sessionmaker to `engine` once; return a callable that opens
    a fresh `transaction` against it per invocation. Mirrors the
    `sessionmaker(engine)` pattern from SQLAlchemy.

    Usage:
        make_uow = transaction_factory(engine)
        async with make_uow(actor=System()) as uow: ...
        async with make_uow(actor=user) as uow: ...
    """
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def make(*, actor: Actor) -> AsyncGenerator[_UnitOfWork]:
        try:
            async with sessionmaker() as session, session.begin():
                uow = _UnitOfWork(session, actor)
                yield uow
                await uow._flush_events()  # pyright: ignore[reportPrivateUsage]
        except _SAIntegrityError as exc:
            raise IntegrityError(str(exc.orig)) from exc

    return make


@asynccontextmanager
async def scope(
    url: str,
    *,
    actor: Actor,
) -> AsyncGenerator[_UnitOfWork]:
    """One-shot transactional scope: open engine, run one transaction,
    dispose engine. Convenience for CLI commands that don't otherwise
    hold an engine reference.

    Usage:
        async with hearth.scope(url, actor=System()) as uow:
            await SomeAction(...).handle(uow, System())
    """
    engine = make_async_engine(url)
    try:
        async with transaction(engine, actor=actor) as uow:
            yield uow
    finally:
        await engine.dispose()
