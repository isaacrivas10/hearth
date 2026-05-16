# Plugin: hearth-auth

> **Source package**: `plugins/auth/hearth_auth/`
> **Install**: `pip install hearth[auth]`
> **Status**: v0.0.1 (first release; tracks the 2026-05-15 spec).

## What hearth-auth provides

Users with email + password authentication. Roles and Permissions for
role-based access control. ApiKeys for programmatic actors. Two enforcement
helpers (`require_permission`, `@requires`) that plugin authors use to gate
their actions. A `hearth auth bootstrap` CLI for first-admin setup.

The plugin's entities are the reference implementation of the kernel's
[Actor primitive](../primitives/actor.md): `User` and `ApiKey` are
*entity-actors* that satisfy the `Actor` Protocol. Plugins that want their
own actor-capable entities follow the same shape — see "Building your own
actor entity" below.

## SDK surface

```python
from hearth_auth import (
    # Entities (when you need to query/extend)
    User, Role, Permission, UserRole, RolePermission, ApiKey, ApiKeyPermission,
    # Values
    PermissionName, HashedSecret,
    # Enforcement (the 99% case)
    require_permission, requires, PermissionDenied, PermissionNotFound,
    # Authentication (transport layers, not plugin authors)
    AuthenticationFailed,
    # Composable helper for custom actor entities (rare)
    permission_grant_exists,
)
```

For test fixtures: `from hearth_auth.testing import seed_admin, grant_permissions`.

## Gating an action

Static permission requirement — declare and forget:

```python
from hearth import Action
from hearth_auth import requires

@requires("orders:read")
class GetOrders(Action):
    async def handle(self, uow, actor):
        return await uow.query(Order).all()
```

Dynamic permission requirement — call inline when the gate depends on data
the action loads:

```python
from hearth_auth import require_permission

class GetOrder(Action):
    order_id: EntityId

    async def handle(self, uow, actor):
        order = await uow.query(Order).where(Order.id == self.order_id).one()
        if order.customer_id != getattr(actor, "id", None):
            await require_permission(uow, actor, "orders:read_any")
        return order
```

Both helpers raise `PermissionDenied` when the actor lacks the permission.
The exception carries the actor and permission for logging.

## Permission resolution

Permissions are `<resource>:<action>` strings. A grant matches a request
when **both** components match independently — the grant's component
matches if it equals the request OR if it is the wildcard `*`. This means
all four shapes work:

| Grant | Examples it satisfies | Examples it does not satisfy |
|---|---|---|
| `("orders", "read")` exact | `orders:read` | `orders:write`, `users:read`, `*:*` |
| `("orders", "*")` resource wildcard | `orders:read`, `orders:write` | `users:read`, `*:*` |
| `("*", "read")` action wildcard | `orders:read`, `users:read` | `orders:write`, `*:*` |
| `("*", "*")` full wildcard | everything | — |

The wildcard is literal — `*` is not a pattern character, it is the
sentinel "match any". A grant of `("orders", "read")` does not satisfy a
`*:*` request: specific grants do not escalate to wildcard requests.

`PermissionName` rejects empty or whitespace-padded components at
construction (no `Permission(resource="", action="")` rows can exist), and
`permission_grant_exists` rejects malformed lookup strings (no colon,
multiple colons, empty segments) — both defenses combine so a malformed
input on either side fails closed.

## Authentication

`AuthenticateUser(email, password)` and `AuthenticateApiKey(key_string)`
are actions transport layers (HTTP, WhatsApp, CLI) call to convert
credentials into an actor. Both:

- Run argon2id verification on every call branch — even when the email
  is unknown or the key prefix matches no row. A module-level sentinel
  hash is used as the verify target when no candidate exists, equalizing
  response time. Lazily initialized to avoid the ~100ms argon2 cost on
  CLI startup.
- Raise a single `AuthenticationFailed("invalid credentials")` regardless
  of which check failed (no user, wrong password, disabled user). The
  message is intentionally indistinguishable.
- Return the authenticated entity on success. The entity is the actor for
  subsequent actions in the same request scope.

## Bootstrap

After `hearth db init` materializes the auth tables:

```bash
hearth auth bootstrap --email admin@example.com
# (password prompted interactively, confirmed twice)
```

Creates the first admin User, an `admin` Role, and the `*:*` wildcard
Permission, linked together. Refuses to run if the wildcard Permission row
already exists (read-side guard).

