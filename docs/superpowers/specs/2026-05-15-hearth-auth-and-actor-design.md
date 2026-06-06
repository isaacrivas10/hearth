# Spec: `Actor` primitive, `hearth-auth` plugin, plugin CLI extension

> **Date**: 2026-05-15
> **Status**: Approved (brainstorming complete; implementation plan next)
> **Parent ADRs**: [ADR-0005 (primitives)](../../architecture/0005-primitives.md), [ADR-0008 (built-in plugins)](../../architecture/0008-built-in-plugins.md).
> **Amends**: ADR-0005 (rename `Identity` → `Actor`; relocate concrete actor types to plugins); [2026-05-14-registry-cli-design.md](2026-05-14-registry-cli-design.md) — Section "Detailed design / `db init`" (previously kernel-only) is amended so `hearth db init` calls `Registry.build()` and creates plugin tables along with the outbox. This is a pre-migrations convenience that goes away when migrations land; once we have migrations, `db init` will return to kernel-only and per-plugin migrations will own plugin schemas.
> **Companion**: [2026-05-14-registry-cli-design.md](2026-05-14-registry-cli-design.md) (registry + CLI are prerequisites for this work).

## Purpose

Replace the half-built `Identity` primitive with a coherent `Actor` model, and land the first real-world plugin (`hearth-auth`) that ships User accounts, ApiKeys, and an RBAC story. As a side effect, give plugins a clean way to extend the `hearth` CLI.

This spec covers three interlocking pieces of work, presented as one design because they're a single coherent feature:

1. **Kernel `Actor` refactor** — rename the primitive, switch from a closed `BaseModel` hierarchy to a Protocol, ship a small set of dataless actors in the kernel.
2. **`hearth-auth` plugin** — Users, Roles, Permissions, ApiKeys; password hashing; RBAC enforcement helpers; the bootstrap CLI command.
3. **Plugin CLI extension** — discovery of `[project.entry-points."hearth.cli"]` so plugins (including `hearth-auth`) can contribute subcommand groups to the `hearth` CLI.

## Why this matters

The existing `Identity` primitive is 21 lines of half-finished sketch: a `kind: str` discriminator and two near-empty subclasses (`User`, `System`). Every meaningful operation on it ("is this user an admin?", "what's their email?") requires reading a backing entity. **The primitive duplicates the shape of an entity reference and adds zero on top.**

The right shape is the inverse: most actors *are* entities, plus a small set of dataless cases (System, PluginActor, Anonymous) the kernel ships. With this collapse:

- Plugins extend the actor set by declaring actor-capable entities, no kernel changes needed.
- Resolution semantics (permission checks, etc.) live on the actor itself, not in a kernel switch statement.
- The kernel stays domain-agnostic — it knows about *actorship* as a capability, not about *user accounts* as a concept.

`hearth-auth` is then the first concrete consumer: it ships `User` and `ApiKey` entities that satisfy the `Actor` Protocol, and adds the RBAC layer on top. Real-world Hearth deployments need real-world auth before they can ship features.

## Scope

In:

- Rename `hearth/primitives/identity.py` → `hearth/primitives/actor.py`. Replace `Identity` BaseModel hierarchy with `Actor` Protocol. Ship kernel-only dataless actors: `System`, `Anonymous`, `PluginActor`.
- Update kernel call sites (Actions, outbox/audit serialization, harness) to use `Actor` instead of `Identity`.
- Delete the placeholder `commerce` plugin (it was a slice scaffold; superseded).
- Add `_register_plugin_cli_groups()` to `hearth/cli/__init__.py` for plugin CLI discovery via `hearth.cli` entry-point group.
- New plugin `hearth-auth` (package `hearth_auth`) at `plugins/auth/`, registered as a `hearth.plugins` entry point and a `hearth.cli` entry point.
- Auth plugin ships: 7 entities, 2 values, 15 Actions + corresponding events, 2 enforcement helpers, 1 CLI command, test helpers.
- ADR-0005 amendment documenting the rename and the actors-are-entities model.
- New primitive spec at `docs/core/primitives/actor.md`.
- New core doc at `docs/core/plugins/auth.md` describing the plugin's contract for SDK consumers.

