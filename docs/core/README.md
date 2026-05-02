# `docs/core/` — design specs for the Hearth core

The ADRs in [`docs/architecture/`](../architecture/) capture *decisions* —
what was chosen, why, and what was rejected. They are the source of truth
for "is this allowed?" questions.

This folder captures *design specs* — the concrete shape of the code that
implements those decisions. ADRs answer "should the ORM be SQLAlchemy with
a narrow `Entity` surface above?" Specs answer "what does the `Entity`
declaration syntax look like, what fields exist, what methods are
overridable, what does the test harness fixture look like."

## Two documentation tracks

Hearth ships two pipelines for documentation, both eventually published:

1. **Self-documenting code** — docstrings, type hints, runnable examples
   inside the source. Generates the API reference (Sphinx autodoc or
   equivalent).
2. **Research and design docs** — `docs/architecture/` (ADRs) and
   `docs/core/` (specs). Generates the conceptual reference. The audience
   is engineers who want to understand *why* the system is shaped the
   way it is.

The two pipelines cross-link: each spec lists the source module it
specifies (`hearth/primitives/entity.py`), and code modules link back to
their spec in their docstrings. Reading either side leads to the other.

## Relationship to ADRs

- **ADRs win on principle.** If a spec contradicts an ADR, the ADR is
  right and the spec is wrong. Update the spec, not the ADR.
- **Specs win on detail.** ADRs deliberately stop at the "what was
  decided" level; specs go to the actual signature, field, and method
  names.
- **Specs may evolve faster than ADRs.** A spec is allowed to change as
  the first vertical slice exposes friction. ADRs change only with
  deliberate amendment.
- **Each spec links its parent ADR(s) at the top.** Reading the ADR
  first is the right order: principle, then detail.

## Layout

The folder mirrors the planned `hearth/` package structure. Each spec
file documents the corresponding source module.

```
docs/core/
├── README.md                  # this file
├── primitives/                ↔ hearth/primitives/
│   ├── README.md              # the seven; fixed by ADR-0005
│   ├── entity.md
│   ├── event.md
│   ├── value.md               # the Value primitive *contract*
│   ├── action.md
│   ├── job.md
│   ├── identity.md
│   └── view.md
├── fields.md                  ↔ hearth/fields.py
├── references.md              ↔ hearth/references.py
├── identifiers.md             ↔ hearth/identifiers.py (EntityId)
├── unit-of-work.md            ↔ hearth/unit_of_work.py
├── kernel/                    ↔ hearth/kernel/ (internals; not plugin-facing)
│   ├── README.md
│   ├── transaction.md
│   ├── outbox.md
│   ├── registry.md
│   ├── persistence.md
│   └── migrations.md
└── testing/                   ↔ hearth/testing/
    ├── README.md
    └── harness.md
```

Plugin-specific specs live with each plugin (e.g.,
`plugins/commerce/docs/`), not here. `docs/core/` is strictly about the
kernel and the SDK surface.

## Authoring rules

- **Code samples are the primary content.** Prose explains intent; code
  is the contract.
- **Code samples are runnable in principle.** No pseudo-code that papers
  over decisions. When the slice lands, every spec example should
  type-check against the SDK.
- **Specs are honest about open questions.** A spec that punts on a
  decision says so explicitly, in an "Open questions" section. The
  slice will force the answers.
- **One file per source module.** The doc-to-code mapping is mechanical;
  if a code module gains complexity that warrants splitting, the spec
  splits too.
