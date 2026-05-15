# Spec: `Field` and the annotation-driven column model

> **Source module**: `hearth/fields.py`
> **Parent ADRs**: [ADR-0006 (ORM contract)](../architecture/0006-orm-contract.md) (with 2026-05-02 amendment), [ADR-0008 (built-in plugins)](../architecture/0008-built-in-plugins.md).

## What `fields` is

Most entity fields don't need anything from this module — a bare type
annotation plus an optional default value is enough. The kernel's
metaclass infers the SQLAlchemy column type from the annotation and
generates a `mapped_column(...)` descriptor automatically.

`Field(...)` is provided for cases where a kwarg the annotation can't
carry is needed (kernel-managed defaults, descriptions, server-side
defaults).

## The `Field` declarator

```python
from hearth import Field
```

`Field()` returns an opaque marker the Entity metaclass turns into a
SQLAlchemy `mapped_column(...)` with the right column type from the
annotation.

```python
def Field(
    *,
    default: Any = ...,
    default_factory: Callable[[], Any] | None = None,
    description: str | None = None,
    auto_now_add: bool = False,
    auto_now: bool = False,
    server_default: Any = None,
    init: bool = True,
    primary_key: bool = False,
) -> Any: ...
```

| kwarg | Source | Notes |
| --- | --- | --- |
| `default=...` | Pydantic | Constant default value. |
| `default_factory=...` | Pydantic | Callable returning a default — for mutable defaults. |
| `description="..."` | Pydantic | Documentation; flows into autogen API ref. |
| `auto_now_add=True` | Hearth | Kernel sets the field at first save (datetime). |
| `auto_now=True` | Hearth | Kernel sets the field on every save (datetime). |
| `server_default=...` | Hearth | DB-side default (SQL expression). |

Use `Field(...)` only when one of these kwargs is needed. Otherwise
the declaration is just an annotation, optionally with a default value:

```python
from datetime import datetime
from hearth import Entity, Field

class Order(Entity, plugin="commerce"):
    customer: Customer                                    # required FK
    note: str = ""                                         # default ""
    placed_at: datetime = Field(auto_now_add=True)        # kernel-managed
    cancelled_at: datetime | None = None                   # nullable, default None
```

## Annotation-driven column dispatch

The kernel translates the type annotation into a database column. The
mapping (defined in `hearth/kernel/persistence.py:column_for_annotation`):

| Annotation | Column | Notes |
| --- | --- | --- |
| `str` | TEXT | |
| `int` | BIGINT | |
| `bool` | BOOLEAN | |
| `float` | DOUBLE PRECISION | |
| `datetime` | TIMESTAMPTZ | TZ-aware; naive `datetime` rejected by Pydantic. |
| an enum subclass | ENUM | (Native ENUM on PostgreSQL; VARCHAR + CHECK on SQLite.) |
| `dict \| list` | JSONB | |
| a `Value` subclass | embedded JSONB | Wrapped with a per-class `TypeDecorator` that calls `model_dump`/`model_validate`. |
| `EntityId` | TEXT | Currently — proper FK semantics land with `References()`. |

`T | None` makes the column NULL-allowed; `T` makes it NOT NULL.

## Required vs. optional at construction

Independently of nullability:

| Annotation | Default | DB column | Required at construction? |
| --- | --- | --- | --- |
| `name: str` | (none) | NOT NULL | yes |
| `name: str = "foo"` | `"foo"` | NOT NULL | no |
| `name: str \| None` | (none) | NULL allowed | yes — must pass `None` explicitly |
| `name: str \| None = None` | `None` | NULL allowed | no |

The middle case (nullable, no default) is intentional — it forces the
plugin author to choose whether `None` is the implicit default or
whether the field is required. This matches Pydantic v2.

## Embedding `Value` subclasses

A field annotated as a `Value` subclass (like `PhoneNumber`) is stored
as a JSONB column with automatic round-trip through Pydantic's
`model_dump` / `model_validate`. The kernel builds the `TypeDecorator`
once per Value class. Plugin authors don't see this:

```python
class Customer(Entity, plugin="commerce"):
    phone: PhoneNumber                  # JSONB column, round-trips PhoneNumber

# In storage:
#   phone column = '{"raw": "+50499998888"}'::jsonb
# At load:
#   customer.phone is a PhoneNumber instance
```

The `Value` subclass's `field_validator` runs at construction, so
`PhoneNumber("not-a-phone")` raises before it ever reaches the
database.

## Validators

Use Pydantic's decorators on `Value` subclasses for field validation,
and `Entity.validate()` for cross-field invariants on entities:

```python
from hearth import Value, field_validator

class PhoneNumber(Value):
    raw: str

    @field_validator("raw")
    @classmethod
    def must_e164(cls, v: str) -> str:
        if not v.startswith("+"):
            raise ValueError("must be E.164")
        return v
```

```python
from hearth import Entity

class Order(Entity, plugin="commerce"):
    placed_at: datetime
    delivered_at: datetime | None = None

    def validate(self) -> None:
        if self.delivered_at and self.delivered_at < self.placed_at:
            raise ValueError("cannot deliver before placement")
```

See [`primitives/value.md`](primitives/value.md) and
[`primitives/entity.md`](primitives/entity.md).

## What `fields` is *not*

There are no `fields.Text`, `fields.Integer`, `fields.Boolean`,
`fields.Timestamp`, `fields.Enum`, `fields.Json`, or `fields.Value()`
helpers. The annotation is the source of truth; field-type-specific
helpers would be redundant.

(The earlier draft of [ADR-0006](../architecture/0006-orm-contract.md)
listed type-specific field functions; the 2026-05-02 amendment removed
`fields.Money` for ADR-0008 reasons, and the slice migration finished
the cleanup by collapsing the rest into a single `Field()` driven by
the annotation.)

## Escape hatch: direct `mapped_column`

The metaclass detects when a plugin author has written `mapped_column(...)`
explicitly and leaves it alone. Use this when you need a column type the
annotation-dispatch table doesn't cover (e.g., an ARRAY type, a custom
SQLAlchemy `TypeDecorator`):

```python
from sqlalchemy import ARRAY, Integer
from sqlalchemy.orm import mapped_column

class Histogram(Entity, plugin="analytics"):
    buckets: list[int] = mapped_column(ARRAY(Integer))   # explicit; metaclass leaves it
```

This *does* leak `sqlalchemy` into plugin code — reach for it only when
the SDK truly can't express what you need. If an exotic case starts
recurring, that's a signal to extend the SDK's column dispatch instead
of normalizing the leak.

## Open questions

- **`server_default` shape.** Today it's passed straight through to
  `mapped_column(server_default=...)` — plugin authors may end up
  needing SQLAlchemy expression objects. Worth wrapping when this
  starts hurting.
- **Default factory ergonomics.** `Field(default_factory=lambda: [])`
  is verbose; consider sugar (or just rely on `default_factory=list`).
- **Type-specific constraints via `Annotated`.** `Annotated[str, MaxLength(100)]`
  works for Pydantic validation but isn't yet wired into column-type
  selection (e.g., VARCHAR(N) vs TEXT). Land when a plugin needs it.
- **Strict mode opt-in.** Pydantic supports per-field `Annotated[int, Strict()]`.
  Whether entities default strict (no implicit coercion) or lax
  (Pydantic default) is a slice decision; current default is lax.
