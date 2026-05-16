# ADR-0005: The Seven Primitives

## Status

Accepted.

## Context

The kernel exposes a fixed set of building blocks to plugins. Every
plugin is some combination of "register entities, define actions,
subscribe to events, schedule jobs, expose views." The set of building
blocks — the primitives — is the most important and most expensive-to-
change thing about the kernel. If the primitives are right, plugins
compose cleanly forever. If they are wrong, plugins fight the kernel
forever and no amount of SDK polish hides it.

Two questions structure this decision:

1. **What is the minimum set of primitives that covers the workflows
   we expect plugins to express?** Too few primitives and plugins
   reinvent shapes the kernel should have given them (every plugin
   builds its own job runner, its own audit log, its own event bus).
   Too many and the kernel takes opinions on things it shouldn't
   (every plugin must use our generic state machine, our generic
   workflow engine, etc.).
2. **What shape should each primitive take?** Specifically: are
   business operations methods on entity classes (Odoo, Django) or
   first-class registered objects (Airflow tasks, CQRS commands)?
   This is the question that determines whether multi-plugin extension
   composes or collides.

The set below was settled in conversation. Two refinements happened
during settling:

- **Action was elevated from "method on Entity" to its own primitive.**
  The Entity-method form (`order.place()`) is natural in Python but
  collapses when multiple plugins want to extend the same operation.
  Two plugins both wanting to override or wrap `place()` cannot do so
  without inheritance ordering games (Odoo `_inherit`) or
  monkey-patching. The first-class form (`PlaceOrder` is an action
  registered with the kernel) makes extension additive: plugins
  register new actions that orchestrate existing entities, or
  subscribe to events from existing actions. They never fight for
  ownership of a shared method.
- **Event and Value were distinguished from Entity.** All three were
  initially being treated as "things plugins declare," but they are
  three distinct shapes: Entity is mutable and identified, Event is
  immutable and append-only, Value is immutable and embedded. The
  SDK exposes them as three distinct base classes; the persistence
  story for each is different (entities are rows that get updated;
  events are rows that never change; values are columns inside
  entities).

## Decision

The kernel exposes exactly seven primitives. New primitives require an
ADR; deletions require an ADR. The set:

### 1. Entity

Has identity, has lifecycle, mutable over time, persisted as rows that
get updated. Owns its invariants. Examples: `Customer`, `Order`,
`Product`, `Appointment`, `PosTerminal`.

- Plugins declare entities by subclassing `Entity` and declaring fields
  with kernel-provided types.
