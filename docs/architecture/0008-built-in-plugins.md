# ADR-0008: Built-in Plugins via Extras; Nothing is On by Default

## Status

Accepted.

## Context

ADR-0001 commits to "the kernel knows nothing about commerce, products,
orders, customers, payments, inventory, appointments, or any other
domain." ADR-0003 commits to "in-process Python plugins, declared at
build time" and to a manifest-driven plugin set.

Two questions the earlier ADRs left implicit, both surfaced while
designing the SDK package layout:

1. **Defaults.** Does the kernel ship with any plugins active out of
   the box? Some values look "obviously useful" — a `Money` type, a
   `PhoneNumber` type, an `Address` type. Tempting candidates for
   built-ins. But "default-on" cuts against the kernel's
   domain-agnostic stance: a non-commerce operator wouldn't want
   commerce-shaped helpers installed by default, and even broadly
   useful values like `Money` (currency-aware) presume a particular
   shape of business.
2. **Manifest format.** ADR-0003 referred to "a `plugins.toml` file
   (or equivalent) lists the plugins and versions that constitute this
   Hearth instance." That left the format open. The actual mechanism
   that enforces "which plugins exist in this instance" is `pip
   install` — pip extras and the manifest are two ways of saying the
   same thing, and maintaining both creates a synchronization problem
   with no benefit.

A third question that fell out while answering the first two: where do
broadly-useful values like `Money` and `PhoneNumber` live, given that
they are not kernel concerns and not commerce-specific either? The
answer is "in their own plugin." That plugin needs a name and a
location, and both interact with the defaults question.

## Decision

The kernel ships with **zero active plugins by default**. Operators opt
into plugins by installing them, and **pip extras are the manifest**.

Specifically:

1. **`pip install hearth` installs the kernel only.** No commerce, no
   commons, no anything. The kernel must be runnable and testable in
   this state — every primitive, every kernel facility, every test
   that the kernel ships works without any plugin loaded.
2. **Plugins are installed via extras.** `pip install hearth[commerce]`
   installs the kernel plus the `commerce` plugin. `pip install
   hearth[commerce,commons]` installs both. The operator's Dockerfile
   line is the instance's manifest, full stop.
3. **Built-in plugins live in the same monorepo as the kernel.** A
   top-level `plugins/` directory contains one Python package per
   plugin (`plugins/commerce/`, `plugins/commons/`, etc.). Each plugin
   has its own `pyproject.toml`; the kernel's `pyproject.toml` lists
   each as an extras target.
4. **"Built-in" means "ships in the monorepo."** It carries no special
   runtime status. A built-in plugin is treated by the kernel
   identically to a third-party plugin installed from PyPI: same
   registration shape, same dependency declaration, same audit trail.
5. **Installed equals active.** There is no separate runtime "enable"
   flag. If a plugin's distribution is present in the Python
   environment, it participates. This collapses what would otherwise
   be two states (installed-but-disabled, installed-and-enabled) into
   one. ADR-0003's "image *is* the instance's identity" gets a sharper
   enforcement mechanism.

### The kernel-vs-commons-vs-domain line

A three-tier rule for where a value type lives, decided by *who
manipulates the value*:

- **Kernel ships values it itself manipulates.** Currently `EntityId`
  (for identity assignment) and `Reference` (for cross-plugin FK
  integrity). The kernel writes to these as part of its own job;
  plugins import them.
- **`commons` ships values used across multiple domain plugins but not
  by the kernel.** `Money`, `Address`, `PhoneNumber`, `EmailAddress`.
  The kernel never reads or writes these; multiple plugins do.
- **Domain plugins ship values used only within that domain.**
  `HonduranID` and `RTN` (Honduran tax ID) belong in commerce or an
  accounting plugin. `BACAccountNumber` belongs in a banking
  integration. Only the owning plugin and its declared dependents
  touch these.

The test is **who writes the value, not who reads it.** If the kernel
manipulates a value as part of its own machinery, kernel. If multiple
plugins read/write but the kernel doesn't, commons. If only one
plugin's machinery touches it, that plugin.

## Consequences

- **The "default Hearth experience" is conservative.** A new operator
  who runs `pip install hearth` gets a kernel with no domain values
  loaded. This is correct: the kernel is a foundation, not a starter
  kit.
- **Extras are the source of truth for *which plugins are running*.**
  No separate file to keep in sync. `pip freeze` and the operator's
  Dockerfile both answer the question identically.
- **The kernel's test suite must run with zero plugins installed.**
  This is a forcing function for keeping the kernel domain-agnostic.
  If the kernel needs `commons` or `commerce` to pass its own tests,
  that's a bug. Plugin tests run with the plugin (and its declared
  dependencies) installed.
