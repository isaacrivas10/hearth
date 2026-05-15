"""Tests for SA composite-column storage of Value subclasses.

Runs on SQLite always; additionally on Postgres when DATABASE_URL is set.
"""

from collections.abc import AsyncIterator, Callable

import pytest

from hearth import Entity, Value, agg
from hearth.testing._base import _SqlAlchemyHarness


class _Money(Value):
    amount_cents: int
    currency: str


class _CompOrder(Entity, plugin="comp_test"):
    total: _Money
    note: str = ""


class _NullCompOrder(Entity, plugin="comp_test"):
    total: _Money | None = None
    note: str = ""


class _Inner(Value):
    x: int


class _Outer(Value):
    inner: _Inner
    label: str


class _NestedHolder(Entity, plugin="comp_test"):
    outer: _Outer


class _DictBag(Value):
    tags: dict[str, str]


class _BagHolder(Entity, plugin="comp_test"):
    bag: _DictBag


_ALL: list[type[Entity]] = [_CompOrder, _NullCompOrder, _NestedHolder, _BagHolder]


@pytest.fixture
async def harness(
    make_harness: Callable[[], _SqlAlchemyHarness],
) -> AsyncIterator[_SqlAlchemyHarness]:
    h = make_harness()
    await h.setup(entities=_ALL)
    try:
        await h.reset()
        yield h
    finally:
        await h.teardown()


def test_composable_value_uses_composite_columns() -> None:
    cols = {c.name for c in _CompOrder.__table__.columns}
    assert "total_amount_cents" in cols
    assert "total_currency" in cols
    assert "total" not in cols


async def test_save_and_load_composite_roundtrips(harness: _SqlAlchemyHarness) -> None:
    o = _CompOrder(total=_Money(amount_cents=10000, currency="HNL"))
    async with harness.transaction() as uow:
        await uow.save(o)

    async with harness.transaction() as uow:
        loaded = await uow.get(_CompOrder, o.id)
    assert loaded.total == _Money(amount_cents=10000, currency="HNL")


async def test_nullable_composite_stores_all_nulls(harness: _SqlAlchemyHarness) -> None:
    o = _NullCompOrder()
    async with harness.transaction() as uow:
        await uow.save(o)

    async with harness.transaction() as uow:
        loaded = await uow.get(_NullCompOrder, o.id)
        rows = await uow.query(
            _NullCompOrder.total_amount_cents,  # pyright: ignore[reportAttributeAccessIssue]
            _NullCompOrder.total_currency,  # pyright: ignore[reportAttributeAccessIssue]
        ).all()
    assert loaded.total is None
    assert rows == [(None, None)]


async def test_nullable_composite_with_value_loads_correctly(
    harness: _SqlAlchemyHarness,
) -> None:
    o = _NullCompOrder(total=_Money(amount_cents=500, currency="USD"))
    async with harness.transaction() as uow:
        await uow.save(o)

    async with harness.transaction() as uow:
        loaded = await uow.get(_NullCompOrder, o.id)
    assert loaded.total == _Money(amount_cents=500, currency="USD")


async def test_filter_on_composite_subfield(harness: _SqlAlchemyHarness) -> None:
    big = _CompOrder(total=_Money(amount_cents=1000, currency="HNL"))
    small = _CompOrder(total=_Money(amount_cents=50, currency="HNL"))
    async with harness.transaction() as uow:
        await uow.save(big)
        await uow.save(small)

    async with harness.transaction() as uow:
        rows = await uow.query(_CompOrder).where(_CompOrder.total.amount_cents > 100).all()
    assert [r.id for r in rows] == [big.id]


async def test_aggregate_on_composite_subfield(harness: _SqlAlchemyHarness) -> None:
    async with harness.transaction() as uow:
        await uow.save(_CompOrder(total=_Money(amount_cents=1000, currency="HNL")))
        await uow.save(_CompOrder(total=_Money(amount_cents=2500, currency="HNL")))
        await uow.save(_CompOrder(total=_Money(amount_cents=400, currency="USD")))

    async with harness.transaction() as uow:
        rows = await (
            uow.query(
                _CompOrder.total.currency,
                agg.sum(_CompOrder.total.amount_cents).label("revenue"),
            )
            .group_by(_CompOrder.total.currency)
            .all()
        )
    by_currency = {row[0]: int(row[1]) for row in rows}
    assert by_currency == {"HNL": 3500, "USD": 400}


async def test_equality_filter_on_whole_composite(harness: _SqlAlchemyHarness) -> None:
    target = _Money(amount_cents=10000, currency="HNL")
    o = _CompOrder(total=target)
    async with harness.transaction() as uow:
        await uow.save(o)
        await uow.save(_CompOrder(total=_Money(amount_cents=99, currency="USD")))

    async with harness.transaction() as uow:
        match = await uow.query(_CompOrder).where(_CompOrder.total == target).one_or_none()
    assert match is not None
    assert match.id == o.id


def test_nested_value_falls_back_to_jsonb() -> None:
    cols = {c.name for c in _NestedHolder.__table__.columns}
    assert "outer" in cols
    assert "outer_label" not in cols
    assert "outer_inner" not in cols


def test_value_with_unsupported_field_falls_back_to_jsonb() -> None:
    cols = {c.name for c in _BagHolder.__table__.columns}
    assert "bag" in cols
    assert "bag_tags" not in cols
