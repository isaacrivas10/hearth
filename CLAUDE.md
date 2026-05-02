# CLAUDE.md — Hearth

## Status

Pre-code. This document and the ADRs in `docs/architecture/` are the entire project.
The architectural foundation was decided before any code was written; that is intentional.

## What Hearth is

A domain-agnostic business kernel with an SDK-first plugin contract. The kernel
knows nothing about commerce, products, orders, appointments, or any other
domain — those are introduced by plugins. Hearth treats e-commerce,
point-of-sale, inventory, appointment booking, or any other "things-and-money"
workflow as the same shape of problem on top of the same primitives.

The reference first plugin is commerce, because the project's owner runs a small
Honduran digital-goods store and needs to migrate off Odoo. Odoo's plugin
architecture is the inspiration. Odoo's scope, complexity, and ERP-coded bias
are explicitly not.

## Origin

This project began as a working session in the `fb_chatbot` repo (a frozen 2019
Flask Messenger bot for the same store). That session produced the
architectural commitments below before any code was written. This document is
the bridge between that conversation and future Hearth work; it is the source
of truth for "what was decided and why" until the ADRs and code supersede it.

## Architectural commitments

These are decided. Each has its own ADR in `docs/architecture/`. Do not
relitigate without explicit pushback from the user.

1. **Kernel + SDK, not a product.** Hearth is a foundation. Commerce is plugin
   zero. Multiple unrelated domains should sit on top without the kernel
   learning about them. (ADR-0001)

2. **Single-tenant deployment.** One kernel + chosen plugins → one Docker
   container → one database. The merchant chooses the database. No
   multi-tenancy in the kernel. Deployment model is "Snowflake-on-any-cloud" —
   kernel is portable, infrastructure is the operator's. (ADR-0002)

3. **In-process Python plugins, declared at build time.** Plugins are Python
   packages installed alongside the kernel and listed in a manifest. No runtime
   plugin loading, no inter-process plugin contracts. Hard dependencies between
   plugins, declared explicitly. (ADR-0003)

4. **SDK-first.** The public Python surface plugins import from is the
   kernel's contract. Designed before internals stabilize. If the kernel needs
   to expose something to plugins, it goes through the SDK; plugins never reach
   into kernel internals. Reference: Apache Airflow's provider model. (ADR-0004)

5. **Seven primitives, exactly.** The kernel exposes these to plugins and
   nothing else:
   - **Entity** — identity, lifecycle, mutable, persisted with row updates.
   - **Event** — immutable fact, append-only, has timestamp and causation.
   - **Value** — no identity, immutable, embedded in entities. `Money`,
     `Address`, `Reference`.
   - **Action** — synchronous transactional command. Takes a command DTO and a
     unit-of-work, mutates entities, emits events.
   - **Job** — deferred or scheduled work. Idempotent by contract.
   - **Identity** — the actor performing an action (user, API key, system,
     plugin).
   - **View** — read-side projection, plugin-defined, decoupled from entities.
   (ADR-0005)

6. **ORM contract.** SQLAlchemy underneath; narrow `Entity`-shaped surface
   above. Plugins subclass `Entity`, declare fields with kernel types, get
   CRUD and lifecycle hooks. Plugins cannot access the SQLAlchemy session,
   write raw SQL, or use ORM features the kernel doesn't bless. The kernel uses
   escape hatches; plugins live in the walled garden. Cross-plugin references
   are object-form (`References(Order)`), with string form as a forward-
   reference escape hatch only. (ADR-0006)

7. **Transactional contract: outbox pattern.** Action mutations and event
   records persist in the same database transaction. Subscribers run *after*
   commit, asynchronously, with at-least-once delivery and idempotency required
   on the subscriber side. Synchronous in-transaction subscribers are
   explicitly rejected — they let downstream failures roll back upstream
   state. (ADR-0007)

## Composition model

- **Entities** hold data and enforce their own invariants. They do not
  contain business workflows.
- **Actions** orchestrate across entities, are first-class registered objects,
  and emit events on success.
- Plugins extend the system by **registering new actions** and **subscribing
  to events** — never by subclassing entities. This is what makes multi-plugin
  extension of the same domain (e.g., layaway + WhatsApp + inventory all
  touching order placement) compose without Odoo's `_inherit`-style magic.
- Presentation layers (storefronts, WhatsApp handlers, POS terminals) are
  themselves plugins that register routes and handlers. From the kernel's
  perspective they are indistinguishable from any other plugin.

## Non-goals

- Multi-tenancy in the kernel.
- A first-party admin UI ecosystem like Odoo's.
- Runtime plugin loading or dynamic plugin discovery.
- Becoming an ERP. Accounting, MRP, HR are plugin or out-of-scope problems.
- Exposing SQLAlchemy directly to plugins.
- Synchronous in-transaction event subscribers.
- Configurable middle-ground designs. We commit to extremes deliberately.

## Open decisions

These were deferred. Do not pre-design them; let the first vertical slice
force the answers.

- **Identity / auth model.** Known only as "actions take an `Identity`."
- **Migrations.** Plugin entity declarations need a versioned schema log;
  mechanism TBD.
- **Job runner.** In-process vs. separate worker. Current lean: separate
  worker for outbox dispatch and scheduled jobs.
- **Repo structure.** Monorepo with `kernel/` + `plugins/`, or kernel-only
  with plugins as separate repos. Current lean: monorepo to start.
- **Storefront plugin shape.** Headless API only, or server-rendered fallback.
  Decide when writing it.

## First vertical slice

The smallest slice that exercises every primitive:

> A `commerce` plugin defines a `Customer` entity. An `IdentifyCustomer`
> action takes a phone number, finds-or-creates the entity, and emits a
> `CustomerIdentified` event. A test imports the in-memory test harness from
> the SDK, runs the action, asserts the entity is persisted and the event is
> recorded in the outbox.

This forces decisions on: SDK package shape, `Entity` declaration syntax,
`Action` signature, unit-of-work API, in-memory test harness, outbox table
contract. Target around 300 lines of kernel code. The next round of design
questions will be informed by real friction instead of imagined friction.

## Working with this codebase

- Single-tenant, in-process Python. Do not propose multi-tenant designs,
  runtime plugin discovery, or cross-process plugin contracts unless asked.
- Plugins must never reach into kernel internals. If a plugin needs to, the
  SDK is missing something — extend the SDK, don't escape it.
- Reject middle-ground designs. We commit to extremes: single-tenant,
  in-process, hard deps, opinionated narrow ORM surface, outbox-only events.
  Push back if a proposal drifts toward "configurable, flexible, both/and."
- Spanish-language UX is a real constraint for the first plugin set. Payment
  integrations (Tigo Money, Tengo, Lafise, BAC) and the WhatsApp Cloud API
  are the practical first targets after the vertical slice above.
- This is a hobby project for the owner, but the owner is a professional
  software engineer and is willing to work on heavy tasks. Don't infantilize
  the design or pad estimates. Ship velocity matters; quality matters more.

## Conversation provenance

The architectural decisions above came from a working session on 2026-04-30
between the project owner (a software engineer who runs the Honduran store
the system is initially built for) and Claude. The session covered: review of
the legacy `fb_chatbot` codebase; rejection of rebuilding the chatbot or
pursuing a marketplace; the decision to build a domain-agnostic kernel; and
the seven commitments enumerated above. This document supersedes that
conversation as the source of truth.
