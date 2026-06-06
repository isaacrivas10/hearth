"""Verify make_async_engine produces a usable async engine on both dialects."""

import os
from pathlib import Path

import pytest
from sqlalchemy import text

from hearth.kernel._engine import (  # pyright: ignore[reportPrivateUsage]
    _to_async_url,
    make_async_engine,
)


def test_to_async_url_postgresql_plain() -> None:
    assert _to_async_url("postgresql://u:p@h/db") == "postgresql+asyncpg://u:p@h/db"


def test_to_async_url_postgresql_already_async() -> None:
    assert _to_async_url("postgresql+asyncpg://u:p@h/db") == "postgresql+asyncpg://u:p@h/db"


def test_to_async_url_postgres_alias() -> None:
    assert _to_async_url("postgres://u:p@h/db") == "postgresql+asyncpg://u:p@h/db"


def test_to_async_url_sqlite_passes_through() -> None:
    assert _to_async_url("sqlite:///foo.db") == "sqlite+aiosqlite:///foo.db"


def test_to_async_url_sqlite_already_async() -> None:
    assert _to_async_url("sqlite+aiosqlite:///foo.db") == "sqlite+aiosqlite:///foo.db"


async def test_sqlite_engine_executes_simple_query(tmp_path: Path) -> None:
    db_file = tmp_path / "hearth_test.db"
    engine = make_async_engine(f"sqlite:///{db_file}")
    try:
        async with engine.connect() as conn:
            result = await conn.execute(text("SELECT 1 AS n"))
            assert result.scalar_one() == 1
    finally:
        await engine.dispose()


async def test_sqlite_engine_enables_foreign_keys(tmp_path: Path) -> None:
    db_file = tmp_path / "fk_test.db"
    engine = make_async_engine(f"sqlite:///{db_file}")
    try:
        async with engine.connect() as conn:
            result = await conn.execute(text("PRAGMA foreign_keys"))
            assert result.scalar_one() == 1
    finally:
        await engine.dispose()


@pytest.mark.skipif("DATABASE_URL" not in os.environ, reason="DATABASE_URL not set")
async def test_postgres_engine_executes_simple_query() -> None:
    engine = make_async_engine(os.environ["DATABASE_URL"])
    try:
        async with engine.connect() as conn:
            result = await conn.execute(text("SELECT 1 AS n"))
            assert result.scalar_one() == 1
    finally:
        await engine.dispose()
