# ADR-0003: Plugin Model — In-process Python Packages with Build-time Manifest

## Status

Accepted.

## Context

The plugin model is the load-bearing decision of the entire project. It
determines what a plugin author can do, what isolation guarantees the
kernel offers, how plugins compose with each other, what "deployment" of
a Hearth instance means, and what kinds of bugs are possible. Everything
else in the architecture is downstream of this choice.

Three families of plugin model exist in production systems:

1. **External-service plugins (Shopify, modern Slack apps).** Plugins are
   independent processes that speak to the kernel over HTTP/webhooks/RPC
   with a versioned contract. Pros: strong isolation, language-agnostic,
   independently deployable. Cons: the contract becomes the bottleneck.
   Every new capability requires new endpoints, new versioning, new
   migration coordination. Plugin authors fight the contract more than
   they build features. Local development requires a contract simulator.
   Cross-plugin composition is impossible without the kernel mediating
   every interaction.
2. **In-process compiled plugins (.so / .dll / .wasm).** Plugins ship as
   compiled artifacts loaded into the kernel process. Pros: performance,
   some isolation via WASM sandboxes. Cons: compilation toolchain per
   plugin author, slow iteration, language constraints, debugging
   nightmares. Overkill for our use case.
3. **In-process source plugins (Odoo, Django apps, Airflow providers,
   pytest plugins).** Plugins are source-code packages installed into the
   same Python process as the kernel. Pros: trivial composition (plugins
   import from each other and from the SDK), trivial debugging, fast
   iteration, no contract negotiation. Cons: no isolation between plugins
   or between plugins and the kernel; a buggy plugin can corrupt kernel
   state; plugin authors can reach into kernel internals if the SDK lets
   them.

The owner has explicit experience with all three and explicit preferences:
Odoo's plugin model is the best they've used, Shopify's is contract hell,
and Airflow's provider system is the ideal reference for a Python-package
plugin model that is still well-bounded.

The remaining design questions, given a Python in-process model, are:

- **When are plugins selected?** At runtime (discovery via entry points,
  enabling/disabling without restart) or at build time (declared in a
  manifest, baked into the image)?
- **What dependencies can plugins declare on each other?** Hard
  dependencies (plugin A imports from plugin B; A doesn't load if B is
  absent) or soft references (A names B by string and works without it)?
- **How do plugins extend each other?** By subclassing/monkey-patching
  shared classes (Odoo `_inherit`), or by registering new things and
  subscribing to events (Airflow operators, Django signals)?

## Decision

Plugins are **in-process Python packages, declared at build time, with
hard dependencies, that extend the system by registration rather than
inheritance.**

Specifically:

1. **A plugin is a Python package.** It declares itself by exposing a
   `Plugin` subclass (or equivalent registration object) from the SDK
   that lists what it contributes: entities, actions, events, jobs,
   views, integrations, route handlers.
2. **The instance's plugin set is declared in a manifest at build time.**
   A `plugins.toml` file (or equivalent — the exact format is a later
   decision) lists the plugins and versions that constitute this Hearth
   instance. The manifest drives `pip install` (or the chosen package
   manager) at image-build time. The image _is_ the instance's identity.
3. **No runtime plugin loading.** There is no admin UI, no API call, no
   filesystem scan that adds or removes a plugin from a running kernel.
   Changing the plugin set means rebuilding the image and redeploying.
   This is intentional and non-negotiable.
4. **Plugins declare hard dependencies on other plugins.** A plugin
   that needs `commerce` declares it (probably as a regular Python
   package dependency plus a kernel-level declaration). The kernel
   refuses to start if dependencies are missing or version-incompatible.
   This catches integration bugs at startup, not in production.
5. **Cross-plugin references are object-form, not string-form.** When
   `whatsapp.Conversation` references `commerce.Order`, it does so by
   importing the `Order` class. Typing works, missing dependencies fail
   at import time. The string form (`References("commerce.Order")`) is
   reserved as a forward-reference escape hatch within a single module.
   Details in ADR-0006.
