# Primitives

The seven kernel primitives plugins compose with — the fixed surface from
[ADR-0005](../../architecture/0005-primitives.md). Adding or removing one
requires a new ADR.

Each primitive has its own spec in this folder. Specs cover the public
declaration shape, the kernel's contract, and what plugins must guarantee
in return.

| Primitive | Spec | What it is |
| --- | --- | --- |
| `Entity` | [entity.md](entity.md) | Things with identity and lifecycle |
| `Event` | [event.md](event.md) | Immutable facts; outbox-delivered |
| `Value` | [value.md](value.md) | The contract for embedded immutable types |
| `Action` | [action.md](action.md) | Synchronous transactional commands |
| `Job` | [job.md](job.md) | Deferred or scheduled work |
| `Identity` | [identity.md](identity.md) | The actor performing an action |
| `View` | [view.md](view.md) | Read-side projections |

## What's *not* a primitive

(See [ADR-0005](../../architecture/0005-primitives.md) §"What is
deliberately not a primitive" for the full list.)

- Workflow / state machine
- Form / UI definition
- Permission / role
- Translation / i18n
- Configuration

These are plugin concerns or out of scope. The default answer to "should
this be a primitive?" is no.

## Declaration shapes

The seven primitives don't have to share a declaration shape — each picks
the shape that matches its semantics:

- **`Entity`** is a base class with class-level kwargs and
  `dataclass_transform` support (see
  [ADR-0006](../../architecture/0006-orm-contract.md) and its 2026-05-02
  amendment). Instances carry identity, lifecycle, and registry semantics;
  subclassing is the natural fit.
- **`Action`, `Job`** are likely candidates for decorator-based
  registration. Their instances are stateless commands rather than
  identity-carrying objects. *Decision deferred to the slice.*
- **`Event`, `Value`** are dataclass-shaped (immutable, structural
  equality). Likely subclassing with `dataclass_transform` or
  `@dataclass`-style decoration. *Decision deferred to the slice.*
- **`Identity`** is a small ADT (`User`, `ApiKey`, `System`, `Plugin`).
  Likely subclassing.
- **`View`** is read-side and most freedom-preserving — could be a class
  with a query method, a function with a decorator, or both. *Decision
  deferred to the slice.*

Each spec records the chosen shape once decided.

## Where specific values live

The `Value` primitive is the kernel's contract for "no identity,
immutable, embedded." *Specific* values like `Money`, `Address`,
`PhoneNumber` are not kernel concerns — they live in plugins. See
[ADR-0008](../../architecture/0008-built-in-plugins.md) for the
kernel-vs-commons-vs-domain rule.
