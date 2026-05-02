# Spec: `Event`

> **Source module**: `hearth/primitives/event.py`
> **Parent ADRs**: [ADR-0005 (primitives)](../../architecture/0005-primitives.md), [ADR-0007 (transactional contract)](../../architecture/0007-transactional-contract.md).

## Status

Stub. To be filled when the slice forces decisions.

## Brief

Immutable record of something that happened. Append-only. Has a
timestamp, an actor (`Identity`), a causation chain, and a payload.
Persisted to the outbox in the same transaction as the action that
emitted it; dispatched to subscribers post-commit with at-least-once
delivery (ADR-0007).

Plugins emit events from inside actions. Events are never edited or
deleted. Mistakes are corrected by emitting compensating events
(`OrderCancelled`, `PaymentReversed`) — never by editing history.

Plugins subscribe to events via registered subscribers. Subscribers
must be idempotent.

## Sketch

```python
from hearth import Event

class CustomerIdentified(Event, plugin="commerce"):
    customer_id: EntityId
    phone: PhoneNumber
```

(Declaration shape — subclass with `dataclass_transform` vs
`@event(...)` decorator vs `@dataclass`-aware base — to be confirmed in
the slice.)

## Open questions

- Declaration shape: subclass with `dataclass_transform`, or `@event`
  decorator?
- Causation / correlation IDs: kernel-managed, plugin-supplied, or both?
- Subscriber registration: decorator on a function
  (`@subscribes_to(CustomerIdentified)`) or `Subscriber` subclass?
- Outbox row schema (kernel-managed; see ADR-0007 §"Implementation
  sketch"). Detailed in [`../kernel/outbox.md`](../kernel/outbox.md).
- Replay semantics: filter by event type? by causation root?
- Event payload size limits and policy.