6. **Plugins extend the system by registration, not by subclassing
   kernel- or other-plugin-defined classes.** A plugin adds a new
   capability by registering a new `Action`, subscribing to an `Event`,
   adding a `Job`, or contributing a `View`. Plugins do not subclass
   another plugin's `Order` to add `place_order_with_layaway`; they
   register a new action that orchestrates the same entities. This is
   the single most important rule for keeping multi-plugin extension
   composable.

## Consequences

- **Instances are reproducible.** "What's in production" is exactly
  "the manifest at the commit that built the image." No drift, no
  in-database plugin state, no "well, on this server we also enabled X
  last Tuesday."
- **Plugin authors can build with full IDE support.** Imports are real
  imports; types are real types; refactors work; tests run with
  ordinary `pytest`. There is no synthetic contract layer to fight.
- **Composition is addition.** New behavior is new actions and new
  subscribers, not overrides of existing behavior. Two plugins that both
  want to react to `OrderPlaced` simply both subscribe; they don't fight
  for ownership of a shared method.
- **Failure modes are honest.** A plugin can crash the kernel. A plugin
  can corrupt the database if it abuses raw SQL — which is why ADR-0006
  forbids plugins from accessing raw SQL or the SQLAlchemy session. The
  walled-garden ORM surface is the mitigation; it is not optional.
- **Operators must rebuild and redeploy to change plugins.** This is the
  trade for reproducibility. Hot-add of plugins is not a goal; it is an
  anti-feature. If a plugin must be added urgently, the rebuild-deploy
  cycle is what we tune (small images, fast CI), not the plugin model.
- **Cross-plugin breaking changes are visible.** Bumping a plugin's
  major version with breaking SDK or schema changes will fail
  dependent plugins at import or migration time. The build catches it.
- **Plugins are not sandboxed from each other.** A buggy plugin can
  observe or interact with state belonging to other plugins via the
  database. This is acceptable because (a) all plugins in an instance
  are deliberately installed by the operator, who is the trust boundary,
  and (b) sandboxing in-process Python is a fool's errand.

## Alternatives considered

- **Runtime plugin loading via entry points.** Considered. Rejected
  because it makes "what is running" a function of the database, the
  filesystem, the entry-points cache, and the admin UI's history —
  rather than a function of the image. Reproducibility loss is severe;
  the upside (hot-swapping plugins) is not a problem we have.
- **Soft string-form cross-plugin references.** Considered. Rejected for
  ordinary use because it gives up typing and IDE support, and because
  the operator's stated principle is hard dependencies declared
  explicitly. String form remains as a forward-reference escape hatch;
  see ADR-0006.
- **Inheritance-based extension (Odoo `_inherit`).** Considered. Rejected
  because it creates implicit ordering of plugin loads, makes "who
  defines this method" hard to answer, and breaks down when two plugins
  want to extend the same method. Odoo solves this with method
  resolution order magic that is famously hard to debug. Registration
    - events do the same job with explicit ordering and trivial
      introspection.
- **External-process plugins for some plugin classes (e.g., payment
  integrations).** Considered. Rejected for now: a plugin that wraps an
  external service is still a Python module; it just makes HTTP calls
  inside its action handlers. Forcing it into a separate process buys
  no isolation we need. If a future plugin genuinely requires process
  isolation (heavy ML model, untrusted code), it can run as a sidecar
  service that the in-process plugin talks to — but the plugin itself
  is still in-process.
- **Plugins as separate git repos, kernel as a library.** Possibly the
  long-term shape but premature. Lean toward a monorepo with `kernel/`
  and `plugins/<name>/` until the SDK stabilizes, then consider
  splitting. This is an open decision tracked in `CLAUDE.md`.

## References

- ADR-0001 — vision and non-goals.
- ADR-0002 — deployment model (one image per instance).
- ADR-0004 — SDK-first design (the public surface plugins import from).
- ADR-0005 — primitives (what plugins register).
- ADR-0006 — ORM contract (the walled garden).
