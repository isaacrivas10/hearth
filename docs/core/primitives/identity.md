# Spec: `Identity`

> **Source module**: `hearth/primitives/identity.py`
> **Parent ADRs**: [ADR-0005 (primitives)](../../architecture/0005-primitives.md).

## Status

Stub. The Identity model in detail is an open decision in CLAUDE.md.
What is settled: there *is* a primitive for it, and plugins must accept
and propagate it.

## Brief

The actor performing an action. Threads through every action and event
for audit. Concrete subclasses include `User`, `ApiKey`, `System` (the
kernel itself), and `Plugin` (a plugin acting on its own behalf, e.g., a
scheduled job).

Plugins receive an `Identity` in every action call and use it for
authorization decisions and audit annotations.

## Sketch

```python
from hearth import Identity

class User(Identity):
    user_id: EntityId
    # roles, permissions: TBD

class ApiKey(Identity):
    key_id: EntityId

class System(Identity): ...

class Plugin(Identity):
    plugin_name: str
```

## Open questions

- **Auth model.** Where do users live (kernel, plugin, integration)?
  This is the bigger open ADR mentioned in CLAUDE.md.
- **Permission/role system.** Kernel-shipped, plugin-shipped, or absent?
- **Async propagation.** How identity threads across async boundaries
  (contextvars vs. explicit parameter).
- **Identity propagation across `Job` invocations** triggered by events
  — the originating actor should flow into derived work for audit.
- **Serialization of `Identity` for outbox rows** (per ADR-0007's
  outbox row schema).
