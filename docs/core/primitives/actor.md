# Spec: Actor primitive

> **Source module**: `hearth/primitives/actor.py`
> **Parent ADR**: [ADR-0005 (primitives)](../../architecture/0005-primitives.md) with 2026-05-15 amendment.

## What an Actor is

The "who" of an Action — the entity or kernel-shipped identity threading
through every Action call for audit and authorization. Two kinds:

1. **Entity actors**: any `Entity` subclass that declares
   `actor_kind: ClassVar[str]` and implements
   `async def has_permission(uow, permission: str) -> bool`. Plugin-contributed
   (e.g., hearth-auth's `User` and `ApiKey`).

2. **Dataless actors**: kernel-shipped types for callers with no backing row.
   `System`, `Anonymous`, `PluginActor`. Frozen dataclasses; satisfy the
   Protocol directly.

Both shapes satisfy the `Actor` Protocol. The kernel uses the Protocol type
as the parameter to `Action.handle(uow, actor)` and as the input to outbox
audit serialization.

## Protocol surface

```python
@runtime_checkable
class Actor(Protocol):
    actor_kind: ClassVar[str]

    async def has_permission(self, uow: UnitOfWork, permission: str) -> bool:
        ...
```

**`actor_kind` convention**: snake_case, lowercase. The string is the wire
discriminator for outbox audit JSON; choose carefully — once shipped it
shouldn't be renamed.

**`has_permission`**: permission strings follow `<resource>:<action>`.
Resolution semantics are the actor's responsibility. The kernel does not
know about Role, Permission, or any plugin's authz schema.

## Kernel-shipped actors

| Type | `actor_kind` | `has_permission` | Notes |
|---|---|---|---|
| `System()` | `"system"` | always `True` | Kernel-internal callers; trusted. |
| `Anonymous()` | `"anonymous"` | always `False` | Public, unauthenticated. |
| `PluginActor(alias)` | `"plugin"` | always `True` | Plugin cron/bootstrap; trusted. |

## Outbox serialization

The outbox `actor` JSONB column stores:

```json
{"kind": "<actor_kind>", "id": "<entity id or null>", "meta": {...}}
```

`meta` is type-specific. For `PluginActor`, `meta` contains `{"alias": "..."}`.
For entity actors and kernel dataless actors, `meta` is `{}` by default.
The kernel writes this JSON; it does not reconstruct typed actors from
outbox rows. Audit consumers read the JSON directly.

## What the kernel does NOT know

- What permission strings mean.
- What concrete actor types exist beyond its three dataless ones.
- How any entity-actor resolves its permissions.

The boundary is intentional: plugins own the actor-capable entities and the
authz schema. The kernel only knows about *actorship as a capability*.