- **Operators must learn the extras names.** Documentation lists each
  built-in plugin and the extras name that installs it. Third-party
  plugins document themselves.
- **`pip install hearth[all]` may exist as a development convenience.**
  Installs every built-in plugin. Explicitly not what production
  deployments use; production deployments name the exact plugins they
  want.
- **The `plugins.toml` mention in ADR-0003 is superseded.** Where
  ADR-0003 refers to "a manifest at build time," read "the operator's
  pip extras specification (Dockerfile line, requirements.txt,
  `pyproject.toml` dependencies, or equivalent)." No standalone
  `plugins.toml` file is required.
- **Plugin authors get a clean `from hearth_<name> import …` namespace.**
  The plugin's package name is the import root; no extra ceremony.

## What ships in the monorepo (initial set)

These are the plugins Hearth ships with — none active by default,
each opt-in via extras. The list is descriptive of intent at the time
of this ADR, not prescriptive of what must always exist; future ADRs
may add or remove built-ins.

- **`commons`** — common business vocabulary as values: `Money`,
  `Address`, `PhoneNumber`, `EmailAddress`. No entities, no actions,
  no events. A values-only plugin that other plugins can depend on
  for shared vocabulary. Install with `pip install hearth[commons]`.
- **`commerce`** — the reference commerce plugin. Defines `Customer`,
  `Order`, `Product`, etc. and the actions/events that operate on
  them. Depends on `commons` for `Money`, `Address`, `PhoneNumber`.
  Install with `pip install hearth[commerce]` (which transitively
  installs commons).

Future plugins (`whatsapp`, `tigo-money`, `lafise`, etc.) live in the
same `plugins/` directory under the same model.

### Strict rule for `commons`

`commons` is values-only. The moment an entity, action, event, job, or
view appears in `commons`, it has stopped being commons and become a
domain plugin in disguise. Enforce by convention now and by lint rule
once the kernel is mature.

## Alternatives considered

- **Default-on built-ins, disable-able.** Considered. Rejected for the
  reasons in ADR-0001: the kernel must remain domain-agnostic, and
  default-on built-ins (even a `Money` type) push commerce-shaped
  thinking onto operators who didn't ask for it. Default-off via
  opt-in extras is honest and simple.
- **A standalone `plugins.toml` manifest file.** Considered (and
  mentioned in ADR-0003). Rejected as redundant with pip extras. Pip
  already enforces "which packages exist in this Python environment,"
  which is exactly what a plugin manifest needs to express.
- **Putting `Money`/`Address`/`PhoneNumber` inside the `commerce`
  plugin.** Considered. Rejected because non-commerce plugins would
  then have to depend on `commerce` to use `Money`, which leaks
  commerce identity outward. These values are used across appointments,
  POS, field service, CRM, and accounting — they belong in a shared
  plugin, not under commerce.
- **A separate runtime "enable" step.** Considered. Rejected because
  it adds a state (installed-but-disabled) for no clear gain.
  Removing a plugin means removing it from the extras list and
  rebuilding. ADR-0003's image-as-identity principle stays intact.
- **A "starter kit" extras bundle pre-mixing commons + commerce as the
  default install.** Considered. Rejected because commons is useful
  well outside commerce; bundling them invites scope creep on the
  starter and blurs the line between "shared vocabulary" and
  "commerce-specific."

## Open questions

These are deferred to the slice rather than pre-decided.

- **Whether a values-only plugin like `commons` needs a `Plugin`
  registration object at all.** ADR-0003 says plugins "expose a
  `Plugin` subclass... that lists what it contributes." A pure-values
  plugin contributes nothing the kernel registers — it just exposes
  Python classes for other plugins to import. The slice will tell us
  whether a no-op `Plugin` is required-but-empty, or whether the
  plugin model permits "no-registration" plugins where being
  importable is the whole contribution.
- **Whether `[all]` is worth shipping at all.** Useful for development
  and CI; a footgun in production. May or may not exist; deferred.

## References

- ADR-0001 — vision (kernel is domain-agnostic).
- ADR-0003 — plugin model (in-process Python packages, build-time
  manifest, hard dependencies). This ADR clarifies what "manifest"
  means in practice.
- ADR-0005 — primitives (Value as a primitive contract; specific
  values are plugin concerns). See its 2026-05-02 amendment.
- ADR-0006 — ORM contract (`fields.Value(...)` as the generic embed
  mechanism for plugin-defined values). See its 2026-05-02 amendment.
