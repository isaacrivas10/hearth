"""Tests for the public UoW factory and scope helpers."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from sqlalchemy import select

import hearth
from hearth import Entity, Event, System
from hearth.kernel._engine import make_async_engine  # pyright: ignore[reportPrivateUsage]
from hearth.kernel.persistence import METADATA, OUTBOX_TABLE


class _Widget(Entity, plugin="tx_factory_test"):
    name: str


class _WidgetCreated(Event, plugin="tx_factory_test"):
    name: str


@pytest.fixture
async def file_db(tmp_path: Path) -> AsyncIterator[str]:
    """Yield a sqlite URL backed by a real file. Set up _Widget's table once
    so the tests can save/query inside the transaction helpers."""
    db_path = tmp_path / "tx_factory_test.db"
    url = f"sqlite:///{db_path}"
    setup_engine = make_async_engine(url)
    try:
        async with setup_engine.begin() as conn:
            await conn.run_sync(
                METADATA.create_all,
                tables=[_Widget.__table__, OUTBOX_TABLE],
            )
    finally:
        await setup_engine.dispose()
    yield url


async def test_transaction_yields_uow_and_commits(file_db: str) -> None:
    engine = make_async_engine(file_db)
    try:
        async with hearth.transaction(engine, actor=System()) as uow:
            w = _Widget(name="alpha")
            await uow.save(w)
            uow.emit(_WidgetCreated(name="alpha"))
    finally:
        await engine.dispose()

    # Re-open in a fresh engine to verify the commit landed.
    verify_engine = make_async_engine(file_db)
    try:
        async with verify_engine.connect() as conn:
            widgets = list(await conn.execute(select(_Widget.__table__)))
            outbox = list(await conn.execute(select(OUTBOX_TABLE)))
        assert len(widgets) == 1
        assert widgets[0].name == "alpha"
        assert len(outbox) == 1
    finally:
        await verify_engine.dispose()


async def test_transaction_rolls_back_on_exception(file_db: str) -> None:
    engine = make_async_engine(file_db)
    try:
        with pytest.raises(RuntimeError):
            async with hearth.transaction(engine, actor=System()) as uow:
                w = _Widget(name="beta")
                await uow.save(w)
                raise RuntimeError("boom")
    finally:
        await engine.dispose()

    # Confirm the row was NOT committed.
    verify_engine = make_async_engine(file_db)
    try:
        async with verify_engine.connect() as conn:
            widgets = list(await conn.execute(select(_Widget.__table__)))
        assert widgets == []
    finally:
        await verify_engine.dispose()


async def test_transaction_factory_reuses_engine(file_db: str) -> None:
    """The factory holds a sessionmaker against one engine and produces
    multiple transactions, each yielding a fresh UoW."""
    engine = make_async_engine(file_db)
    try:
        make_uow = hearth.transaction_factory(engine)

        async with make_uow(actor=System()) as uow:
            await uow.save(_Widget(name="one"))

        async with make_uow(actor=System()) as uow:
            await uow.save(_Widget(name="two"))
    finally:
        await engine.dispose()

    verify_engine = make_async_engine(file_db)
    try:
        async with verify_engine.connect() as conn:
            widgets = list(await conn.execute(select(_Widget.__table__)))
        names = sorted(w.name for w in widgets)
        assert names == ["one", "two"]
    finally:
        await verify_engine.dispose()


async def test_scope_handles_engine_lifecycle(file_db: str) -> None:
    """`scope` opens an engine, runs one transaction, disposes the engine."""
    async with hearth.scope(file_db, actor=System()) as uow:
        await uow.save(_Widget(name="from-scope"))
        uow.emit(_WidgetCreated(name="from-scope"))

    verify_engine = make_async_engine(file_db)
    try:
        async with verify_engine.connect() as conn:
            widgets = list(await conn.execute(select(_Widget.__table__)))
            outbox = list(await conn.execute(select(OUTBOX_TABLE)))
        assert len(widgets) == 1
        assert widgets[0].name == "from-scope"
        assert len(outbox) == 1
    finally:
        await verify_engine.dispose()


async def test_scope_disposes_engine_on_exception(file_db: str) -> None:
    with pytest.raises(RuntimeError):
        async with hearth.scope(file_db, actor=System()) as uow:
            await uow.save(_Widget(name="will-roll-back"))
            raise RuntimeError("inside scope")

    # Engine should have been disposed; we can reopen and confirm no row.
    verify_engine = make_async_engine(file_db)
    try:
        async with verify_engine.connect() as conn:
            widgets = list(await conn.execute(select(_Widget.__table__)))
        assert widgets == []
    finally:
        await verify_engine.dispose()