Out — explicitly deferred to v1.1+ or out of scope entirely:

- **Plugin-declared permission seeding.** Plugins do not ship a `PERMISSIONS` constant or any auto-registration. Permission rows are created via Actions, by humans. The seeding system is migration-shaped and waits for migrations.
- **Sessions, tokens, JWT.** The auth plugin returns `User` / `ApiKey` entities from its Authenticate actions; transport layers (HTTP, WhatsApp, CLI) own request-scoped session state. The plugin is stateless.
- **Multi-mechanism authentication.** No OAuth, OTP, magic-link, WebAuthn, MFA in v1. Email + password only. New mechanisms can be added later by introducing parallel credential Values and `AuthenticateUserViaX` actions; nothing in v1 blocks them.
- **Bots as a distinct actor type.** Bots can be modeled as ApiKeys with appropriate permissions. If Bot accumulates Bot-specific features later it can graduate to its own entity.
- **Per-resource roles.** No "admin-of-store-X" granularity. Roles are global. Hearth is single-tenant.
- **Authorization audit log.** No record of which `(role, permission)` combo authorized a given action.
- **Password reset flow.** No email sender yet; reset is admin-mediated via `ChangePassword`.
- **Admin web UI.** Presentation plugin's job.
- **Login throttling / brute-force protection.** Transport layer's concern for v1.
- **CLI subcommands beyond `bootstrap`.** `hearth auth user list`, `permission list`, etc. — Actions cover the operations; CLI sugar can come later.

## Background: the half-finished Identity primitive

Today's `hearth/primitives/identity.py`:

```python
class Identity(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    kind: str

class User(Identity):
    kind: Literal["user"] = "user"
    user_id: str

class System(Identity):
    kind: Literal["system"] = "system"
```

ADR-0005 lists "concrete subclasses include `User`, `ApiKey`, `System`, and `Plugin`" — three of the four are unimplemented. The implementation that does exist (`User(Identity)`) is a thin pointer: a discriminator plus a string ID that always resolves to a row in some plugin. The primitive does no work of its own.

The deeper architectural issue: anything meaningful you'd want to ask about an actor (permissions, ownership, status, name) requires reading that backing row. So the "Identity primitive" is really just "an Entity reference, dressed up." That's not pulling its weight as a primitive.

## Design

### Section 1: `Actor` Protocol (kernel)

**File**: `hearth/primitives/actor.py` (renamed from `identity.py`).

```python
from typing import ClassVar, Protocol

from hearth.unit_of_work import UnitOfWork


class Actor(Protocol):
    """The 'who' of an Action. Threads through actions for audit + authz.

    Concrete actors are either entities (User, ApiKey, etc., contributed
    by plugins) or kernel-shipped dataless types (System, Anonymous,
    PluginActor) for callers that have no backing row.
    """

    actor_kind: ClassVar[str]
    # Convention: snake_case, lowercase. Single words for the kernel types
    # (`"system"`, `"anonymous"`, `"plugin"`) and multi-word actor types
    # (`"api_key"`). The string is the wire-level discriminator for outbox
    # audit JSON; choose carefully — once shipped it shouldn't be renamed.

    async def has_permission(self, uow: UnitOfWork, permission: str) -> bool:
        """Return True if this actor holds the given permission string.

        Permission strings follow the `<resource>:<action>` convention
        (`orders:read`, `users:write`). Resolution semantics are the
        actor's responsibility — the kernel knows nothing about how
        permissions are stored or granted.
        """
        ...
```

**Kernel-shipped concrete actors** (dataless, satisfy the Protocol directly):

```python
class System:
    """The kernel itself. Outbox dispatch, bootstrap, schema operations."""
    actor_kind: ClassVar[str] = "system"

    async def has_permission(self, uow: UnitOfWork, permission: str) -> bool:
        return True  # kernel-internal callers are trusted


class Anonymous:
    """Unauthenticated public access. Used by transport layers for endpoints
    that explicitly allow no auth (e.g., a public health check)."""
    actor_kind: ClassVar[str] = "anonymous"

    async def has_permission(self, uow: UnitOfWork, permission: str) -> bool:
        return False


@dataclass(frozen=True)
class PluginActor:
    """A plugin acting on its own behalf — e.g., a scheduled cron from
    the commerce plugin running nightly invoice generation."""
    actor_kind: ClassVar[str] = "plugin"
    alias: str

    async def has_permission(self, uow: UnitOfWork, permission: str) -> bool:
        return True  # plugin code is trusted; permission checks are for external callers
```

