# Spec: `References` and the `Reference` value type

> **Source module**: `hearth/references.py`
> **Parent ADRs**: [ADR-0006 (ORM contract)](../architecture/0006-orm-contract.md), [ADR-0008 (built-in plugins)](../architecture/0008-built-in-plugins.md).

## Status

Stub. The `References()` field declaration is shown in
[`primitives/entity.md`](primitives/entity.md); the underlying machinery
is specified here.

## Brief

Cross-plugin foreign-key references. Plugins declare references between
entities (same or different plugin) using
`References(EntityClass, on_delete=...)`. The kernel manages FK
integrity, lifecycle propagation (`restrict` / `cascade` / `set_null`),
and migration ordering.

`Reference` is also the kernel-shipped Value (per the
[ADR-0005 amendment](../architecture/0005-primitives.md#amendment-2026-05-02--value-kernel-ships-only-the-contract))
— the value type the kernel manipulates for FK integrity.

## Open questions

- **Runtime form.** Lazy-loaded entity, eager-loaded entity, or explicit
  `Reference[T]` value that requires `await uow.resolve(ref)` to
  materialize? ADR-0005 lists `Reference` as a Value (suggests an
  explicit value type), but the example in
  [`primitives/entity.md`](primitives/entity.md) annotates the field as
  the entity class itself. The slice will pick.
- **`on_delete` semantics across the outbox.** Should a cascade delete
  emit `EntityDeleted` events for each cascaded entity, or one event
  per cascade root?
- **Forward-reference parsing** for the string form
  (`References("Customer")`).
- **N+1 prevention.** Prefetch hints, batch loaders.