- Entities expose narrow, invariant-protecting methods (e.g.,
  `Order.add_line(product, qty)` enforces "can't add lines to a closed
  order"). They do not contain business workflows that orchestrate
  across multiple entities — that is what Actions are for.
- The kernel manages persistence, identity assignment, lifecycle hooks,
  and audit metadata. Plugins do not see the SQLAlchemy session.

### 2. Event

Immutable record of something that happened. Append-only. Has a
timestamp, an actor (Identity), a causation chain, and a payload.
Persisted to the outbox in the same transaction as the action that
emitted it; dispatched to subscribers post-commit (see ADR-0007).
Examples: `OrderPlaced`, `PaymentReceived`, `CustomerIdentified`,
`StockAdjusted`.

- Plugins declare events by subclassing `Event` and declaring payload
  fields.
- Plugins emit events from inside actions. Events are never edited or
  deleted. Mistakes are corrected by emitting compensating events
  (`OrderCancelled`, `PaymentReversed`) — never by editing history.
- Plugins subscribe to events via registered subscribers. Subscribers
  must be idempotent; the kernel guarantees at-least-once delivery,
  not exactly-once.

### 3. Value

No identity, immutable, often embedded inside entities. Examples:
`Money`, `Address`, `Reference`, `PhoneNumber`, `EmailAddress`. Not
persisted on its own — only as part of an entity that owns it.

- Plugins (and the SDK) declare values by subclassing `Value`.
- The SDK ships a small core set: `Money` (currency-aware arithmetic,
  decimal-precision-aware, supports HNL/USD/etc.), `Reference`
  (cross-plugin reference to another entity, see ADR-0006), and
  baseline scalars. Plugins can declare their own (`HonduranID`,
  `RTN`, etc.).

### 4. Action

A synchronous, transactional command that mutates entities and emits
events. Takes a command DTO (the input) and a `UnitOfWork` (the
transactional handle) and returns the events it emitted. The only
legitimate way for a plugin to change state.

- Plugins declare actions as registered classes (`PlaceOrder`,
  `IdentifyCustomer`, `RecordPayment`).
- Actions are the unit of authorization, audit, and middleware.
  Authorization, logging, idempotency-key handling, transaction
  management — all wrap actions uniformly.
- Actions are testable in isolation: pass a command DTO and an
  in-memory UnitOfWork, assert on returned events and entity state.
  The SDK's test harness makes this trivial.
- Actions may invoke other actions (carefully — composing transactions
  is delicate; the kernel manages this so plugin authors cannot deadlock
  themselves).

### 5. Job

Deferred or scheduled work. Idempotent by contract. Examples:
`SendWhatsAppConfirmation`, `ReconcileBankTransfers`, `SyncInventory`.

- Plugins declare jobs as registered classes with a `run` method.
- Jobs are the kernel's offer to plugin authors for "do this later, or
  on a schedule, or in response to an event, with retry semantics." The
  outbox dispatcher itself is implemented as a kernel job.
- Jobs run in a worker process, not in the request handler. Their
  inputs are values (not live entity references) so they can be
  serialized to a queue.
- Idempotency is the plugin author's responsibility; the kernel
  guarantees at-least-once execution, not exactly-once. Job authors
  must be able to run twice without harm.

### 6. Identity

The actor performing an action. Threads through every action and event
for audit. Concrete subclasses include `User`, `ApiKey`, `System` (the
kernel itself), and `Plugin` (a plugin acting on its own behalf, e.g.,
a scheduled job).

- Plugins receive an Identity in every action call and use it for
  authorization decisions and audit annotations.
- The Identity model in detail is an open decision (see CLAUDE.md);
  what is settled is that there _is_ a primitive for it and that
  plugins must accept and propagate it.

### 7. View

A read-side projection. Plugins define views to expose data for queries
and presentation. Views are decoupled from entities — they may
denormalize, aggregate, join across plugins, or be backed by caches.

- Plugins declare views as registered classes that describe their
  shape (fields and types) and their source (a query against entities,
  an event-driven projection, etc.).
- Views are the natural seam between the write side (entities,
  actions, events) and the read side (HTTP handlers, GraphQL resolvers,
  WhatsApp message formatters). Presentation-layer plugins consume
  views; they do not query entities directly.
- A simple plugin may have views that are thin wrappers over entity
  rows. A complex plugin may have views backed by event-driven
  projections updated by jobs. Both are first-class.

## Consequences

- **The SDK has exactly seven base classes / decorators / registration
  points** (one per primitive), plus the supporting machinery (UnitOfWork,
  field types, the test harness). New plugin authors learn one short list.
- **Multi-plugin extension composes.** Plugin B adds new behavior to
  Plugin A's domain by registering new actions and subscribing to A's
  events. Plugin A's entities and actions are not modified.
- **Audit, authorization, and observability are uniform.** Every
  state change goes through an Action with an Identity; every fact is
  an Event; every deferred work is a Job. Cross-cutting concerns wrap
  these primitives once.
- **Workflows that don't fit the seven primitives don't get built into
  the kernel.** State machines, BPMN-style workflow engines,
  approval chains, generic forms — all are plugin concerns or out of
  scope. The kernel stays small.
- **Some Odoo-style ergonomics are lost.** `order.place()` reads
  better than `kernel.run(PlaceOrder(order_id=order.id))`. We accept
  this trade because the gain (composable extension) is permanent and
  the loss (one extra line) is cosmetic.

## What is deliberately not a primitive

- **Workflow / state machine.** Plugins that want explicit state
  machines build them as patterns over entities, actions, and events.
  The kernel does not impose a workflow shape.
- **Form / UI definition.** The kernel does not know about forms,
  fields-with-labels, or admin pages. Presentation plugins handle this.
- **Permission / role.** Authorization decisions live inside actions
  (which receive an Identity). The kernel does not ship a generic
  role/permission model. Plugins can build them; some will.
- **Translation / i18n.** Plugins handle their own strings. The kernel
  is locale-agnostic.
- **Configuration.** Plugins read config from environment variables or
  whatever they need. The kernel does not ship a generic settings
  registry.

If a future plugin pattern keeps re-implementing one of these and the
duplication is painful, an ADR can propose adding it as a primitive.
Default answer is no.

## Alternatives considered

- **Six primitives (drop View).** Considered. Rejected because views
  are a real seam and without them plugins either query entities
  directly (coupling) or invent ad-hoc DTO patterns (duplication).
  Naming the read side as a primitive saves a lot of bad code later.
- **Eight primitives (add Workflow / StateMachine).** Considered.
  Rejected for now: plugins can express state machines as patterns
  over entities + actions + events without needing a kernel primitive.
  If we see three plugins reinventing the same shape painfully, revisit.
- **Action as method on Entity.** Considered (this was the initial
  shape). Rejected because it does not compose under multi-plugin
  extension. See "Context" above.
- **Event as a kind of Entity.** Considered (initially conflated).
  Rejected because their persistence semantics are different (mutable
  rows vs append-only log) and conflating them produces sloppy
  migrations and audit problems.
- **Value as a kind of Entity.** Same — values have no identity and no
  persistence of their own; entities have both. Different primitive.

## References

- ADR-0001 — vision (kernel as a foundation; primitives are the
  foundation's surface).
- ADR-0003 — plugin model (registration, not inheritance).
- ADR-0004 — SDK-first design (the primitives are the SDK's center).
- ADR-0006 — ORM contract (entities and references in detail).
- ADR-0007 — transactional contract (actions, events, outbox).
- ADR-0008 — built-in plugins via extras (where specific Values live).

## Amendment 2026-05-02 — Value: kernel ships only the contract

The Value section above lists `Money`, `Address`, `PhoneNumber`, and
`EmailAddress` as examples and states that "the SDK ships a small core
set: `Money` (currency-aware arithmetic, decimal-precision-aware,
supports HNL/USD/etc.), `Reference` ... and baseline scalars." That
guidance is superseded.

Per ADR-0008, the kernel ships only what it itself manipulates:

- `EntityId` — the kernel-assigned identity for entities.
- `Reference` — the kernel-managed cross-plugin foreign-key value.

Currency-shaped, geographic, or otherwise domain-shaped values
(`Money`, `Address`, `PhoneNumber`, `EmailAddress`) are *plugin*
concerns. They live in the `commons` plugin, which ships in the same
monorepo as the kernel but is opt-in via `pip install hearth[commons]`
— never active by default. Domain-specific values like `HonduranID`
or `RTN` live in the plugin that owns the domain.

The Value primitive itself remains a kernel concept. The `Value` base
class, the "no identity, immutable, embedded" contract, equality and
hashing semantics, and the embedding mechanism via
`fields.Value(ValueClass)` (see the amendment to ADR-0006) are kernel
responsibilities. The cut is between the *contract* and the *concrete
types*:

- **Value contract** (subclass of `hearth.primitives.Value`, immutable,
  no identity, embedded into entities) — kernel.
- **Kernel-infrastructure values** (`EntityId`, `Reference`) — kernel,
  because the kernel itself manipulates them.
- **Common-vocabulary values** (`Money`, `Address`, `PhoneNumber`,
  `EmailAddress`) — `commons` plugin.
- **Domain-specific values** (`HonduranID`, `RTN`,
  `BACAccountNumber`) — the owning domain plugin.

A separate clarification: `Timestamp` is **not a Value**. It is a
field declaration (`fields.Timestamp(auto_now_add=True)`) that
produces a timezone-aware `datetime` at runtime. There is no kernel
`Timestamp` value class.

## Amendment 2026-05-15 — `Identity` renamed to `Actor`; concrete actors live in plugins

The "Identity" primitive in the original ADR is renamed to **Actor**. The
prior `Identity(BaseModel)` hierarchy with thin `User(Identity)` /
`System(Identity)` / `ApiKey(Identity)` subclasses is replaced with:

1. **`Actor`** as a runtime-checkable `Protocol` exposing:
   - `actor_kind: ClassVar[str]` — snake_case discriminator (e.g. `"user"`,
     `"system"`, `"plugin"`, `"api_key"`).
   - `async def has_permission(uow, permission: str) -> bool` — resolution
     semantics live on the actor itself; the kernel knows about strings, not
     about how permissions are stored.

2. **Kernel-shipped dataless actors** for cases with no backing row:
   - `System` — kernel-internal callers (outbox dispatch, bootstrap).
   - `Anonymous` — public, unauthenticated endpoints.
   - `PluginActor(alias=str)` — a plugin running its own scheduled work.

3. **Plugin-contributed entity-actors** for cases with a backing row:
   - `hearth-auth` ships `User(Entity)` and `ApiKey(Entity)`, both declaring
     `actor_kind` and implementing `has_permission` against their own joins.
   - Future plugins can mint additional actor-capable entities the same way
     (e.g., a hypothetical `WhatsAppNumber(Entity)`).

**Action signature update.** `Action.handle(self, uow, identity)` becomes
`Action.handle(self, uow, actor)`. The harness API exposes the kwarg as
`actor=` instead of `identity=`.

**Outbox serialization.** The outbox `actor` JSONB column stores
`{"kind": actor.actor_kind, "id": <entity id or null>, "meta": <type-specific dict>}`.
Plugin-actor metadata (alias, future delegation chains) lives under `meta`
so the kernel doesn't need to update the schema when new actor types are
added.

**Companion specs.** See [docs/core/primitives/actor.md](../core/primitives/actor.md)
for the primitive contract and [docs/core/plugins/auth.md](../core/plugins/auth.md)
for the auth plugin's SDK surface.
