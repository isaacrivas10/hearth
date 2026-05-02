# Spec: `View`

> **Source module**: `hearth/primitives/view.py`
> **Parent ADRs**: [ADR-0005 (primitives)](../../architecture/0005-primitives.md), [ADR-0006 (ORM contract)](../../architecture/0006-orm-contract.md).

## Status

Stub. Views are the read-side seam between entities and presentation;
the slice is too small to exercise them, but the spec needs to exist
because [ADR-0006](../../architecture/0006-orm-contract.md) §"Read paths"
forbids ad-hoc entity queries — views are where general reading goes.

## Brief

Read-side projections. Plugins define views to expose data for queries
and presentation. Views are decoupled from entities — they may
denormalize, aggregate, join across plugins, or be backed by caches.

A simple plugin may have views that are thin wrappers over entity rows.
A complex plugin may have views backed by event-driven projections
updated by jobs. Both are first-class.

Presentation-layer plugins (storefronts, WhatsApp formatters, POS
terminals) consume views; they do not query entities directly.

## Sketch

```python
from hearth import View

class CustomerSummary(View, plugin="commerce"):
    customer_id: EntityId
    phone: PhoneNumber
    total_orders: int
    last_order_at: datetime | None

    @classmethod
    async def query(cls, uow: ReadOnlyUnitOfWork, customer_id: EntityId) -> "CustomerSummary":
        ...
```

(Declaration shape — class with classmethod, function with decorator,
SQL-like DSL — to be confirmed once a presentation plugin needs it.)

## Open questions

- **Declaration shape.** Class with `query` classmethod, or `@view` on a
  function?
- **Storage backing.** Live query, materialized table, or event-driven
  projection (fed by a job)?
- **Read transaction.** Separate `ReadOnlyUnitOfWork`, or reuse
  `UnitOfWork` with a read-only flag?
- **Cross-plugin views.** A view in plugin B that reads from plugin A's
  entities — allowed? coupled?
- **Pagination, filtering, sorting.** Kernel utility or per-view?
- **Cache invalidation strategy** if backed by a cache.
