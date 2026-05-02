# ADR-0001: Vision and Non-goals

## Status

Accepted.

## Context

Before writing code, we need an unambiguous answer to "what is this thing?" —
because the answer determines what every subsequent design decision means.
The project's owner runs a small Honduran digital-goods store, currently on
Odoo, and started this conversation with a vague idea of "rebuild my old
Facebook Messenger chatbot, but better." That framing was wrong, and the
discussion that produced this ADR clarified what is actually being built.

The relevant prior art was surveyed:

- **Odoo.** Plugin architecture is excellent. Scope is wrong: it's an ERP
  pretending to fit small business, the freemium model has aggressively
  paywalled core features, self-hosting is heavy, and the codebase carries
  twenty years of accounting/MRP/HR scope creep. The good idea (modules
  composing on a small kernel) is buried under the bad idea (one company
  shipping eighty first-party modules in the same repo).
- **Shopify.** Wrong for Latam (payment rails, taxation, language defaults
  all wrong). Plugins are external HTTP services governed by Shopify's app
  store, which is contract hell for plugin authors and a permanent middleman
  for operators.
- **WooCommerce.** PHP/WordPress maintenance burden; plugin ecosystem is a
  security disaster.
- **Medusa, commercetools, Saleor.** Headless commerce kernels in the right
  rough shape. None have Odoo's plugin composition. All assume the consumer
  is a SaaS vendor or a large e-commerce team, not a single technical
  operator running their own infra.
- **Build commerce-from-scratch.** Wrong because the operator wants to spend
  time on the parts that matter (the store, the integrations specific to
  their market) and not on auth, admin panels, plumbing.

The shape that fits the operator's stated principles (scalability,
modularity, extensibility) and constraint (single technical operator, can
work on heavy tasks but is not a team) is: **a small kernel with a clean
plugin contract, where the operator's own store is tenant zero and the
commerce features they need are written as plugins.**

## Decision

Hearth is a **domain-agnostic business kernel with an SDK-first plugin
contract.** Specifically:

1. The kernel knows nothing about commerce, products, orders, customers,
   payments, inventory, appointments, or any other domain. It exposes
   primitives (Entity, Event, Value, Action, Job, Identity, View) and the
   machinery that ties them together (persistence, outbox, dispatch).
2. Domains are introduced by plugins. Plugins are Python packages that
   import from the SDK and register entities, actions, events, jobs, views,
   and integrations.
3. **Commerce is a plugin, not the kernel.** The first reference plugin set
   covers what the project owner's store needs (products, orders, payments
   for Honduran rails, WhatsApp ordering). The kernel does not depend on
   any of it and could ship without it.
4. Other domains — point-of-sale, appointment booking, simple inventory,
   field-service ticketing, anything in the shape of "things, people,
   money, events" — are equally valid plugin sets on the same kernel. They
   are not currently planned, but the kernel must not preclude them.

The single sentence: **Hearth is a kernel. Commerce is its first plugin.
The kernel could host any business domain in the same shape.**

## Consequences

- The first audience is technical operators (developers, devops-capable
  founders, small consultancies). Not non-technical merchants. A
  non-technical merchant should use Shopify or Odoo SaaS; that's fine.
- The kernel must be small enough that one person can hold it in their
  head. If a feature is "useful for some plugins but not the kernel," it
  belongs in the SDK or in a plugin, not in the kernel.
- Documentation, marketing, and examples must consistently lead with the
  kernel-not-product framing. If users come expecting "commerce software,"
  they will be disappointed; if they come expecting a foundation, they will
  be served. This is a positioning constraint as much as a technical one.
- The reference plugin set's first job is to validate the kernel's design
  by being demanding of it. If commerce can be built cleanly as a plugin,
  the kernel is correct. If commerce keeps reaching past the SDK, the
  kernel or SDK is wrong.

## Non-goals

These are explicitly out of scope. They are not "not yet" — they are "not
ever, by design." Reverting any of these requires a new ADR.

- **Becoming an ERP.** No accounting, no MRP, no HR, no general ledger in
  the kernel or in first-party plugins. Plugins for accounting _integration_
  (export to QuickBooks, Honduran SAR fiscal reporting) are fine; building
  double-entry accounting in-house is not.
- **A SaaS product.** Hearth is software that operators run, not a service
  Hearth runs for them. No central control plane, no telemetry, no
  Hearth-hosted instances.
- **A plugin marketplace.** No central registry, no distribution channel,
  no review process. Plugins are Python packages on PyPI or in the
  operator's own infrastructure. Discovery is the operator's problem.
- **An admin UI ecosystem.** The kernel does not ship a generic admin UI.
  If a plugin wants an admin UI, the plugin builds one. The headless
  contract makes this trivial.
- **Multi-tenancy in the kernel.** See ADR-0002.
- **Runtime plugin loading.** See ADR-0003.
- **Generic, configurable, both-ways designs.** Hearth commits to extremes.
  When a design choice has a "flexible" middle option, the middle option
  is the wrong answer.

## Alternatives considered

- **Build commerce, not a kernel.** Faster to ship the operator's store.
  Rejected because the operator's actual interest is in the kernel-shaped
  problem, and because a commerce-only project would re-encounter the
  Shopify/Medusa shape without solving anything new.
- **Fork Odoo, strip it down.** Considered briefly. Rejected because
  Odoo's codebase carries twenty years of assumptions that would have to
  be unwound, and because Python 3.6+/Flask 1.x-era patterns in Odoo's
  ORM are not the patterns we want to inherit.
- **Build on Medusa or Saleor.** Rejected because their plugin models do
  not match what we want (Medusa is JavaScript, plugins-as-services;
  Saleor is GraphQL-first with a centralized Django app underneath).
  Neither would let us define the seven-primitive contract we want.
- **Build it in TypeScript / Go / Elixir.** Considered. Python wins
  because (a) the operator is fluent in Python, (b) SQLAlchemy is the
  mature ORM that fits our requirements, (c) the plugin-as-package model
  is more natural in Python than in compiled languages, and (d) the
  reference points the operator cares about (Airflow, Odoo, Django) are
  all Python.

## References

- `CLAUDE.md` — full architectural summary.
- ADR-0002 — deployment model.
- ADR-0003 — plugin model.
- ADR-0004 — SDK-first design.
