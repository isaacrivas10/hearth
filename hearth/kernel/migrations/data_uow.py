"""Open a UnitOfWork for a migration data hook.

The data UoW is scoped to ``PluginActor(alias=plugin)`` so rows the migration
creates are attributed to the plugin (visible in audit/outbox). Event emission
via ``uow.emit(...)`` is suppressed — migrations are not user activity and
should not trigger subscribers as if a user had acted.
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncEngine

from hearth.kernel.transaction import transaction
from hearth.primitives.actor import PluginActor
from hearth.primitives.event import Event
from hearth.unit_of_work import UnitOfWork


@contextlib.asynccontextmanager
async def open_data_uow(
    engine: AsyncEngine, *, plugin: str
) -> AsyncGenerator[UnitOfWork]:
    """Yield a UoW with the migration actor and event emission disabled.

    Suppression mechanism: ``uow.emit`` is replaced with a no-op. The
    underlying ``_UnitOfWork`` only writes outbox rows from its internal
    event buffer at commit time, so a no-op ``emit`` means nothing is ever
    buffered and nothing is ever flushed. Tests can verify suppression by
    asserting that no rows appear in the outbox table after a migration
    that calls ``uow.emit(...)``.
    """
    actor = PluginActor(alias=plugin)
    async with transaction(engine, actor=actor) as uow:
        uow.emit = _suppressed_emit  # type: ignore[method-assign]
        yield uow


def _suppressed_emit(_event: Event) -> None:
    """No-op: the data UoW does not enqueue outbox events."""
    return None
