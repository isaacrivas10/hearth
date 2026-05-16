"""Tests for the async Query builder.

Runs on SQLite always; additionally on Postgres when DATABASE_URL is set.
"""

from collections.abc import AsyncIterator, Callable

import pytest

from hearth import Entity, EntityId, Query, Value, agg, field_validator
from hearth.testing import BaseHarness


class _Phone(Value):
    raw: str

    @field_validator("raw")
    @classmethod
    def _e164(cls, v: str) -> str:
        if not v.startswith("+") or len(v) < 4:
            raise ValueError("must look E.164-ish")
        return v


class _QCust(Entity, plugin="qry_test"):
    name: str
    phone: _Phone
    note: str = ""
    score: int = 0


class _QOrder(Entity, plugin="qry_test"):
    customer_id: EntityId
    total_cents: int
    status: str = "open"


@pytest.fixture
async def harness(
    make_harness: Callable[[], BaseHarness],
) -> AsyncIterator[BaseHarness]:
    h = make_harness()
    await h.setup(entities=[_QCust, _QOrder])
    try:
        await h.reset()
        yield h
    finally:
        await h.teardown()


async def _seed_customers(h: BaseHarness) -> list[_QCust]:
    customers = [
        _QCust(name="Alice", phone=_Phone(raw="+50411111111"), note="vip", score=10),
        _QCust(name="Bob", phone=_Phone(raw="+50422222222"), note="vip", score=5),
        _QCust(name="Carol", phone=_Phone(raw="+50433333333"), note="", score=8),
        _QCust(name="Dave", phone=_Phone(raw="+50444444444"), note="", score=3),
    ]
    async with h.transaction() as uow:
        for c in customers:
            await uow.save(c)
    return customers


async def test_query_returns_typed_query(harness: BaseHarness) -> None:
    async with harness.transaction() as uow:
        q = uow.query(_QCust)
    assert isinstance(q, Query)


async def test_all_returns_entities_for_single_entity_query(
    harness: BaseHarness,
) -> None:
    await _seed_customers(harness)
    async with harness.transaction() as uow:
        rows = await uow.query(_QCust).all()
    assert len(rows) == 4
    assert all(isinstance(r, _QCust) for r in rows)


async def test_where_filters_results(harness: BaseHarness) -> None:
    await _seed_customers(harness)
    async with harness.transaction() as uow:
        vips = await uow.query(_QCust).where(_QCust.note == "vip").all()
    names = sorted(c.name for c in vips)
    assert names == ["Alice", "Bob"]


async def test_where_with_comparison_operators(harness: BaseHarness) -> None:
    await _seed_customers(harness)
    async with harness.transaction() as uow:
        high_score = await uow.query(_QCust).where(_QCust.score > 5).all()
    names = sorted(c.name for c in high_score)
    assert names == ["Alice", "Carol"]


async def test_order_by_and_limit(harness: BaseHarness) -> None:
    await _seed_customers(harness)
    async with harness.transaction() as uow:
        top2 = await uow.query(_QCust).order_by(_QCust.score.desc()).limit(2).all()
    assert [c.name for c in top2] == ["Alice", "Carol"]


async def test_one_or_none(harness: BaseHarness) -> None:
    await _seed_customers(harness)
    async with harness.transaction() as uow:
        alice = await uow.query(_QCust).where(_QCust.name == "Alice").one_or_none()
        nobody = await uow.query(_QCust).where(_QCust.name == "Zed").one_or_none()
    assert alice is not None and alice.name == "Alice"
    assert nobody is None


async def test_first_returns_one(harness: BaseHarness) -> None:
    await _seed_customers(harness)
    async with harness.transaction() as uow:
        first = await uow.query(_QCust).order_by(_QCust.score.desc()).first()
    assert first is not None
    assert first.name == "Alice"


async def test_count_honors_where(harness: BaseHarness) -> None:
    await _seed_customers(harness)
    async with harness.transaction() as uow:
        total = await uow.query(_QCust).count()
        vips = await uow.query(_QCust).where(_QCust.note == "vip").count()
    assert total == 4
    assert vips == 2


async def test_exists(harness: BaseHarness) -> None:
    await _seed_customers(harness)
    async with harness.transaction() as uow:
        has_vip = await uow.query(_QCust).where(_QCust.note == "vip").exists()
        has_unknown = await uow.query(_QCust).where(_QCust.note == "ghost").exists()
    assert has_vip is True
    assert has_unknown is False


async def test_column_projection_returns_rows(harness: BaseHarness) -> None:
    await _seed_customers(harness)
    async with harness.transaction() as uow:
        rows = await uow.query(_QCust.name, _QCust.score).order_by(_QCust.name).all()
    assert rows == [("Alice", 10), ("Bob", 5), ("Carol", 8), ("Dave", 3)]


async def test_aggregation_with_group_by(harness: BaseHarness) -> None:
    customers = await _seed_customers(harness)
    async with harness.transaction() as uow:
        await uow.save(_QOrder(customer_id=customers[0].id, total_cents=10000))
        await uow.save(_QOrder(customer_id=customers[0].id, total_cents=5000))
        await uow.save(
            _QOrder(customer_id=customers[1].id, total_cents=2500, status="closed"),
        )

    async with harness.transaction() as uow:
        rows = await (
            uow.query(
                _QOrder.customer_id,
                agg.sum(_QOrder.total_cents).label("total"),
                agg.count(_QOrder.id).label("orders"),
            )
            .group_by(_QOrder.customer_id)
            .order_by(_QOrder.customer_id)
            .all()
        )

    by_customer = {row[0]: (int(row[1]), int(row[2])) for row in rows}
    assert by_customer[customers[0].id] == (15000, 2)
    assert by_customer[customers[1].id] == (2500, 1)


async def test_subquery_composition(harness: BaseHarness) -> None:
    customers = await _seed_customers(harness)
    async with harness.transaction() as uow:
        await uow.save(_QOrder(customer_id=customers[0].id, total_cents=10000))
        await uow.save(_QOrder(customer_id=customers[0].id, total_cents=5000))
        await uow.save(_QOrder(customer_id=customers[1].id, total_cents=20000))

    async with harness.transaction() as uow:
        totals = (
            uow.query(
                _QOrder.customer_id,
                agg.sum(_QOrder.total_cents).label("total"),
            )
            .group_by(_QOrder.customer_id)
            .subquery()
        )
        top = await (
            uow.query(_QCust, totals.c.total)
            .join(totals, totals.c.customer_id == _QCust.id)
            .order_by(totals.c.total.desc())
            .limit(1)
            .all()
        )

    assert len(top) == 1
    cust, total = top[0]
    assert cust.id == customers[1].id
    assert int(total) == 20000
