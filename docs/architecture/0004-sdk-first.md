# ADR-0004: SDK-first Design

## Status

Accepted.

## Context

Given the in-process Python plugin model from ADR-0003, plugins extend
the system by importing from some public Python surface and registering
things with the kernel. That public surface is the kernel's contract with
plugin authors. Whatever symbols are importable, whatever base classes
are subclassable, whatever signatures are stable — that is the contract,
whether or not we acknowledge it.

The recurring failure mode in plugin systems is to bolt this on later.
The kernel is built first; the public surface accumulates by accident as
plugin authors reach into whatever they need; eventually the team
declares an SDK that mostly papers over the leakage. By that point the
kernel has assumed plugin behavior in ways that constrain what the SDK
can offer, and plugin authors have habits of reaching past the SDK that
are hard to break.

Examples of getting this right and wrong:

- **Apache Airflow.** Provider packages are a clean SDK shape:
  `BaseOperator`, `BaseHook`, well-documented hooks for connections and
  sensors. New providers can be written without touching Airflow core.
  This is the model the operator named as the reference.
- **Django.** `django.contrib` is the plugin model; the public API is
  documented and stable across major versions. Apps can be written
  against a published surface.
- **Odoo.** SDK was never the design center. Plugins reach into ORM
  internals; method-resolution-order magic is part of normal plugin
  authoring. Upgrades break plugins routinely because the "contract" was
  never a contract — it was whatever the kernel happened to expose this
  release.
- **WordPress.** No SDK; "the API" is whatever PHP functions and globals
  exist. Plugin authors do anything; security and upgrades suffer
  proportionally.

The choice is whether the SDK is **designed first**, with the kernel
implementing the SDK's promises, or whether it is **extracted later**
from whatever the kernel happens to expose. We choose first.

The owner used the word "SDK" in conversation but flagged that "SDK"
might not be the right name. The concept is: a Python package, public
and stable, that plugin authors import from. We will call it the **SDK**
in design discussions and in this ADR. The actual package name is a
later, low-stakes decision (`hearth`, `hearth.sdk`, `hearth.api`, etc.).

## Decision

The SDK is the kernel's public Python contract. It is **designed before
the kernel internals stabilize**, and the kernel's internals exist to
serve the SDK's promises.

Specifically:

1. **The SDK is the only thing plugins import from.** Plugins do not
   import from kernel internals. If they do, the SDK is missing something
   — fix the SDK, do not normalize the leak.
2. **The SDK exposes the seven primitives** (Entity, Event, Value,
   Action, Job, Identity, View — see ADR-0005) plus the registration,
   typing, and test-harness machinery needed to use them. Anything
   outside that list is either internal or a candidate for a future SDK
   addition with its own ADR.
3. **The SDK ships a first-class test harness.** Plugin tests must run
   without Postgres, without Docker, without the kernel HTTP server.
   The harness provides: an in-memory `UnitOfWork`, an in-memory event
   bus / outbox, a SQLite-backed entity store, and fixtures for "a
   plugin set with these plugins loaded." Plugin authors write `pytest`
   tests that import from `hearth.testing` (or equivalent) and run in
   milliseconds.
4. **The SDK's stability is treated as a public commitment.** Breaking
   changes to the SDK require an ADR, a version bump, and migration
   notes. Reordering parameters, renaming exported symbols, changing
   base-class signatures, changing event/action protocols — all
   breaking. Internals can change freely; the SDK cannot.
5. **The SDK is the documentation surface.** What's importable from the
   SDK is what's documented for plugin authors. Internals may have
   docstrings for the kernel team, but the canonical reference is the
   SDK package.
6. **The kernel is built by exercising the SDK.** The first plugin
   (commerce) is built with no special privileges — it imports from the
   SDK like any third-party plugin would. If commerce needs something
   the SDK doesn't expose, the SDK gets the addition; commerce never
   reaches around it.

## Consequences

- **Plugin authors get a clean surface from day one.** Imports are
  obvious, types are real, the test harness eliminates the Odoo-style
  "you need a running instance to write a unit test" tax.
- **The kernel team has a forcing function for good design.** Every
  capability the kernel offers must be expressible as a coherent SDK
  addition. Capabilities that don't fit cleanly into the SDK are signs
  of bad abstractions in the kernel.
- **The kernel can be refactored aggressively.** If the SDK is stable,
  internals can be rewritten without breaking plugins. This is the
  long-term payoff: kernel evolution without plugin churn.
- **Documentation and maintenance burden concentrate in one place.**
  The SDK package becomes the project's most important documentation
  artifact. Investment there pays back across every plugin.
- **The first vertical slice is gated on SDK shape.** The slice
  described in `CLAUDE.md` (Customer entity, IdentifyCustomer action,
  CustomerIdentified event) is, by intent, an SDK-design exercise as
  much as a kernel exercise. Writing the slice forces decisions about
  the entity declaration syntax, the action signature, the unit-of-work
  API, and the test-harness fixtures.
- **Internal escape hatches exist but are not part of the SDK.** The
  kernel will need to do things plugins cannot do (e.g., run raw SQL
  for migrations, access the SQLAlchemy session directly, manage the
  outbox dispatcher). Those facilities live in kernel internals, are
  not exported from the SDK, and are not available to plugins. The
  walled garden is real.

## Alternatives considered

- **No SDK; let plugins import from kernel modules.** This is the
  WordPress / Odoo path. Rejected for the reasons above: the contract
  becomes "whatever exists today," upgrades break plugins, and the
  kernel never gets to refactor.
- **SDK extracted later from observed plugin usage.** Rejected because
  by then the leakage will already be normalized. Plugin authors will
  resist the constraint; the kernel will have assumed leaky access in
  ways that constrain the SDK.
- **Multiple SDK tiers (stable / experimental / internal).** Tempting
  but premature. With a single SDK we have one stability promise to
  keep. Tiers can be added later if a clear need emerges (probably for
  experimental capabilities), but starting with one is simpler.
- **Generated SDK from an OpenAPI / IDL spec.** Wrong shape for an
  in-process Python API. The SDK is Python; the contract is Python
  classes, signatures, and protocols. An IDL would force a synthetic
  layer that adds nothing.

## References

- ADR-0001 — vision (kernel + plugins, not a product).
- ADR-0003 — plugin model (in-process Python packages).
- ADR-0005 — primitives (what the SDK exposes).
- ADR-0006 — ORM contract (the walled garden).
- ADR-0007 — transactional contract (an SDK promise).
