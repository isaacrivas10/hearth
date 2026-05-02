# Spec: `Job`

> **Source module**: `hearth/primitives/job.py`
> **Parent ADRs**: [ADR-0005 (primitives)](../../architecture/0005-primitives.md), [ADR-0007 (transactional contract)](../../architecture/0007-transactional-contract.md).

## Status

Stub. The first concrete job will be the kernel's outbox dispatcher
itself ([`../kernel/outbox.md`](../kernel/outbox.md)).

## Brief

Deferred or scheduled work. Idempotent by contract. Examples:
`SendWhatsAppConfirmation`, `ReconcileBankTransfers`, `SyncInventory`.
The kernel's outbox dispatcher is itself a `Job`.

Jobs run in a worker process, not in the request handler. Their inputs
are values (not live entity references) so they can be serialized to a
queue. The kernel guarantees at-least-once execution; idempotency is
the plugin author's responsibility.

## Sketch

```python
from hearth import Job

class SendWhatsAppConfirmation(Job, plugin="whatsapp"):
    customer_id: EntityId
    order_id: EntityId

    async def run(self, uow: UnitOfWork, identity: Identity) -> None:
        ...
```

## Open questions

- **Declaration shape.** Subclass (sketched) or `@job` decorator?
- **Scheduling.** Cron-like in-kernel scheduler, OS-level (cron /
  systemd), or both?
- **Backend.** In-process worker vs. separate worker process. Current
  lean per ADR-0007: separate worker.
- **Retry/backoff strategy.** Kernel default with plugin override?
- **Dead-letter handling.** Parking row in DB, separate table, alerting
  hook?
- **Identity propagation.** How a job triggered by an event inherits the
  causation's identity.