**Plugin-contributed concrete actors** are Entities that satisfy the Protocol. The auth plugin's `User` and `ApiKey` declare `actor_kind: ClassVar[str]` and implement `async def has_permission(...)`; the kernel never imports them.

**Delegation** (`on_behalf_of`) is deferred. The Protocol shape doesn't preclude adding it later as an optional field on individual actor types.

### Section 2: `Action.handle` signature change

Today:
```python
async def handle(self, uow: UnitOfWork, identity: Identity) -> ...
```

After refactor:
```python
async def handle(self, uow: UnitOfWork, actor: Actor) -> ...
```

The rename is mechanical. Every site that constructs an `Identity.User(user_id=...)` either constructs a `User` Entity (and reads it from the auth plugin's UoW) or uses one of the new kernel actors.

The placeholder commerce plugin is deleted as part of this work — it uses the old `Identity` types and was always a slice scaffold, never real commerce.

### Section 3: `hearth-auth` entities (7) and values (2)

**Values**:

```python
class PermissionName(Value):
    """The (resource, action) pair. Two-column composite when persisted."""
    resource: str
    action: str

class HashedSecret(Value):
    """An argon2id hash. Used by User.password and ApiKey.key_hash.

    Hashing and verification are CPU-bound (~50–100ms per call by design).
    Both methods MUST be invoked via `asyncio.to_thread(...)` to avoid
    blocking the event loop:

        hashed = await asyncio.to_thread(HashedSecret.from_plaintext, plaintext)
        ok = await asyncio.to_thread(hashed.verify, candidate)

    The plugin's Action implementations use this pattern; plugin authors
    calling these methods directly are expected to do the same.
    """
    hash: str

    @classmethod
    def from_plaintext(cls, plaintext: str | SecretStr) -> "HashedSecret":
        """Hash with argon2id and return a HashedSecret value."""
        ...

    def verify(self, plaintext: str | SecretStr) -> bool:
        """True if plaintext hashes to self.hash; False otherwise."""
        ...
```

The plugin ships one module-level sentinel hash used to equalize timing on lookup-miss paths (see Section 6).

Both live in `hearth_auth` — they're auth-plugin-specific. (ADR-0008 test: only auth touches them; not commons-eligible.)

**Entities**:

```python
class User(Entity):
    actor_kind: ClassVar[str] = "user"
    email: EmailAddress                    # commons value
    password: HashedSecret                 # composite, one column
    display_name: str | None
    is_active: bool = True
    last_login_at: datetime | None = None

    async def has_permission(self, uow: UnitOfWork, permission: str) -> bool:
        # JOIN UserRole → RolePermission → Permission, check (resource, action)
        ...

class Role(Entity):
    name: str                              # unique
    description: str | None

class Permission(Entity):
    name: PermissionName                   # composite → (resource, action) columns; unique
    description: str | None

class UserRole(Entity):                    # join: User ↔ Role
    user_id: EntityId = ForeignKey()
    user: User = References(User)
    role_id: EntityId = ForeignKey()
    role: Role = References(Role)
    granted_at: datetime = Field(auto_now_add=True)
    granted_by_id: EntityId | None         # nullable for bootstrap
    granted_by: User | None = References(User)

class RolePermission(Entity):              # join: Role ↔ Permission
    role_id: EntityId = ForeignKey()
    role: Role = References(Role)
    permission_id: EntityId = ForeignKey()
    permission: Permission = References(Permission)

class ApiKey(Entity):
    actor_kind: ClassVar[str] = "api_key"
    name: str                              # human-readable label
    key_prefix: str                        # first ~8 chars for display
    key_hash: HashedSecret                 # composite, one column
    owner_id: EntityId | None              # FK to User; nullable for system keys
    owner: User | None = References(User)
    expires_at: datetime | None
    revoked_at: datetime | None

    async def has_permission(self, uow: UnitOfWork, permission: str) -> bool:
        if self.revoked_at or (self.expires_at and self.expires_at < now()):
            return False
        # JOIN ApiKeyPermission → Permission, check (resource, action)
        ...

class ApiKeyPermission(Entity):            # direct: ApiKey ↔ Permission
    api_key_id: EntityId = ForeignKey()
    api_key: ApiKey = References(ApiKey)
    permission_id: EntityId = ForeignKey()
    permission: Permission = References(Permission)
```

Conventional table names from `_hearth_plugin` + class: `auth__user`, `auth__role`, `auth__permission`, `auth__user_role`, `auth__role_permission`, `auth__api_key`, `auth__api_key_permission`.

### Section 4: Permission strings + wildcard

Permission identifiers are strings following `<resource>:<action>` convention. The Permission entity stores them as `PermissionName(resource, action)`; the Actor protocol takes them as strings; the helpers parse `":"` to bridge.

**Wildcard semantics in v1**:

- A single wildcard form is supported: the literal string `"*:*"` (resource `"*"`, action `"*"`), backed by a Permission row with `PermissionName(resource="*", action="*")`.
- Both `User.has_permission` and `ApiKey.has_permission` check for the `*:*` permission first and short-circuit to True if the actor holds it. The check is uniform across actor types.
- **Partial wildcards are NOT supported in v1** — `"users:*"`, `"*:read"`, and similar do not match anything. If a use case emerges later, a v1.1+ change can introduce LIKE-style matching with documented semantics.
- The bootstrap admin holds `*:*` by default (see Section 8). This is the only out-of-the-box way to get a working admin without enumerating every permission.

No registry of valid permission strings, no startup-time typo detection. The cost of `@requires("oders:read")` typos is paid at runtime when no one can perform the action — same cost as in any string-based RBAC system. Plugin-declared registration is deferred to v1.1+.

### Section 5: Enforcement helpers

Two layered tools, both shipped by `hearth_auth`:

```python
# hearth_auth/helpers.py

class PermissionDenied(Exception):
    def __init__(self, actor: Actor, permission: str) -> None:
        self.actor = actor
        self.permission = permission
        super().__init__(f"actor {actor.actor_kind} denied permission {permission!r}")


async def require_permission(uow: UnitOfWork, actor: Actor, permission: str) -> None:
    """Raise PermissionDenied if actor lacks the permission."""
    if not await actor.has_permission(uow, permission):
        raise PermissionDenied(actor, permission)


def requires(*permissions: str) -> Callable[[type[Action]], type[Action]]:
    """Decorator: gate an Action class on static permissions.

    Wraps the action's `handle` so that all listed permissions are checked
    before the body runs. For dynamic checks (e.g., row-level ownership),
    call `require_permission` inside the body instead.

    LIMITATION: the decorator wraps the class's `handle` at decoration time.
    A subclass that overrides `handle` REPLACES the wrapped version and
    bypasses the gate silently. Plugin authors who subclass a `@requires`-
    decorated Action MUST reapply `@requires(...)` on the subclass.
    The decorator also sets a `_hearth_required_permissions` ClassVar on
    the class so test helpers and admin tools can introspect what a given
    action requires without executing it.
    """
    def wrap(cls: type[Action]) -> type[Action]:
        cls._hearth_required_permissions = permissions
        original_handle = cls.handle
        async def handle(self, uow, actor):
            for p in permissions:
                await require_permission(uow, actor, p)
            return await original_handle(self, uow, actor)
        cls.handle = handle
        return cls
    return wrap
```

Usage:

```python
from hearth_auth import requires, require_permission

# Static gate
@requires("orders:read")
class GetOrders(Action):
    async def handle(self, uow, actor):
        return await uow.query(Order).all()

# Dynamic check
class GetOrder(Action):
    order_id: EntityId

    async def handle(self, uow, actor):
        order = await uow.query(Order).where(Order.id == self.order_id).one()
        if order.customer_id != actor.id:
            await require_permission(uow, actor, "orders:read_any")
        return order
```

**The kernel has zero permission code.** Both helpers are plugin-shipped; both consult the Actor protocol method.

### Section 6: Authentication flow

**Timing-constant flow.** Both AuthenticateUser and AuthenticateApiKey use a module-level sentinel `HashedSecret` and always invoke `.verify(...)` exactly once before deciding the outcome, regardless of whether the lookup succeeded. This prevents timing-based email/key enumeration: an attacker measuring response time cannot distinguish "no such email" from "wrong password." Argon2id verification (~50–100ms) dominates the response time; the lookup query is fast in comparison.

**User**: `AuthenticateUser(email, password)` action.
1. Look up `User` row by email; if not found, hold a `None` reference.
2. `verify_target = user.password if user else SENTINEL_HASHED_SECRET`.
3. `ok = await asyncio.to_thread(verify_target.verify, plaintext)`.
4. If `user is None` or `not ok` or `not user.is_active`, raise `AuthenticationFailed("invalid credentials")` — same message for all three branches.
5. Update `user.last_login_at = now()`.
6. Emit `UserAuthenticated(user_id=user.id)` event.
7. Return the User entity.

**ApiKey**: `AuthenticateApiKey(key_string)` action.
1. Parse the prefix (first ~8 chars) from the key_string.
2. Look up `ApiKey` rows by prefix; if none, hold an empty list.
3. For each candidate (or the sentinel if none): `ok = await asyncio.to_thread(candidate.key_hash.verify, key_string)`. Track the first matching candidate. ALWAYS run verify at least once, against the sentinel if necessary.
4. If no match, or if matched row is `revoked_at is not None` or expired, raise `AuthenticationFailed("invalid api key")`.
5. Emit `ApiKeyAuthenticated(api_key_id=...)` event.
6. Return the ApiKey entity.

**Policy: `is_active` is an authentication gate, not a per-action gate.** After authentication succeeds, the User's permissions stand until the session expires and the transport layer re-authenticates. Disabling a user via `DisableUser` prevents new logins; it does NOT immediately revoke active sessions. Real-time session invalidation is the transport layer's responsibility (e.g., a session store consulted on each request). The plugin documents this explicitly so operators don't assume `DisableUser` is a kill-switch.

**No session creation.** The transport layer that called these actions decides whether to set a cookie, return a JWT, or store anything at all.

### Section 7: Actions shipped (15)

Each Action emits a corresponding Event on success.

```python
# User lifecycle
class CreateUser(Action):
    email: EmailAddress
    password: SecretStr                    # plaintext, hashed before persist
    display_name: str | None = None
    # → User; emits UserCreated(user_id, email)

class ChangePassword(Action):
    user_id: EntityId
    old_password: SecretStr
    new_password: SecretStr
    # → None; emits PasswordChanged(user_id)

class DisableUser(Action):
    user_id: EntityId
    # → None; emits UserDisabled(user_id)

class EnableUser(Action):
    user_id: EntityId
    # → None; emits UserEnabled(user_id)

# Authentication
class AuthenticateUser(Action):
    email: EmailAddress
    password: SecretStr
    # → User; emits UserAuthenticated(user_id)
    # raises AuthenticationFailed on bad credentials

class AuthenticateApiKey(Action):
    key_string: SecretStr
    # → ApiKey; emits ApiKeyAuthenticated(api_key_id)
    # raises AuthenticationFailed on bad/revoked/expired key

# Roles and permissions
class CreateRole(Action):
    name: str
    description: str | None = None
    # → Role; emits RoleCreated(role_id, name)

class CreatePermission(Action):
    name: PermissionName
    description: str | None = None
    # → Permission; emits PermissionCreated(permission_id, name)

class AssignRoleToUser(Action):
    user_id: EntityId
    role_id: EntityId
    # → UserRole; emits RoleAssignedToUser(user_id, role_id, granted_by_id)

class RevokeRoleFromUser(Action):
    user_id: EntityId
    role_id: EntityId
    # → None; emits RoleRevokedFromUser(user_id, role_id)

class AssignPermissionToRole(Action):
    role_id: EntityId
    permission_id: EntityId
    # → RolePermission; emits PermissionAssignedToRole(role_id, permission_id)

class RevokePermissionFromRole(Action):
    role_id: EntityId
    permission_id: EntityId
    # → None; emits PermissionRevokedFromRole(role_id, permission_id)

# ApiKey lifecycle
class CreateApiKey(Action):
    name: str
    permissions: list[PermissionName]     # MUST already exist in the Permission table
    owner_id: EntityId | None = None
    expires_at: datetime | None = None
    # → (ApiKey, plaintext_key: str)       # plaintext returned exactly once
    # emits ApiKeyCreated(api_key_id, key_prefix, owner_id)
    # On unknown PermissionName: raises PermissionNotFound (does NOT auto-create).
    # Rationale: auto-create would mask typos; explicit lookup forces operators
    # to create permissions via CreatePermission first.

class RevokeApiKey(Action):
    api_key_id: EntityId
    # → None; emits ApiKeyRevoked(api_key_id)
```

### Section 8: First-user bootstrap via CLI

```bash
hearth auth bootstrap --email admin@example.com
# (password prompted interactively, confirmed twice, hidden input)
```

Behavior:
1. **Refuses to run if the `*:*` wildcard Permission row already exists.** Exits 1 with `ERROR: hearth-auth is already bootstrapped (wildcard permission exists). Manage users via the Action surface.` The check is on the wildcard Permission row, not on any User row — this is intentional. The wildcard is the unique artifact bootstrap creates; checking for it lets `CreateUser` run normally in dev/test before bootstrap (the user can still rerun bootstrap as long as nobody created the wildcard manually first).
2. Creates the first `User` with the given credentials.
3. Creates a built-in `Role(name="admin", description="Bootstrapped administrator role.")`.
4. Creates a `Permission(name=PermissionName(resource="*", action="*"), description="Wildcard — grants all permissions.")`.
5. Inserts `RolePermission` linking admin → wildcard.
6. Inserts `UserRole` linking the user → admin (with `granted_by_id=None`).
7. Echoes the user's id, email, and role assignments. Does NOT echo the password.

All steps run in a single transaction. Failure rolls back fully. Remediation if bootstrap fails partway and the wildcard Permission row exists but the User does not: delete the wildcard Permission row and the admin Role, then rerun.

### Section 9: Plugin CLI extension (kernel addition)

A new entry-point group `hearth.cli` complements the existing `hearth.plugins` group.

```toml
# plugins/auth/pyproject.toml
[project.entry-points."hearth.plugins"]
auth = "hearth_auth"

[project.entry-points."hearth.cli"]
auth = "hearth_auth.cli:auth_app"
```

In `hearth/cli/__init__.py`, after the built-in `plugins`/`db` groups are registered, the CLI scans the `hearth.cli` group:

```python
_RESERVED_CLI_NAMES = frozenset({"plugins", "db"})

def _register_plugin_cli_groups() -> None:
    """Discover and register Typer subcommand groups contributed by plugins."""
    for ep in importlib.metadata.entry_points(group="hearth.cli"):
        if ep.name in _RESERVED_CLI_NAMES:
            typer.echo(
                f"WARNING: plugin CLI name {ep.name!r} is reserved; skipping",
                err=True,
            )
            continue
        try:
            plugin_app = ep.load()
        except Exception as exc:
            typer.echo(
                f"WARNING: failed to load CLI from plugin {ep.name!r}: "
                f"{type(exc).__name__}: {exc}",
                err=True,
            )
            continue
        app.add_typer(plugin_app, name=ep.name)

_register_plugin_cli_groups()
```

Properties:
- **Independent of `Registry.build()`.** Plugin CLI loads via its own entry-point group; CLI is available even if the plugin registry has problems, which is exactly when you'd reach for CLI diagnostics.
- **Tolerant of failures.** A broken plugin's CLI failing to load logs a warning but doesn't crash the rest. Operators can still run `hearth db status` to diagnose.
- **Reserved names.** `plugins` and `db` are kernel-owned. Plugins using these names get a warning and their CLI is skipped.
- **First-wins on collision.** If two plugins claim the same CLI name, the first registered wins; the second logs a warning. Sufficient for v1; can escalate to hard error later if it becomes common.

**Test isolation**: `_register_plugin_cli_groups()` runs at module import time, the same as the existing `hearth.plugins` discovery in the registry-cli spec. Tests follow the same pattern: monkeypatch `importlib.metadata.entry_points` to return a controlled list before invoking the CLI. `ep.load()` imports the plugin module, which can leak entries into `Entity.__subclasses__()` — tests that mix CLI-extension scenarios with registry scenarios use unique fake module names per test (the established `hearth_test_<scenario>` convention).

### Section 10: SDK surface

What plugin authors import:

```python
from hearth_auth import (
    # Entities
    User, Role, Permission, UserRole, RolePermission, ApiKey, ApiKeyPermission,
    # Values
    PermissionName, HashedSecret,
    # Enforcement (the 99% case)
    require_permission, requires, PermissionDenied,
    # Authentication (rarely needed directly; transports use them)
    AuthenticateUser, AuthenticateApiKey, AuthenticationFailed,
)
```

What the kernel exports (Actor primitive):

```python
from hearth import Actor, System, Anonymous, PluginActor
```

What ADR-0005-style amendment-ready primitives.md says:

```markdown
# Spec: Actor primitive
> Source module: hearth/primitives/actor.py
> Parent ADR: ADR-0005 (primitives).

An Actor is the "who" of an Action — the entity or kernel-shipped
identity threading through every Action call. Two kinds:

1. Entity actors: any Entity subclass that declares `actor_kind: ClassVar[str]`
   and implements `async def has_permission(uow, permission: str) -> bool`.
   Plugin-contributed (e.g., hearth-auth's User and ApiKey).
2. Dataless actors: kernel-shipped types for callers with no backing row.
   System, Anonymous, PluginActor.

Both satisfy the Actor Protocol. The kernel uses the Protocol type as the
parameter to Action.handle(uow, actor) and to outbox event records.
Permission resolution semantics are the actor's own responsibility.
```

### Section 11: Testing posture

**Kernel tests**:
- `tests/test_actor.py` — Protocol shape, dataless actor types, `has_permission` defaults.
- `tests/test_cli.py` — add a test for the `hearth.cli` entry-point discovery (same monkeypatch shape as `hearth.plugins` tests).

**Plugin tests** in `plugins/auth/tests/`:
- One file per Action group (`test_user_actions.py`, `test_role_actions.py`, `test_api_key_actions.py`).
- `test_authentication.py` — full login flow, bad credentials, disabled users, expired/revoked keys.
- `test_helpers.py` — `require_permission` and `@requires` decorator, including the wildcard case.
- `test_bootstrap_cli.py` — the `hearth auth bootstrap` command, including the "already bootstrapped" guard.
- `test_cross_plugin_enforcement.py` — a tiny in-tree fake plugin (in `conftest.py`) that registers a `@requires("foo:read")` action, to prove cross-plugin enforcement works.

**Test helpers in `hearth_auth.testing`**:

```python
async def seed_admin(harness, email="admin@test", password="adminpass") -> User:
    """Programmatic equivalent of `hearth auth bootstrap`. Used in test setup."""
    ...

async def grant_permissions(harness, role: Role, *permissions: PermissionName) -> None:
    """Bulk-create Permission rows and attach to role. Test convenience."""
    ...
```

## Implementation sequencing

One spec, one implementation plan. Dependency order:

1. **Drop placeholder commerce plugin.** Remove `plugins/commerce/` directory. Update any references in pyproject extras / harness fixtures.

2. **Extend `hearth db init` to create plugin tables.** Currently `_init_impl` only runs `METADATA.create_all` against the kernel's outbox; plugin tables aren't registered in METADATA at init time because their modules haven't been imported. Add a `Registry.build()` call before `create_all` so plugin entity modules import and register with `MappedAsDataclass`, then `create_all` picks up `auth__user` and friends. This is a small change to [hearth/cli/_db.py](../../../hearth/cli/_db.py) but a prerequisite for `hearth auth bootstrap` to work end-to-end after `pip install hearth[auth]`.

3. **Kernel Actor refactor.**
   - Rename `hearth/primitives/identity.py` → `hearth/primitives/actor.py`.
   - Define `Actor` Protocol with `actor_kind: ClassVar[str]` and `async def has_permission`.
   - Ship `System`, `Anonymous`, `PluginActor` dataless implementations.
   - Update `hearth/__init__.py` re-exports.
   - Update `Action.handle` signature: `identity: Identity` → `actor: Actor`.
   - Update outbox/audit serialization. The existing outbox table has a single JSONB `actor` column (see [hearth/kernel/persistence.py](../../../hearth/kernel/persistence.py)). The actor column is repurposed to hold `{"kind": actor.actor_kind, "id": <entity id as string or null>, "meta": <type-specific dict>}`. Specifically:
     - `User`: `{"kind": "user", "id": "<entity id>", "meta": {}}`
     - `ApiKey`: `{"kind": "api_key", "id": "<entity id>", "meta": {}}`
     - `System`: `{"kind": "system", "id": null, "meta": {}}`
     - `Anonymous`: `{"kind": "anonymous", "id": null, "meta": {}}`
     - `PluginActor`: `{"kind": "plugin", "id": null, "meta": {"alias": "<plugin alias>"}}`
     The kernel writes this JSON; it does NOT reconstruct typed actors from outbox rows. Audit consumers read the JSON directly. Type-specific metadata (alias, future delegation chain, etc.) lives under `meta` so the kernel doesn't need to update the schema when new actor types are added.
   - Update harness construction.
   - Write `docs/core/primitives/actor.md`.
   - Amend ADR-0005 (rename note, actors-are-entities note).

4. **Plugin CLI extension.**
   - Add `_register_plugin_cli_groups()` to `hearth/cli/__init__.py`.
   - Tests for entry-point discovery + reserved-name guard + failure tolerance.

5. **`hearth-auth` plugin scaffold.**
   - `plugins/auth/pyproject.toml` with `hearth.plugins` and `hearth.cli` entry points; declare `hearth` and `hearth-commons` as workspace dependencies (auth uses `EmailAddress` from commons).
   - `plugins/auth/hearth_auth/_base.py` via `bases_for("auth")`.
   - `plugins/auth/hearth_auth/values/` — `PermissionName`, `HashedSecret`.
   - `plugins/auth/hearth_auth/entities/` — 7 entity files.

6. **Auth plugin Actions (one file per group).**
   - `actions/user.py` — Create/Change/Disable/Enable + Authenticate.
   - `actions/role.py` — CreateRole + Assign/Revoke.
   - `actions/permission.py` — CreatePermission + Assign/Revoke from Role.
   - `actions/api_key.py` — Create/Authenticate/Revoke.
   - `events/` — one file per event group.

7. **Auth plugin helpers.**
   - `helpers.py` — `require_permission`, `requires`, `PermissionDenied`, `AuthenticationFailed`.
   - `hearth_auth/__init__.py` re-exports.

8. **Auth plugin CLI.**
   - `cli.py` — `auth_app` Typer app with `bootstrap` command.

9. **Test suite.**
   - Kernel: `tests/test_actor.py`, extend `tests/test_cli.py`.
   - Plugin: `plugins/auth/tests/` with the files listed in Section 11.
   - `hearth_auth.testing` module.

10. **Documentation.**
    - `docs/core/primitives/actor.md` (new).
    - `docs/core/plugins/auth.md` (new — plugin contract for SDK consumers).
    - Register `hearth-auth` extras in root `pyproject.toml` (`auth = ["hearth-auth"]`).
   - ADR-0005 amendment.

## Open questions deferred to later specs

- **Delegation** (`on_behalf_of`). The current Protocol doesn't preclude adding it; deferred until a concrete need arises (likely the WhatsApp bot plugin).
- **Audit log of authorization decisions.** Useful for forensics, defer until there's evidence we need it.
- **Per-resource roles.** Pure single-tenant for now.
- **Permission registry / plugin-declared permissions / auto-seeding.** Migration system's job; v1.1+.
- **Token-based session story.** Transport layer's job; out of scope.
- **Password reset, MFA, OAuth, OTP, magic link.** Future credential mechanisms; nothing in v1 blocks them.

## References

- [ADR-0005 (primitives)](../../architecture/0005-primitives.md) — Identity is the primitive being renamed.
- [ADR-0008 (built-in plugins)](../../architecture/0008-built-in-plugins.md) — auth as an opt-in plugin.
- [2026-05-14-registry-cli-design.md](2026-05-14-registry-cli-design.md) — registry + CLI prerequisites; `hearth-auth` is the first non-scaffold plugin to use them.
- [CLAUDE.md](../../../CLAUDE.md) — Identity/auth listed as an open decision (this spec resolves it).
