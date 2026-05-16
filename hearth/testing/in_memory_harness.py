"""In-memory test harness — see docs/core/testing/harness.md.

Backed by SQLite-in-memory so plugin authors get the full SA-backed UnitOfWork
(including `query()`) without running Postgres.
"""

from __future__ import annotations

from hearth.kernel._engine import make_async_engine  # pyright: ignore[reportPrivateUsage]
from hearth.testing.base import BaseHarness


class InMemoryHarness(BaseHarness):
    """In-memory plugin test harness, backed by SQLite-in-memory.

    Named for its observable behavior (no persistence between instances)
    rather than the SQLite backend, which is an implementation detail.
    Mirrors the kernel pattern of naming concrete harnesses after their
    deployment shape: pair with `PostgresHarness` for parametrized fixtures.
    """

    def __init__(self) -> None:
        engine = make_async_engine("sqlite:///:memory:")
        super().__init__(engine)
