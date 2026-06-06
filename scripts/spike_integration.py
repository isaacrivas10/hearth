"""In-depth integration spike — runs realistic plugin scenarios against the
user's Postgres to validate the kernel's ORM/Pydantic integration.

Sections cover ground the slice didn't reach: cross-entity references,
nested Value types, dirty tracking, bulk operations, identity map,
failure modes, outbox semantics, and things we haven't implemented but
want to know the behavior of.

Run with:
    DATABASE_URL=postgresql://... uv run python scripts/spike_integration.py
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import UTC, datetime

from pydantic import ValidationError, field_validator
from sqlalchemy import inspect, select
from sqlalchemy.exc import IntegrityError, InvalidRequestError

from hearth import (
    Action,
    Actor,
    Entity,
    EntityId,
    EntityNotFoundError,
    Event,
    Field,
    ForeignKey,
    References,
    UnitOfWork,
    Value,
    agg,
)
from hearth.testing import PostgresHarness


def section(name: str) -> None:
    print()
    print("=" * 70)
    print(f"  {name}")
    print("=" * 70)


def expect(label: str, condition: bool, detail: str = "") -> None:
    marker = "PASS" if condition else "FAIL"
    extra = f" — {detail}" if detail else ""
    print(f"  [{marker}] {label}{extra}")


def explain(text: str) -> None:
    print(f"        ↳ {text}")


# ---------- realistic plugin-author code ----------


class Money(Value):
    amount_cents: int
    currency: str

    @field_validator("currency")
    @classmethod
    def _iso_4217(cls, v: str) -> str:
        if len(v) != 3 or not v.isupper():
            raise ValueError("currency must be 3-letter ISO 4217 code")
        return v


class Address(Value):
    street: str
    city: str
    country: str  # 2-letter ISO 3166

    @field_validator("country")
    @classmethod
    def _iso_3166(cls, v: str) -> str:
        if len(v) != 2 or not v.isupper():
            raise ValueError("country must be 2-letter ISO 3166 code")
        return v


class PhoneNumber(Value):
    raw: str

    @field_validator("raw")
    @classmethod
    def _e164(cls, v: str) -> str:
        if not v.startswith("+") or len(v) < 4:
            raise ValueError("must look E.164-ish")
        return v


class SpikeCustomer(Entity, plugin="spike_commerce"):
    phone: PhoneNumber
    name: str | None = None
    note: str = ""
    address: Address | None = None


class SpikeOrder(Entity, plugin="spike_commerce"):
    customer_id: EntityId = ForeignKey()
    customer: SpikeCustomer = References(SpikeCustomer, on_delete="restrict")
    total: Money
    placed_at: datetime = Field(auto_now_add=True)
    delivered_at: datetime | None = None
    note: str = ""

    def validate(self) -> None:
        if self.delivered_at is not None and self.delivered_at < self.placed_at:
            raise ValueError("cannot deliver before placement")
        if self.total.amount_cents < 0:
            raise ValueError("order total cannot be negative")


class SpikeCascadeOrder(Entity, plugin="spike_commerce"):
    customer_id: EntityId = ForeignKey()
    customer: SpikeCustomer = References(SpikeCustomer, on_delete="cascade")
    total: Money


class SpikeNullOrder(Entity, plugin="spike_commerce"):
    customer_id: EntityId | None = ForeignKey()
    customer: SpikeCustomer | None = References(SpikeCustomer, on_delete="set_null")
    total: Money


class SpikeCustomerCreated(Event):
    customer_id: EntityId
    phone: PhoneNumber


class SpikeOrderPlaced(Event):
    order_id: EntityId
    customer_id: EntityId
    total: Money


# ---------- spike scenarios ----------


async def main() -> None:
    if "DATABASE_URL" not in os.environ:
        print("DATABASE_URL not set; aborting spike.")
        sys.exit(1)

    harness = PostgresHarness(os.environ["DATABASE_URL"])
    await harness.setup(
        entities=[SpikeCustomer, SpikeOrder, SpikeCascadeOrder, SpikeNullOrder],
    )
    try:
        await harness.reset()
        await section_1_value_validation()
        await section_2_nested_values_roundtrip(harness)
        await section_3_cross_entity_reference(harness)
        await section_4_identity_map(harness)
        await section_5_dirty_tracking(harness)
        await section_6_bulk_via_iteration(harness)
        await section_7_query_patterns(harness)
        await section_8_failure_modes(harness)
        await section_9_outbox_semantics(harness)
        await section_10_unimplemented_behaviors(harness)
        await section_11_query_builder(harness)
        await section_12_fk_semantics(harness)
        await section_13_composite_columns(harness)
    finally:
        await harness.teardown()
    print()
    print("=" * 70)
    print("  Spike complete.")
    print("=" * 70)


async def section_1_value_validation() -> None:
    section("1. Value construction enforces invariants")
    try:
        Money(amount_cents=100, currency="usd")
        expect("rejects lowercase currency", False)
    except ValidationError:
        expect("rejects lowercase currency", True)

    try:
        Address(street="X", city="Y", country="Honduras")
        expect("rejects 8-letter country", False)
    except ValidationError:
        expect("rejects 8-letter country", True)

    m = Money(amount_cents=12345, currency="HNL")
    expect("Money is structurally equal", m == Money(amount_cents=12345, currency="HNL"))
    expect(
        "Money is hashable (frozen)",
        hash(m) == hash(Money(amount_cents=12345, currency="HNL")),
    )


async def section_2_nested_values_roundtrip(harness: PostgresHarness) -> None:
    section("2. Nested Values round-trip through JSONB")
    addr = Address(street="Av La Paz 100", city="Tegucigalpa", country="HN")
    cust = SpikeCustomer(
        phone=PhoneNumber(raw="+50499998888"),
        name="Alice",
        address=addr,
    )
    async with harness.transaction() as uow:
        await uow.save(cust)

    async with harness.transaction() as uow:
        loaded = await uow.get(SpikeCustomer, cust.id)
    expect("nullable Address round-trips", loaded.address == addr)
    expect("phone round-trips", loaded.phone == PhoneNumber(raw="+50499998888"))
    explain(f"loaded.address is type {type(loaded.address).__name__}, value={loaded.address}")


async def section_3_cross_entity_reference(harness: PostgresHarness) -> None:
    section("3. Cross-entity reference via References() — FK at the DB level")
    cust = SpikeCustomer(phone=PhoneNumber(raw="+50488887777"), name="Bob")
    async with harness.transaction() as uow:
        await uow.save(cust)

    order = SpikeOrder(
        customer=cust,
        total=Money(amount_cents=15000, currency="HNL"),
    )
    async with harness.transaction() as uow:
        await uow.save(order)

    async with harness.transaction() as uow:
        loaded_order = await uow.get(SpikeOrder, order.id)
        loaded_cust = await uow.get(SpikeCustomer, loaded_order.customer_id)
    expect(
        "Order's customer_id is the saved Customer's id",
        loaded_order.customer_id == cust.id,
    )
    expect("can hop from Order.customer_id to Customer", loaded_cust.id == cust.id)

    # Deliberately construct without a customer so we can set a stale FK below
    # and verify Postgres rejects it. The customer kwarg would otherwise be required.
    bogus_order = SpikeOrder(  # pyright: ignore[reportCallIssue]
        total=Money(amount_cents=1, currency="HNL"),
    )
    bogus_order.customer_id = EntityId.new()
    try:
        async with harness.transaction() as uow:
            await uow.save(bogus_order)
        expect("stale customer_id is rejected at DB level", False)
    except IntegrityError:
        expect("stale customer_id is rejected at DB level", True, "FK enforced")
    explain("References() emits a real FOREIGN KEY constraint with ON DELETE RESTRICT.")
    explain("Plugin authors set the relationship; SA flushes the FK column on commit.")


async def section_4_identity_map(harness: PostgresHarness) -> None:
    section("4. SQLAlchemy identity map (within and across sessions)")
    cust = SpikeCustomer(phone=PhoneNumber(raw="+50477776666"), name="Carol")
    async with harness.transaction() as uow:
        await uow.save(cust)

    async with harness.transaction() as uow:
        a = await uow.get(SpikeCustomer, cust.id)
        b = await uow.get(SpikeCustomer, cust.id)
    expect("same instance returned by two get()s in one session", a is b)

    async with harness.transaction() as uow:
        c = await uow.get(SpikeCustomer, cust.id)
    expect("different Python instance across sessions", a is not c)
    expect("but equal by id", a == c)


async def section_5_dirty_tracking(harness: PostgresHarness) -> None:
    section("5. Dirty tracking — mutate loaded entity, no explicit save needed")
    cust = SpikeCustomer(phone=PhoneNumber(raw="+50466665555"), name="Dave")
    async with harness.transaction() as uow:
        await uow.save(cust)

    async with harness.transaction() as uow:
        loaded = await uow.get(SpikeCustomer, cust.id)
        loaded.name = "Dave Updated"
        loaded.note = "renamed by spike"
        # No explicit uow.save() — SQLAlchemy auto-flushes on commit

    async with harness.transaction() as uow:
        reloaded = await uow.get(SpikeCustomer, cust.id)
    expect("name update persisted without explicit save()", reloaded.name == "Dave Updated")
    expect("note update persisted", reloaded.note == "renamed by spike")
    explain("This is canonical SQLAlchemy: instances loaded in a session are tracked;")
    explain("attribute mutations auto-flush as UPDATE on commit. Plugin authors don't")
    explain("need to call save() for re-saves of loaded instances.")


async def section_6_bulk_via_iteration(harness: PostgresHarness) -> None:
    section("6. Bulk operations via iteration (no add_all yet)")
    customers = [
        SpikeCustomer(phone=PhoneNumber(raw=f"+5049{i:07d}"), name=f"Bulk{i}") for i in range(20)
    ]
    async with harness.transaction() as uow:
        for c in customers:
            await uow.save(c)

    async with harness.transaction() as uow:
        for c in customers:
            loaded = await uow.get(SpikeCustomer, c.id)
            assert loaded.name == c.name
    expect("20 inserts in one transaction", True)
    explain("Each save() is a separate session.add. SQLAlchemy batches the actual")
    explain("INSERTs at flush time. For larger volumes, a uow.save_all([...]) helper")
    explain("would be a future addition.")


async def section_7_query_patterns(harness: PostgresHarness) -> None:
    section("7. Query patterns — single-field, value-field, multi-condition")
    cust_a = SpikeCustomer(phone=PhoneNumber(raw="+50455554444"), name="Eve", note="vip")
    cust_b = SpikeCustomer(phone=PhoneNumber(raw="+50433332222"), name="Frank", note="vip")
    async with harness.transaction() as uow:
        await uow.save(cust_a)
        await uow.save(cust_b)

    # Single-field filter
    async with harness.transaction() as uow:
        match = await uow.query(SpikeCustomer).where(SpikeCustomer.name == "Eve").one_or_none()
        nope = await uow.query(SpikeCustomer).where(SpikeCustomer.name == "Nobody").one_or_none()
    expect(
        "query.where(name).one_or_none() returns the right row",
        match is not None and match.id == cust_a.id,
    )
    expect("query.where(name) with no match returns None", nope is None)

    # Filter by Value field — TypeDecorator handles serialization
    async with harness.transaction() as uow:
        by_phone = (
            await uow.query(SpikeCustomer)
            .where(SpikeCustomer.phone == PhoneNumber(raw="+50455554444"))
            .one_or_none()
        )
    expect(
        "query by Value field works (JSONB equality)",
        by_phone is not None and by_phone.id == cust_a.id,
    )

    # Multi-condition filter
    async with harness.transaction() as uow:
        multi = (
            await uow.query(SpikeCustomer)
            .where(SpikeCustomer.name == "Eve", SpikeCustomer.note == "vip")
            .one_or_none()
        )
        wrong = (
            await uow.query(SpikeCustomer)
            .where(SpikeCustomer.name == "Eve", SpikeCustomer.note == "not-vip")
            .one_or_none()
        )
    expect("multi-condition ANDs correctly", multi is not None and multi.id == cust_a.id)
    expect("multi-condition with non-matching value returns None", wrong is None)


async def section_8_failure_modes(harness: PostgresHarness) -> None:
    section("8. Failure modes — rollback, validation, action exceptions")

    # Cross-field validate() raising at construction
    try:
        SpikeOrder(
            customer=SpikeCustomer(phone=PhoneNumber(raw="+50411112222")),
            total=Money(amount_cents=100, currency="HNL"),
            delivered_at=datetime(2025, 1, 1, tzinfo=UTC),  # before placed_at (auto_now_add → now)
        )
        expect("validate() catches delivered-before-placed", False)
    except ValueError as e:
        expect("validate() catches delivered-before-placed", True, str(e))

    # Action raising mid-transaction → rollback
    class ExplodingAction(Action):
        name: str

        async def handle(self, uow: UnitOfWork, actor: Actor) -> None:
            c = SpikeCustomer(phone=PhoneNumber(raw="+50411112222"), name=self.name)
            await uow.save(c)
            uow.emit(SpikeCustomerCreated(customer_id=c.id, phone=c.phone))
            raise RuntimeError("simulated explosion")

    try:
        await harness.run(ExplodingAction(name="Should not persist"))
        expect("action raise propagates out", False)
    except RuntimeError:
        expect("action raise propagates out", True)

    # Verify rollback discarded the save and the event
    async with harness.transaction() as uow:
        not_found = (
            await uow.query(SpikeCustomer)
            .where(SpikeCustomer.name == "Should not persist")
            .one_or_none()
        )
    expect("entity rolled back on action failure", not_found is None)
    events = await harness.events_of_type(SpikeCustomerCreated)
    persisted_phones = [e.phone.raw for e in events]
    expect(
        "outbox event rolled back on action failure",
        "+50411112222" not in persisted_phones,
    )


async def section_9_outbox_semantics(harness: PostgresHarness) -> None:
    section("9. Outbox semantics — multiple events in one transaction")

    class MultiEmit(Action):
        async def handle(self, uow: UnitOfWork, actor: Actor) -> SpikeCustomer:
            c = SpikeCustomer(phone=PhoneNumber(raw="+50400001111"))
            await uow.save(c)
            uow.emit(SpikeCustomerCreated(customer_id=c.id, phone=c.phone))
            order = SpikeOrder(
                customer=c,
                total=Money(amount_cents=999, currency="HNL"),
            )
            await uow.save(order)
            uow.emit(SpikeOrderPlaced(order_id=order.id, customer_id=c.id, total=order.total))
            return c

    customer = await harness.run(MultiEmit())
    customer_events = [
        e
        for e in await harness.events_of_type(SpikeCustomerCreated)
        if e.customer_id == customer.id
    ]
    order_events = [
        e for e in await harness.events_of_type(SpikeOrderPlaced) if e.customer_id == customer.id
    ]
    expect("CustomerCreated landed in outbox", len(customer_events) == 1)
    expect("OrderPlaced landed in outbox", len(order_events) == 1)
    expect(
        "events reference the right customer",
        order_events[0].customer_id == customer_events[0].customer_id,
    )
    explain("Both events were inserted into _hearth_outbox in the same SQL transaction")
    explain("as the entity rows. Atomicity verified by the rollback test in §8.")


async def section_10_unimplemented_behaviors(harness: PostgresHarness) -> None:
    section("10. Behaviors of things we haven't directly implemented")

    # 10a: re-attaching a detached entity in a new session (merge path)
    cust = SpikeCustomer(phone=PhoneNumber(raw="+50422223333"), name="Detach")
    async with harness.transaction() as uow:
        await uow.save(cust)

    cust.name = "Detach Updated"  # mutate after first session closed
    try:
        async with harness.transaction() as uow:
            await uow.save(cust)  # detached + persistent → merge path
        async with harness.transaction() as uow:
            reloaded = await uow.get(SpikeCustomer, cust.id)
        expect("detached entity re-attaches via merge on save", reloaded.name == "Detach Updated")
    except Exception as e:
        expect("detached entity re-attaches via merge on save", False, str(e))

    # 10b: get() with non-existent id raises EntityNotFoundError
    async with harness.transaction() as uow:
        try:
            await uow.get(SpikeCustomer, EntityId.new())
            expect("get() with bogus id raises", False)
        except EntityNotFoundError:
            expect("get() with bogus id raises EntityNotFoundError", True)

    # 10d: try mutating a frozen Value
    m = Money(amount_cents=100, currency="HNL")
    try:
        m.amount_cents = 200  # type: ignore[misc]
        expect("Value is frozen — assignment raises", False)
    except ValidationError:
        expect("Value is frozen — assignment raises ValidationError", True)

    # 10e: try direct __setattr__ validation on Entity
    cust2 = SpikeCustomer(phone=PhoneNumber(raw="+50488889999"))
    try:
        cust2.name = 12345  # type: ignore[assignment]
        expect("Entity setattr validates type", False)
    except ValidationError:
        expect("Entity setattr validates type after construction", True)

    # 10f: ORM SELECT through plugin-defined select() — does it work via session.execute?
    async with harness.transaction() as uow:
        # Reach through the UoW's session deliberately — should we expose this?
        # pyright sees `note == "vip"` as `bool` (SA's InstrumentedAttribute typing
        # is opaque through our dataclass_transform); at runtime it's a SQL clause.
        stmt = select(SpikeCustomer).where(SpikeCustomer.note == "vip").limit(2)  # pyright: ignore[reportArgumentType]
        result = await uow._session.execute(stmt)  # pyright: ignore[reportPrivateUsage]
        rows = result.scalars().all()
    expect(
        "raw select() against entity class returns instances",
        all(isinstance(r, SpikeCustomer) for r in rows),  # pyright: ignore[reportUnnecessaryIsInstance]
        f"got {len(rows)} customers",
    )
    explain("This shows what ORM access looks like under the hood. Plugin authors")
    explain("shouldn't reach in via uow._session — they use uow.query(...) instead,")
    explain("which is demonstrated in section 11.")

    # 10g: inspect on a constructed entity to see its state
    fresh = SpikeCustomer(phone=PhoneNumber(raw="+50499997777"), name="Inspect")
    state = inspect(fresh, raiseerr=False)
    expect(
        "inspect() on a fresh entity returns transient state",
        state is not None and state.transient,  # pyright: ignore[reportUnnecessaryComparison]
    )


async def section_11_query_builder(harness: PostgresHarness) -> None:
    section("11. Query builder — filtering, aggregation, joins, subqueries")

    # Seed: customers with varied notes/scores, plus orders for aggregation.
    await harness.reset()
    customers = [
        SpikeCustomer(phone=PhoneNumber(raw="+50411111111"), name="Alice", note="vip"),
        SpikeCustomer(phone=PhoneNumber(raw="+50422222222"), name="Bob", note="vip"),
        SpikeCustomer(phone=PhoneNumber(raw="+50433333333"), name="Carol", note=""),
        SpikeCustomer(phone=PhoneNumber(raw="+50444444444"), name="Dave", note=""),
    ]
    async with harness.transaction() as uow:
        for c in customers:
            await uow.save(c)
        # Orders: Alice has 2 (15000), Bob has 1 (2500), Carol has 1 (8000), Dave has none.
        hnl = "HNL"
        for cust_idx, cents in [(0, 10000), (0, 5000), (1, 2500), (2, 8000)]:
            await uow.save(
                SpikeOrder(
                    customer=customers[cust_idx],
                    total=Money(amount_cents=cents, currency=hnl),
                ),
            )

    # 11a: simple where with comparison operator
    async with harness.transaction() as uow:
        vips = await uow.query(SpikeCustomer).where(SpikeCustomer.note == "vip").all()
    expect("where(== 'vip').all() returns 2 entities", len(vips) == 2)
    # isinstance is tautological per pyright (type narrowing works), kept as
    # runtime evidence that uow.query(C).all() actually returns C instances.
    expect(
        "results are entity instances",
        all(isinstance(v, SpikeCustomer) for v in vips),  # pyright: ignore[reportUnnecessaryIsInstance]
    )

    # 11b: order_by + limit
    async with harness.transaction() as uow:
        first_two = await uow.query(SpikeCustomer).order_by(SpikeCustomer.name).limit(2).all()
    expect(
        "order_by(name).limit(2) returns Alice, Bob",
        [c.name for c in first_two] == ["Alice", "Bob"],
    )

    # 11c: count with where filter
    async with harness.transaction() as uow:
        n_vips = await uow.query(SpikeCustomer).where(SpikeCustomer.note == "vip").count()
        n_total = await uow.query(SpikeCustomer).count()
    expect("count(where vip) == 2", n_vips == 2)
    expect("count() == 4", n_total == 4)

    # 11d: exists
    async with harness.transaction() as uow:
        has_vip = await uow.query(SpikeCustomer).where(SpikeCustomer.note == "vip").exists()
        has_ghost = await uow.query(SpikeCustomer).where(SpikeCustomer.note == "ghost").exists()
    expect("exists(vip) is True", has_vip is True)
    expect("exists(ghost) is False", has_ghost is False)

    # 11e: one_or_none
    async with harness.transaction() as uow:
        alice = await uow.query(SpikeCustomer).where(SpikeCustomer.name == "Alice").one_or_none()
        nobody = await uow.query(SpikeCustomer).where(SpikeCustomer.name == "Zelda").one_or_none()
    expect("one_or_none(Alice) returns Alice", alice is not None and alice.name == "Alice")
    expect("one_or_none(Zelda) returns None", nobody is None)

    # 11f: column projection — returns Row tuples, not entities
    async with harness.transaction() as uow:
        names_and_notes = (
            await uow.query(SpikeCustomer.name, SpikeCustomer.note)
            .order_by(SpikeCustomer.name)
            .all()
        )
    expect(
        "column projection returns Row tuples in order",
        names_and_notes == [("Alice", "vip"), ("Bob", "vip"), ("Carol", ""), ("Dave", "")],
    )

    # 11g: aggregation with group_by — the user's MetricLoad pattern shape
    async with harness.transaction() as uow:
        rows = await (
            uow.query(
                SpikeOrder.customer_id,
                agg.count(SpikeOrder.id).label("orders"),
            )
            .group_by(SpikeOrder.customer_id)
            .order_by(SpikeOrder.customer_id)
            .all()
        )
    by_customer = {row[0]: int(row[1]) for row in rows}
    expect("Alice has 2 orders", by_customer.get(customers[0].id) == 2)
    expect("Bob has 1 order", by_customer.get(customers[1].id) == 1)
    expect("Carol has 1 order", by_customer.get(customers[2].id) == 1)
    expect("Dave has no row (group_by skips empty)", customers[3].id not in by_customer)

    # 11h: subquery composition + join — top-spending customer
    async with harness.transaction() as uow:
        # subquery: customer_id -> total revenue in cents (composite sub-column)
        order_totals = (
            uow.query(
                SpikeOrder.customer_id,
                agg.sum(SpikeOrder.total.amount_cents).label("revenue"),
            )
            .group_by(SpikeOrder.customer_id)
            .subquery()
        )
        rankings = await (
            uow.query(SpikeCustomer, order_totals.c.revenue)
            .join(order_totals, order_totals.c.customer_id == SpikeCustomer.id)
            .order_by(order_totals.c.revenue.desc())
            .all()
        )
    expect("subquery + join returns 3 ranked customers (Dave excluded)", len(rankings) == 3)
    top_cust, top_revenue = rankings[0]
    expect(
        "top customer is Alice with 15000 cents revenue",
        top_cust.name == "Alice" and int(top_revenue) == 15000,
    )

    # 11i: Plugin authors never imported sqlalchemy.
    explain("Throughout section 11, no `from sqlalchemy import ...` was needed.")
    explain("Filter expressions come from entity attributes; aggregates from `agg`;")
    explain("Subquery/join composition stays inside the Query builder. The kernel")
    explain("translates everything to select() under the hood.")


async def section_12_fk_semantics(harness: PostgresHarness) -> None:
    section("12. References() — restrict, cascade, set_null, lazy=raise, with_loaded")
    await harness.reset()

    # 12a: ON DELETE RESTRICT blocks parent delete while children exist.
    parent = SpikeCustomer(phone=PhoneNumber(raw="+50412121212"), name="Parent-R")
    async with harness.transaction() as uow:
        await uow.save(parent)
    child_r = SpikeOrder(customer=parent, total=Money(amount_cents=100, currency="HNL"))
    async with harness.transaction() as uow:
        await uow.save(child_r)

    try:
        async with harness.transaction() as uow:
            await uow.delete(parent)
        expect("ON DELETE RESTRICT blocks parent delete", False)
    except IntegrityError:
        expect("ON DELETE RESTRICT blocks parent delete", True)

    # 12b: ON DELETE CASCADE removes children when parent is deleted.
    parent_c = SpikeCustomer(phone=PhoneNumber(raw="+50413131313"), name="Parent-C")
    async with harness.transaction() as uow:
        await uow.save(parent_c)
    cascade_child = SpikeCascadeOrder(
        customer=parent_c, total=Money(amount_cents=200, currency="HNL")
    )
    async with harness.transaction() as uow:
        await uow.save(cascade_child)

    async with harness.transaction() as uow:
        await uow.delete(parent_c)

    async with harness.transaction() as uow:
        survivors = (
            await uow.query(SpikeCascadeOrder).where(SpikeCascadeOrder.id == cascade_child.id).all()
        )
    expect("ON DELETE CASCADE removed the child", survivors == [])

    # 12c: ON DELETE SET NULL nullifies the FK on parent delete.
    parent_n = SpikeCustomer(phone=PhoneNumber(raw="+50414141414"), name="Parent-N")
    async with harness.transaction() as uow:
        await uow.save(parent_n)
    null_child = SpikeNullOrder(customer=parent_n, total=Money(amount_cents=300, currency="HNL"))
    async with harness.transaction() as uow:
        await uow.save(null_child)

    async with harness.transaction() as uow:
        await uow.delete(parent_n)

    async with harness.transaction() as uow:
        reloaded = await uow.get(SpikeNullOrder, null_child.id)
    expect("ON DELETE SET NULL cleared the FK", reloaded.customer_id is None)

    # 12d: lazy="raise" — accessing the relationship without with_loaded raises.
    cust_l = SpikeCustomer(phone=PhoneNumber(raw="+50415151515"), name="Lazy")
    order_l = SpikeOrder(customer=cust_l, total=Money(amount_cents=400, currency="HNL"))
    async with harness.transaction() as uow:
        await uow.save(cust_l)
        await uow.save(order_l)

    async with harness.transaction() as uow:
        loaded = await uow.get(SpikeOrder, order_l.id)
        try:
            _ = loaded.customer
            expect("lazy='raise' on unloaded relationship raises", False)
        except InvalidRequestError:
            expect("lazy='raise' on unloaded relationship raises", True)

    # 12e: with_loaded resolves the relationship eagerly.
    async with harness.transaction() as uow:
        eager = await (
            uow.query(SpikeOrder)
            .where(SpikeOrder.id == order_l.id)
            .with_loaded(SpikeOrder.customer)
            .one()
        )
    expect(
        "with_loaded(SpikeOrder.customer) lets .customer resolve",
        eager.customer.id == cust_l.id,
    )

    # 12f: relationship traversal in filter (.has()).
    cust_t = SpikeCustomer(phone=PhoneNumber(raw="+50416161616"), name="Traverse")
    order_t = SpikeOrder(customer=cust_t, total=Money(amount_cents=500, currency="HNL"))
    async with harness.transaction() as uow:
        await uow.save(cust_t)
        await uow.save(order_t)

    async with harness.transaction() as uow:
        # SpikeOrder.customer is the SA relationship descriptor at runtime (with
        # .has()), but pyright sees the annotation type SpikeCustomer.
        matches = (
            await uow.query(SpikeOrder)
            .where(SpikeOrder.customer.has(SpikeCustomer.name == "Traverse"))  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType]
            .all()
        )
    expect("traversal-in-filter (.customer.has(...)) returns matching orders", len(matches) == 1)
    explain("References() gives plugin authors a real FK plus an eager-loadable")
    explain("relationship descriptor. Soft-reference via `customer_id: EntityId` is")
    explain("preserved as the escape hatch when no FK semantics are wanted.")


async def section_13_composite_columns(harness: PostgresHarness) -> None:
    section("13. Composite columns for Value types — sub-field SQL access")
    await harness.reset()

    cust = SpikeCustomer(phone=PhoneNumber(raw="+50490909090"), name="Composite")
    async with harness.transaction() as uow:
        await uow.save(cust)
        for cents, currency in [(10000, "HNL"), (5000, "HNL"), (2500, "USD"), (800, "USD")]:
            await uow.save(
                SpikeOrder(customer=cust, total=Money(amount_cents=cents, currency=currency)),
            )

    cols = {c.name for c in SpikeOrder.__table__.columns}
    expect(
        "Money composite expanded into total_amount_cents + total_currency",
        "total_amount_cents" in cols and "total_currency" in cols,
    )
    expect("no opaque 'total' JSONB column on SpikeOrder", "total" not in cols)

    async with harness.transaction() as uow:
        big = await uow.query(SpikeOrder).where(SpikeOrder.total.amount_cents > 3000).all()
    expect("filter on total.amount_cents > 3000 returns 2 orders", len(big) == 2)

    async with harness.transaction() as uow:
        rows = await (
            uow.query(
                SpikeOrder.total.currency,
                agg.sum(SpikeOrder.total.amount_cents).label("revenue"),
            )
            .group_by(SpikeOrder.total.currency)
            .all()
        )
    by_currency = {row[0]: int(row[1]) for row in rows}
    expect("HNL revenue is 15000", by_currency.get("HNL") == 15000)
    expect("USD revenue is 3300", by_currency.get("USD") == 3300)

    async with harness.transaction() as uow:
        match = (
            await uow.query(SpikeOrder)
            .where(SpikeOrder.total == Money(amount_cents=10000, currency="HNL"))
            .one_or_none()
        )
    expect(
        "equality filter on whole composite still works",
        match is not None and match.total.amount_cents == 10000,
    )
    explain("Composite columns give plugin authors typed sub-field SQL access without")
    explain("ever importing sqlalchemy. The Value class shape is unchanged; the kernel")
    explain("metaclass just expanded Money into two real columns instead of a JSONB blob.")


if __name__ == "__main__":
    asyncio.run(main())
