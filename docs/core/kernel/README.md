# Kernel internals

Specs for `hearth/kernel/` — the runtime that makes the SDK's promises
work. **Plugins never import from here.** This directory is for the
kernel team's reference; the SDK is what plugins author against.

## Layout

- [`transaction.md`](transaction.md) — `UnitOfWork` implementation,
  SQLAlchemy session lifecycle.
- [`outbox.md`](outbox.md) — outbox table schema, dispatcher worker,
  retry semantics.
- [`registry.md`](registry.md) — plugin/entity/action discovery and
  startup validation.
- [`persistence.md`](persistence.md) — SQLAlchemy mapping layer that
  turns `Entity` declarations into ORM models.
- [`migrations.md`](migrations.md) — schema journal, plugin-aware
  migrations.

## Boundary rule

The kernel's public API surface is everything re-exported from
`hearth.*` (per [ADR-0004](../../architecture/0004-sdk-first.md)).
Anything in `hearth.kernel.*` is private to the kernel team, even
though Python doesn't enforce that. A lint rule (TBD) will reject any
plugin import of `hearth.kernel.*`.

If a plugin discovers it needs something only available in
`hearth.kernel.*`, the SDK is missing something — fix the SDK, do not
normalize the leak (per ADR-0004 §"The SDK is the only thing plugins
import from").
