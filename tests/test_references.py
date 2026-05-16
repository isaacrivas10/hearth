"""Tests for `References()` — see docs/core/references.md (TBD)."""

from collections.abc import AsyncIterator, Callable

import pytest
import sqlalchemy.exc

from hearth import Entity, EntityId, ForeignKey, IntegrityError, References
from hearth.testing import BaseHarness


class _RCustomer(Entity, plugin="ref_test"):
    name: str


class _RBareOrder(Entity, plugin="ref_test"):
    customer_id: EntityId = ForeignKey()
    customer: _RCustomer


class _RCascadeOrder(Entity, plugin="ref_test"):
    customer_id: EntityId = ForeignKey()
    customer: _RCustomer = References(_RCustomer, on_delete="cascade")


class _RNullOrder(Entity, plugin="ref_test"):
    customer_id: EntityId | None = ForeignKey()
    customer: _RCustomer | None = References(_RCustomer, on_delete="set_null")


class _RSoftRefOrder(Entity, plugin="ref_test"):
    customer_id: EntityId


class _RAccount(Entity, plugin="ref_test"):
    label: str


class _RTransfer(Entity, plugin="ref_test"):
    from_account_id: EntityId = ForeignKey()
    from_account: _RAccount
    to_account_id: EntityId = ForeignKey()
    to_account: _RAccount


class _RNamedRefOrder(Entity, plugin="ref_test"):
    referred_by: EntityId = ForeignKey()
    referrer: _RCustomer = References(_RCustomer, column="referred_by")


class _RCategory(Entity, plugin="ref_test"):
    name: str
    parent_id: EntityId | None = ForeignKey()
    parent: "_RCategory | None" = References("_RCategory", on_delete="set_null")


_ALL_REF_ENTITIES: list[type[Entity]] = [
    _RCustomer,
    _RBareOrder,
    _RCascadeOrder,
    _RNullOrder,
    _RSoftRefOrder,
    _RAccount,
    _RTransfer,
    _RNamedRefOrder,
    _RCategory,
]


@pytest.fixture
async def harness(
    make_harness: Callable[[], BaseHarness],
) -> AsyncIterator[BaseHarness]:
    h = make_harness()
    await h.setup(entities=_ALL_REF_ENTITIES)
    try:
        await h.reset()
        yield h
    finally:
        await h.teardown()


def _columns(entity: type[Entity]) -> dict[str, object]:
    return {c.name: c for c in entity.__table__.columns}


def test_bare_entity_annotation_creates_fk_and_relationship() -> None:
    cols = _columns(_RBareOrder)
    assert "customer_id" in cols
    fks = list(cols["customer_id"].foreign_keys)  # type: ignore[attr-defined]
    assert len(fks) == 1
    assert str(fks[0].column.table.name) == _RCustomer.__tablename__
    assert hasattr(_RBareOrder, "customer")


async def test_explicit_references_passes_on_delete(harness: BaseHarness) -> None:
    cust = _RCustomer(name="Alice")
    async with harness.transaction() as uow:
        await uow.save(cust)
    order = _RCascadeOrder(customer=cust)
    async with harness.transaction() as uow:
        await uow.save(order)

    async with harness.transaction() as uow:
        await uow.delete(cust)

    async with harness.transaction() as uow:
        remaining = await uow.query(_RCascadeOrder).where(_RCascadeOrder.id == order.id).all()
    assert remaining == []


def test_set_null_requires_nullable_annotation() -> None:
    with pytest.raises(TypeError, match="set_null"):

        class _Bad(Entity, plugin="ref_test_bad"):
            customer_id: EntityId = ForeignKey()
            customer: _RCustomer = References(_RCustomer, on_delete="set_null")

        _ = _Bad


async def test_set_null_works_with_nullable(harness: BaseHarness) -> None:
    cust = _RCustomer(name="Alice")
    async with harness.transaction() as uow:
        await uow.save(cust)
    order = _RNullOrder(customer=cust)
    async with harness.transaction() as uow:
        await uow.save(order)

    async with harness.transaction() as uow:
        await uow.delete(cust)

    async with harness.transaction() as uow:
        reloaded = await uow.get(_RNullOrder, order.id)
    assert reloaded.customer_id is None


async def test_restrict_blocks_parent_delete(harness: BaseHarness) -> None:
    cust = _RCustomer(name="Alice")
    async with harness.transaction() as uow:
        await uow.save(cust)
    order = _RBareOrder(customer=cust)
    async with harness.transaction() as uow:
        await uow.save(order)

    with pytest.raises(IntegrityError):
        async with harness.transaction() as uow:
            await uow.delete(cust)


async def test_relationship_access_without_load_raises(harness: BaseHarness) -> None:
    cust = _RCustomer(name="Alice")
    order = _RBareOrder(customer=cust)
    async with harness.transaction() as uow:
        await uow.save(cust)
        await uow.save(order)

    async with harness.transaction() as uow:
        loaded = await uow.get(_RBareOrder, order.id)
        with pytest.raises(sqlalchemy.exc.InvalidRequestError):
            _ = loaded.customer


