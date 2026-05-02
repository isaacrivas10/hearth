# Hearth

A domain-agnostic business kernel with an SDK-first plugin contract.

Hearth is a foundation for building business applications — commerce,
point-of-sale, inventory, appointment booking, anything in the shape of
"things, people, money, events." The kernel itself knows nothing about any
specific domain. Plugins introduce domains. The first reference plugin is
commerce; the kernel does not depend on it.

## Status

Pre-code. The architectural foundation is captured in `docs/architecture/` and
in `CLAUDE.md`. No kernel code exists yet. The first vertical slice is
specified but not implemented.

## Why this exists

The project was started by a software engineer who runs a small Honduran
digital-goods store on Odoo and wants out. Odoo's plugin architecture is the
inspiration; Odoo's scope, complexity, and ERP-coded bias are explicitly not.
Shopify, WooCommerce, and Medusa were rejected for reasons documented in
`docs/architecture/0001-vision.md`.

The long-term shape: a kernel that any technically capable operator can spin
up on their own infrastructure, in a single Docker container, with a chosen
set of plugins, against a database of their choice — closer to Snowflake's
"runs on any cloud" stance than to a SaaS platform.

## Principles

- **Scalability** — single-tenant per deployment; horizontal scale is the
  operator's choice, not the kernel's burden.
- **Modularity** — seven primitives, one ORM contract, one event model. New
  domains are plugins. The kernel is small.
- **Extensibility** — plugins are Python packages that import from the SDK.
  No runtime loading, no plugin marketplace, no contract negotiation between
  processes. Extension is composition.

## Architecture at a glance

- **Kernel + SDK in Python.** SDK is the public surface plugins import from.
- **Single-tenant.** One kernel + chosen plugins → one container → one
  database. Operator chooses the database.
- **In-process plugins.** Python packages, declared in a manifest at build
  time. Hard dependencies between plugins.
- **Seven primitives.** `Entity`, `Event`, `Value`, `Action`, `Job`,
  `Identity`, `View`. Plugins compose with these and nothing else.
- **Outbox-pattern events.** Persistence is transactional with the action;
  dispatch is post-commit and asynchronous.
- **Headless by default.** Storefronts, WhatsApp handlers, POS UIs are
  themselves plugins.

Full reasoning is in `docs/architecture/`. Read the ADRs in order:

1. [`0001-vision.md`](docs/architecture/0001-vision.md) — what Hearth is
   and isn't, and what was rejected.
2. [`0002-deployment-model.md`](docs/architecture/0002-deployment-model.md)
   — single-tenant, one container, operator-chosen database.
3. [`0003-plugin-model.md`](docs/architecture/0003-plugin-model.md) —
   in-process Python packages, build-time manifest, hard dependencies.
4. [`0004-sdk-first.md`](docs/architecture/0004-sdk-first.md) — the SDK is
   the kernel's contract; design it before internals stabilize.
5. [`0005-primitives.md`](docs/architecture/0005-primitives.md) — the seven
   primitives, why each exists, what's deliberately not a primitive.
6. [`0006-orm-contract.md`](docs/architecture/0006-orm-contract.md) —
   SQLAlchemy underneath, narrow `Entity` surface above, no escape hatches.
7. [`0007-transactional-contract.md`](docs/architecture/0007-transactional-contract.md)
   — outbox pattern; transactional persistence, post-commit dispatch.

## First vertical slice

The smallest implementation that exercises every primitive:

> A `commerce` plugin defines a `Customer` entity. An `IdentifyCustomer`
> action takes a phone number, finds-or-creates the entity, and emits a
> `CustomerIdentified` event. A test from the SDK's in-memory harness runs
> the action and asserts both the entity and the outbox event.

Target: ~300 lines of kernel code. Everything after that is iteration on
real friction.

## Non-goals

- Multi-tenancy in the kernel.
- Becoming an ERP.
- Runtime plugin loading.
- A first-party admin UI ecosystem.
- Exposing SQLAlchemy directly to plugins.
- Configurable middle-ground designs. Hearth commits to extremes
  deliberately.

## License

TBD.
