# Spec: `UnitOfWork`

> **Source module**: `hearth/unit_of_work.py`
> **Parent ADRs**: [ADR-0006 (ORM contract)](../architecture/0006-orm-contract.md), [ADR-0007 (transactional contract)](../architecture/0007-transactional-contract.md).

## Status

Stub. The `UnitOfWork` is the transactional handle plugins receive in
actions. The slice will fully specify it.

## Brief

The plugin-facing handle for entity persistence and event emission
within a transaction. The kernel passes a UoW into every action;
plugins do all of their reads, writes, and event emissions through it.

UoW methods are async (per the async-canonical decision); event
emission is sync because it just appends to an in-transaction buffer.

The kernel-internal implementation lives in
[`kernel/transaction.md`](kernel/transaction.md). This file specifies
only the public Protocol that plugins author against.

## Sketch

```python
from hearth import UnitOfWork

# Inside an Action.handle:
customer = await uow.get(Customer, customer_id)              # raises if missing
maybe = await uow.find_one(Customer, phone=phone)            # Customer | None
await uow.save(customer)                                     # insert or update
await uow.delete(customer)
uow.emit(CustomerIdentified(customer_id=customer.id, ...))   # sync; buffered until commit
```

## Open questions

- **Filter language for `find_one` / `find`.** Kwargs only, expression
  builder, query objects.
- **Bulk operations.** `await uow.save_all([...])` for batch inserts.
- **Transaction nesting.** Actions invoking other actions inside the
  same UoW vs. starting a fresh transaction.
- **Read-only UoW for views** — see [`primitives/view.md`](primitives/view.md).
- **Test-harness parity.** How the in-memory test harness implements
  the same Protocol with full ordering/visibility honesty (see
  [`testing/harness.md`](testing/harness.md)).
- **Identity-map scope.** Per-action vs. per-action-tree.