async def test_with_loaded_eager_loads_relationship(harness: BaseHarness) -> None:
    cust = _RCustomer(name="Alice")
    order = _RBareOrder(customer=cust)
    async with harness.transaction() as uow:
        await uow.save(cust)
        await uow.save(order)

    async with harness.transaction() as uow:
        loaded = await (
            uow.query(_RBareOrder)
            .where(_RBareOrder.id == order.id)
            .with_loaded(_RBareOrder.customer)
            .one()
        )
    assert loaded.customer.id == cust.id
    assert loaded.customer.name == "Alice"


async def test_relationship_traversal_in_filter(harness: BaseHarness) -> None:
    alice = _RCustomer(name="Alice")
    bob = _RCustomer(name="Bob")
    o1 = _RBareOrder(customer=alice)
    o2 = _RBareOrder(customer=bob)
    async with harness.transaction() as uow:
        await uow.save(alice)
        await uow.save(bob)
        await uow.save(o1)
        await uow.save(o2)

    async with harness.transaction() as uow:
        results = (
            await uow.query(_RBareOrder)
            .where(_RBareOrder.customer.has(_RCustomer.name == "Alice"))
            .all()
        )
    assert [r.id for r in results] == [o1.id]


def test_custom_column_name() -> None:
    cols = _columns(_RNamedRefOrder)
    assert "referred_by" in cols
    assert "referrer_id" not in cols
    fks = list(cols["referred_by"].foreign_keys)  # type: ignore[attr-defined]
    assert len(fks) == 1


def test_attr_ending_in_id_rejected() -> None:
    with pytest.raises(TypeError, match="must not end in '_id'"):

        class _BadOrder(Entity, plugin="ref_test_bad_id"):
            customer_id: _RCustomer = References(_RCustomer)

        _ = _BadOrder


def test_missing_fk_column_raises() -> None:
    with pytest.raises(TypeError, match="missing FK column"):

        class _BadMissing(Entity, plugin="ref_test_missing"):
            customer: _RCustomer = References(_RCustomer)

        _ = _BadMissing


def test_fk_column_wrong_type_raises() -> None:
    with pytest.raises(TypeError, match="must be annotated `EntityId`"):

        class _BadType(Entity, plugin="ref_test_bad_type"):
            customer_id: int
            customer: _RCustomer = References(_RCustomer)

        _ = _BadType


def test_nullability_mismatch_raises_relationship_nullable_column_not() -> None:
    with pytest.raises(TypeError, match="nullability mismatch"):

        class _BadNullA(Entity, plugin="ref_test_null_a"):
            customer_id: EntityId = ForeignKey()
            customer: _RCustomer | None = References(_RCustomer, on_delete="set_null")

        _ = _BadNullA


def test_nullability_mismatch_raises_column_nullable_relationship_not() -> None:
    with pytest.raises(TypeError, match="nullability mismatch"):

        class _BadNullB(Entity, plugin="ref_test_null_b"):
            customer_id: EntityId | None = ForeignKey()
            customer: _RCustomer = References(_RCustomer)

        _ = _BadNullB


def test_custom_column_name_must_exist() -> None:
    with pytest.raises(TypeError, match="missing FK column"):

        class _BadCustom(Entity, plugin="ref_test_custom"):
            referrer: _RCustomer = References(_RCustomer, column="customer_ref")

        _ = _BadCustom


async def test_multiple_fks_to_same_target(harness: BaseHarness) -> None:
    src = _RAccount(label="src")
    dst = _RAccount(label="dst")
    transfer = _RTransfer(from_account=src, to_account=dst)
    async with harness.transaction() as uow:
        await uow.save(src)
        await uow.save(dst)
        await uow.save(transfer)

    async with harness.transaction() as uow:
        loaded = await (
            uow.query(_RTransfer)
            .where(_RTransfer.id == transfer.id)
            .with_loaded(_RTransfer.from_account, _RTransfer.to_account)
            .one()
        )
    assert loaded.from_account.id == src.id
    assert loaded.to_account.id == dst.id
    assert loaded.from_account_id == src.id
    assert loaded.to_account_id == dst.id


async def test_self_referential_via_string(harness: BaseHarness) -> None:
    root = _RCategory(name="root")
    async with harness.transaction() as uow:
        await uow.save(root)
    child = _RCategory(name="child", parent=root)
    async with harness.transaction() as uow:
        await uow.save(child)

    async with harness.transaction() as uow:
        await uow.delete(root)

    async with harness.transaction() as uow:
        reloaded = await uow.get(_RCategory, child.id)
    assert reloaded.parent_id is None


async def test_construction_does_not_require_fk_column(harness: BaseHarness) -> None:
    cust = _RCustomer(name="Alice")
    async with harness.transaction() as uow:
        await uow.save(cust)
    order = _RBareOrder(customer=cust)
    async with harness.transaction() as uow:
        await uow.save(order)
    assert order.customer_id == cust.id


async def test_soft_reference_via_entity_id_still_works(harness: BaseHarness) -> None:
    cols = _columns(_RSoftRefOrder)
    assert "customer_id" in cols
    fks = list(cols["customer_id"].foreign_keys)  # type: ignore[attr-defined]
    assert fks == []

    bogus = EntityId.new()
    order = _RSoftRefOrder(customer_id=bogus)
    async with harness.transaction() as uow:
        await uow.save(order)

    async with harness.transaction() as uow:
        loaded = await uow.get(_RSoftRefOrder, order.id)
    assert loaded.customer_id == bogus