Two concurrent invocations cannot both succeed: the read-side guard and
the create happen in a single transaction, and a composite UNIQUE
constraint on `Permission(name_resource, name_action)` causes the loser to
fail with `hearth.IntegrityError` (the kernel wraps SA's IntegrityError
so the CLI can catch it without importing from `sqlalchemy`).

## Security guarantees

Each defense is implemented at the lowest layer that can enforce it.

| Guarantee | Where |
|---|---|
| Auth timing is constant across "user unknown / wrong password / disabled" branches | `AuthenticateUser`, `AuthenticateApiKey` (sentinel verify) |
| Same for API key prefix lookups | `AuthenticateApiKey` (always-loop, no early break) |
| Authentication errors are indistinguishable | single `AuthenticationFailed("invalid credentials")` |
| Disabled users (`is_active=False`) cannot pass `has_permission` even with valid grants | `User.has_permission` early-return |
| Revoked / expired ApiKeys deny everything | `ApiKey.has_permission` early-return |
| Empty / whitespace permission components cannot be persisted | `PermissionName` validator |
| Malformed permission lookup strings fail closed | `permission_grant_exists` (single-colon + non-empty segment check) |
| Concurrent bootstrap cannot duplicate admin/wildcard rows | unique constraint on `Permission(name_resource, name_action)` |
| Plugin actions cannot accidentally bypass enforcement by overriding `actor: Actor` | `Actor` Protocol typing (basedpyright strict) |

## What hearth-auth does NOT do (and why)

- **No sessions / tokens / JWT.** `AuthenticateUser` and `AuthenticateApiKey`
  return entities; the transport layer (HTTP, WhatsApp, CLI) owns session
  state. Different transports have different session-lifetime needs; the
  plugin stays generic.

- **No OAuth / OTP / magic-link / WebAuthn / MFA.** Email + password and
  long-lived API keys only. New mechanisms can be added as parallel
  `AuthenticateUserViaX` actions without changing the User entity.

- **No password reset flow.** No email sender; reset is admin-mediated via
  `ChangePassword`. A reset-token mechanism would need a transport.

- **No login throttling / brute-force protection.** Transport layer's
  concern — rate limits live at the network boundary, not in actions.

- **No plugin-declared permission seeding.** Permission rows are created
  via `CreatePermission`. Auto-seeding from plugin manifests waits for
  the migration system (see [migrations spec](../kernel/migrations.md)).

- **Disabling a User does not cascade-revoke their ApiKeys.** Operators
  must call `RevokeApiKey` for each owned key alongside `DisableUser`.
  Cascade would require a JOIN to `User.is_active` on every authz check;
  explicit revocation keeps the hot path cheap.

- **No partial-string matching beyond `*`.** Permission strings are
  literal — `orders:read*` is a literal `read*` action name, not a glob.
  Regex / glob support is a v2 question if a real use case appears.

## Building your own actor entity

A plugin that wants its own actor type (e.g., a `commerce.Customer` that
can place orders authenticated by phone + OTP) declares it as a normal
Entity with two additions:

```python
from typing import ClassVar
from hearth import Entity, UnitOfWork
from hearth_auth import Permission, permission_grant_exists

class Customer(Entity):
    actor_kind: ClassVar[str] = "customer"
    phone: PhoneNumber
    is_blocked: bool = False

    async def has_permission(self, uow: UnitOfWork, permission: str) -> bool:
        if self.is_blocked:
            return False
        # Customers grant themselves nothing by default — actions that need
        # auth use the User/ApiKey actors. If the plugin wants Customers to
        # hold grants, it joins through its own CustomerPermission table:
        grants = (
            uow.query(Permission)
            .join(CustomerPermission, CustomerPermission.permission_id == Permission.id)
            .where(CustomerPermission.customer_id == self.id)
        )
        return await permission_grant_exists(grants, permission)
```

The kernel's [`Actor`](../primitives/actor.md) Protocol is structural; any
entity that exposes `actor_kind` and `async has_permission` satisfies it.
`permission_grant_exists` is exported so custom actor entities reuse the
same wildcard semantics without re-implementing the predicate.

## Testing helpers

```python
from hearth_auth.testing import seed_admin, grant_permissions
```

`seed_admin(harness)` is the programmatic equivalent of `hearth auth
bootstrap`. `grant_permissions(harness, role_id, [PermissionName(...)])`
bulk-creates Permission rows and attaches them to a Role. Use both in
fixtures; the bootstrap CLI is for operators, not tests.
