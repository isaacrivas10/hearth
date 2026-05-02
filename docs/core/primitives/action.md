# Spec: `Action`

> **Source module**: `hearth/primitives/action.py`
> **Parent ADRs**: [ADR-0005 (primitives)](../../architecture/0005-primitives.md), [ADR-0007 (transactional contract)](../../architecture/0007-transactional-contract.md).

## Status

Stub. The first vertical slice (`IdentifyCustomer`) is the load-bearing
exercise of this primitive — the slice forces the declaration shape,
the handler signature, and the test-harness fixtures.

## Brief

A synchronous-from-the-caller's-perspective, transactional command that
mutates entities and emits events. Takes a command DTO (the input) and
a `UnitOfWork` (the transactional handle), and returns the events it
emitted (or a plugin-defined return value, with events emitted as a
side effect on the UoW). The only legitimate way for a plugin to change
state.

Actions run inside a database transaction managed by the kernel.
Authorization, audit, idempotency-key handling, transaction management
— all wrap actions uniformly. Handlers are `async def` per the
async-canonical decision (see [`../unit-of-work.md`](../unit-of-work.md)).

## Sketch

```python
from hearth import Action

class IdentifyCustomer(Action):
    phone: PhoneNumber

    async def handle(self, uow: UnitOfWork, identity: Identity) -> Customer:
        customer = await uow.find_one(Customer, phone=self.phone)
        if customer is None:
            customer = Customer(phone=self.phone)
            await uow.save(customer)
            uow.emit(CustomerIdentified(customer_id=customer.id, phone=self.phone))
        return customer
```

## Open questions

- **Declaration shape.** Subclass `Action` (sketched above) or `@action`
  decorator on a handler function with the command DTO as a separate
  class? Likely subclass for the slice; revisit once we feel ergonomics.
- **Return value.** Events list, plugin-defined value, or both?
- **Composition.** Actions invoking other actions: nested in the same
  UoW (current lean) vs. starting a fresh transaction.
- **Idempotency keys.** Kernel feature (decorator, kwarg) or plugin-
  author responsibility?
- **Sync handler escape valve** (`@sync_action` running in
  `asyncio.to_thread`): in the slice or deferred? Lean: deferred —
  add it only if real demand emerges.
- **Authorization seam.** How `Identity` flows into authz decisions
  (function arg, contextvar, middleware).
