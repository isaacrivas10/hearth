# Spec: `Field` and the annotation-driven column model

> **Source module**: `hearth/fields.py`
> **Parent ADRs**: [ADR-0006 (ORM contract)](../architecture/0006-orm-contract.md) (with 2026-05-02 amendment), [ADR-0008 (built-in plugins)](../architecture/0008-built-in-plugins.md).

## What `fields` is

The kernel module plugin authors import to declare entity columns. The
kernel translates declarations into SQLAlchemy columns; plugin authors
never see SQLAlchemy.

Hearth's field model is **Pydantic-style**: the type annotation is the
single source of truth for the field's Python type and the database
column type. A generic `Field(...)` is used only when the declaration
needs additional kwargs.

## The single declarator

```python
from hearth import Field
```

`Field` is a thin wrapper over `pydantic.Field` that adds Hearth-specific
kwargs:

| kwarg | Source | Notes |
| --- | --- | --- |
| `default=...` | Pydantic | Constant default value. |
| `default_factory=...` | Pydantic | Callable returning a default — for mutable defaults. |
| `description="..."` | Pydantic | Documentation; flows into autogen API ref. |
| `alias=...` | Pydantic | Alternate name for serialization (e.g. JSON / outbox payloads). |
| `auto_now_add=True` | Hearth | Kernel sets the field at first save. Valid on `datetime` only. |
| `auto_now=True` | Hearth | Kernel sets the field on every save. Valid on `datetime` only. |
| `server_default=...` | Hearth | DB-side default (SQL expression). Translated to SQLAlchemy `server_default`. |

`Field()` is invoked only when one of these kwargs is needed. Otherwise
the declaration is just an annotation, optionally with a default value:

```python
from datetime import datetime
from hearth import Entity, Field, References

class Order(Entity, plugin="commerce"):
    customer: Customer = References()                    # required FK
    note: str = ""                                        # default ""
    placed_at: datetime = Field(auto_now_add=True)       # kernel-managed
    cancelled_at: datetime | None = None                  # nullable, default None
```

## Annotation-driven column dispatch

The kernel translates the type annotation into a database column. The
mapping:

| Annotation | Column | Notes |
| --- | --- | --- |
| `str` | TEXT | Use `Annotated[str, StringConstraints(max_length=N)]` for VARCHAR(N). |
| `int` | BIGINT | |
| `bool` | BOOLEAN | |
| `float` | DOUBLE PRECISION | |
| `Decimal` | NUMERIC | For currency-aware values, use `commons.Money` (a `Value`), not raw `Decimal`. |
| `datetime` | TIMESTAMPTZ | TZ-aware always; naive `datetime` rejected. |
| `dict \| list` | JSONB | (PostgreSQL); JSON elsewhere. |
| an enum subclass | ENUM | Native ENUM on PostgreSQL, VARCHAR + CHECK on SQLite. |
| a `Value` subclass | embedded column | JSON-encoded by default; see [`kernel/persistence.md`](kernel/persistence.md). |
| an `Entity` subclass (via `References()`) | foreign key | See [`references.md`](references.md). |

`T | None` makes the column NULL-allowed; `T` makes it NOT NULL. The
annotation is the source of truth for nullability — there is no
`nullable=True` kwarg.

## Required vs. optional at construction

Independently of nullability, the `default=` kwarg (or a default value
in the assignment) determines whether the field is required at Python
construction time:

| Annotation | Default | DB column | Required at construction? |
| --- | --- | --- | --- |
| `name: str` | (none) | NOT NULL | yes |
| `name: str = "foo"` | `"foo"` | NOT NULL | no — defaults to `"foo"` |
| `name: str \| None` | (none) | NULL allowed | yes — must pass `None` explicitly |
| `name: str \| None = None` | `None` | NULL allowed | no — defaults to `None` |

The middle case (nullable column, no default) is intentional. It forces
plugin authors to choose whether a field genuinely defaults to `None`
or is actually required. This matches Pydantic v2's semantics.

## Type-specific constraints via `Annotated`

Pydantic's `Annotated` metadata is the idiomatic place for type-specific
constraints:

```python
from typing import Annotated
from hearth import Entity, Field
from pydantic import StringConstraints

class Customer(Entity, plugin="commerce"):
    name: Annotated[str, StringConstraints(max_length=200)] = ""
    age: Annotated[int, Field(ge=0, lt=150)]
```

Hearth re-exports `StringConstraints` and a few common Pydantic
constraint helpers under `hearth.*`. Plugin authors who want
less-common constraints can `from pydantic import …` directly — that's
not forbidden (see [`primitives/value.md`](primitives/value.md) for the
"thinner wall" rationale around Pydantic).

## Validators

Use Pydantic's decorators for field- and model-level validation:

```python
from datetime import datetime
from hearth import Entity, model_validator

class Order(Entity, plugin="commerce"):
    placed_at: datetime
    delivered_at: datetime | None = None

    @model_validator(mode="after")
    def delivered_after_placed(self) -> "Order":
        if self.delivered_at and self.delivered_at < self.placed_at:
            raise ValueError("cannot deliver before placement")
        return self
```

Single-field validation, when available, should be pushed down into
the `Value` type rather than implemented per-entity. See
[`primitives/value.md`](primitives/value.md). `Entity`-level
`model_validator` is for **cross-field invariants** — things no single
value can express.

## What `fields` is *not*

There is no `fields.Text`, `fields.Integer`, `fields.Boolean`,
`fields.Timestamp`, `fields.Enum`, `fields.Json`, or `fields.Value()`.
These were redundant with type annotations and the redundancy is
removed. The annotation drives the column type; `Field(...)` carries
kwargs.

(The earlier draft of [ADR-0006](../architecture/0006-orm-contract.md)
listed type-specific field types; the 2026-05-02 amendment removed
`fields.Money` for ADR-0008 reasons, and this spec finishes the job by
collapsing the rest into a single `Field()` driven by the annotation.)

## Open questions

- **`server_default` shape.** Likely SQLAlchemy expression objects
  underneath, but plugin authors don't see SQLAlchemy — needs a
  Hearth-flavored wrapper or a small DSL.
- **Default factory ergonomics.** `Field(default_factory=lambda: [])`
  is verbose; consider sugar (e.g. `Field(default_factory=list)` works
  for the common case).
- **Sentinel for "unset" vs. `None`.** Pydantic v2 uses
  `PydanticUndefined`; whether plugin authors ever need to handle this
  distinction in entity code is TBD.
- **Strict mode opt-in.** Pydantic supports strict per-field via
  `Annotated[int, Strict()]`. Whether entities default strict (no
  implicit coercion) or lax (Pydantic default) is a slice decision.
