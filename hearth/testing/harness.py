"""In-memory test harness — see docs/core/testing/harness.md.

Backed by SQLite-in-memory so plugin authors get the full SA-backed UnitOfWork
(including `query()`) without running Postgres.
"""

from __future__ import annotations

from hearth.kernel._engine import make_async_engine  # pyright: ignore[reportPrivateUsage]
from hearth.testing._base import _SqlAlchemyHarness  # pyright: ignore[reportPrivateUsage]


class Harness(_SqlAlchemyHarness):
    """In-memory plugin test harness, backed by SQLite-in-memory."""

    def __init__(self) -> None:
        engine = make_async_engine("sqlite:///:memory:")
        super().__init__(engine)
