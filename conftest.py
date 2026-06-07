"""Shared pytest fixtures — parametrizes kernel tests over both harness backends."""

import os
from collections.abc import Callable

import pytest

from hearth.testing import BaseHarness, InMemoryHarness, PostgresHarness

_HARNESS_KINDS = ["sqlite"] + (["postgres"] if "TEST_DATABASE_URL" in os.environ else [])


@pytest.fixture(params=_HARNESS_KINDS)
def make_harness(request: pytest.FixtureRequest) -> Callable[[], BaseHarness]:
    """Returns a builder for a fresh harness of the parametrized backend.

    SQLite always; Postgres additionally when TEST_DATABASE_URL is set. Test IDs
    show as `[sqlite]` / `[postgres]`.
    """
    kind = request.param
    if kind == "sqlite":
        return InMemoryHarness
    database_url = os.environ["TEST_DATABASE_URL"]
    return lambda: PostgresHarness(database_url)
