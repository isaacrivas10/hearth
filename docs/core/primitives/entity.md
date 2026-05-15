# Spec: `Entity`

> **Source module**: `hearth/primitives/entity.py`
> **Parent ADRs**: [ADR-0005 (the seven primitives)](../../architecture/0005-primitives.md), [ADR-0006 (ORM contract)](../../architecture/0006-orm-contract.md) (with 2026-05-02 amendment), [ADR-0008 (built-in plugins)](../../architecture/0008-built-in-plugins.md).

## What `Entity` is

The kernel primitive for *things with identity and lifecycle*. Plugin
authors subclass `Entity` to declare new entity types. Instances are
mapped to SQLAlchemy ORM rows; the kernel manages identity assignment,
column generation, and the unit-of-work pattern.

`Entity` instances are mutable. Mutations to instances loaded inside a
transaction auto-flush on commit (SQLAlchemy ORM dirty tracking).
Entities are never read or written outside an `Action`'s `UnitOfWork`.

## Declaration

```python
from hearth import Entity, model_validator
from hearth_commons import PhoneNumber

class Customer(Entity, plugin="commerce"):
    phone: PhoneNumber                # required Value
    name: str | None = None            # nullable, default None
    note: str = ""                     # default empty string
```

Things to note:

- **The annotation is the source of truth.** `phone: PhoneNumber`
  declares both the Python type and the column type — the kernel's
  metaclass dispatches the column from the annotation. There is no
  `Mapped[T]` wrapper, no `mapped_column(...)` call, no `sqlalchemy`
  import.
- **`__tablename__` is auto-generated** from the plugin name + class
  name (`commerce__customer` for the example above). Plugins can
  override by declaring `__tablename__ = "..."` explicitly.
- **`id` is provided automatically** by `Entity` itself — see "Identity"
  below. Plugin authors don't declare it.
- **Construction is keyword-only** (`Customer(phone=..., name=...)`) —
  matches Pydantic's idiom and side-steps dataclass field-ordering
  rules around defaults.

## Fields

Field declarations are driven by the type annotation. The kernel's
metaclass walks `__annotations__` at class-creation time and
synthesises a SQLAlchemy `mapped_column(...)` with the right column
type for each field. The annotation-to-column mapping:

| Annotation | Column |
| --- | --- |
| `str` | TEXT |
| `int` | BIGINT |
| `bool` | BOOLEAN |
| `float` | DOUBLE PRECISION |
| `datetime` | TIMESTAMPTZ (TZ-aware always) |
| an enum subclass | ENUM (or VARCHAR + CHECK on SQLite) |
| `dict \| list` | JSONB |
| a `Value` subclass | embedded column (JSON-encoded via TypeDecorator) |
| `EntityId` | TEXT |

`T | None` makes the column NULL-allowed; `T` makes it NOT NULL. The
annotation is the source of truth for both Python type and DB
nullability — there is no `nullable=True` kwarg.

### When you do need `Field(...)`

`Field(...)` is for kwargs the annotation can't carry — kernel-managed
defaults, descriptions, server-side defaults. See [`fields.md`](../fields.md).

```python
from datetime import datetime
from hearth import Entity, Field

class Order(Entity, plugin="commerce"):
    customer: Customer
    placed_at: datetime = Field(auto_now_add=True)
    note: str = Field(default="", description="freeform notes")
```

For the typical case (no kwargs needed), bare annotation + Python
default is enough.

## Identity

Every entity inherits an `id: EntityId` field from `Entity`. The kernel
assigns the id at construction (via `default_factory=EntityId.new`),
so plugin code can reference `customer.id` immediately — useful for
emitting events:

```python
class CreateCustomer(Action):
    phone: PhoneNumber

    async def handle(self, uow, identity):
        customer = Customer(phone=self.phone)   # id auto-generated here
        await uow.save(customer)
        uow.emit(CustomerCreated(customer_id=customer.id))   # use it now
        return customer
```

`EntityId` is a ULID-backed string subclass. See
[`identifiers.md`](../identifiers.md).

Equality and hashing are identity-based: two entities of the same type
are equal iff they share an id. Two `Customer` instances constructed
in the same process get distinct auto-generated ids and are therefore
not equal — matching the natural intuition that "two new customers are
different customers."

```python
a = Customer(phone=phone1)
b = Customer(phone=phone1)
assert a.id != b.id        # auto-generated, distinct
assert a != b              # identity-based equality
assert a in {a}            # hashable
```

## Validation

Hearth uses Pydantic v2 for validation. Three places validation lives,
each tied to the lowest layer that can express the constraint:

- **Field-level validation** belongs on the `Value` type. If
  `PhoneNumber` validates E.164 in its own `field_validator`, any
  entity with `phone: PhoneNumber` is automatically guaranteed to have
  a valid phone — there's nothing for the entity to check.
- **Cross-field invariants** belong in `Entity.validate()`. Things no
  single value can express (relationships between two fields, state-
  machine consistency, ordering constraints).
- **Cross-entity invariants** belong in actions. Anything requiring a
  read of another entity (uniqueness checks, FK integrity beyond what
  the DB enforces) lives in the action handler with the UoW.

Cross-field invariants — the entity-level case:

```python
from datetime import datetime
from hearth import Entity

class Order(Entity, plugin="commerce"):
    placed_at: datetime
    delivered_at: datetime | None = None

    def validate(self) -> None:
        if self.delivered_at is not None and self.delivered_at < self.placed_at:
            raise ValueError("cannot deliver before placement")
```

State-machine consistency:

```python
class Order(Entity, plugin="commerce"):
    status: OrderStatus = OrderStatus.DRAFT
    paid_at: datetime | None = None

    def validate(self) -> None:
        if self.status is OrderStatus.PAID and self.paid_at is None:
            raise ValueError("paid orders must record paid_at")
```

`validate()` runs in `__post_init__` after type checking. Type checking
itself is done via `pydantic.TypeAdapter(annotation).validate_python(value)`
for each field — so the annotation's type contract is enforced at
construction.

Mutation is also validated: assigning `customer.name = 123` raises
`ValidationError` because the annotation is `str | None`. SQLAlchemy
ORM load bypasses the validation hook (it uses descriptor protocol
internally), so this only catches plugin-author code paths.

## Persistence interface

Plugin authors don't call `entity.save()` or `entity.delete()` directly.
All persistence goes through `UnitOfWork`, passed into actions:

```python
class CreateCustomer(Action):
    phone: PhoneNumber

    async def handle(self, uow: UnitOfWork, identity: Identity) -> Customer:
        customer = Customer(phone=self.phone)
        await uow.save(customer)
        uow.emit(CustomerCreated(customer_id=customer.id))
        return customer
```

The full `UnitOfWork` interface is in [`unit-of-work.md`](../unit-of-work.md):

- `await uow.get(Customer, id)` — load by id, raises `EntityNotFoundError` if missing.
- `await uow.find_one(Customer, **filters)` — narrow lookup.
- `await uow.save(customer)` — persist (insert for new, update for tracked, upsert for re-attached).
- `await uow.delete(customer)` — delete.
- `uow.emit(event)` — append to in-transaction outbox buffer.

Mutations to entities loaded via `get`/`find_one` are auto-tracked by
SQLAlchemy and flushed on commit; you don't need to call `save()` again.

General queries — reporting, presentation, complex joins — go through
`View` primitives, not `UnitOfWork`. See ADR-0006 §"Read paths" and
[`view.md`](view.md).

## Implementation: SQLAlchemy + Pydantic

`Entity` is built on `MappedAsDataclass + DeclarativeBase` (SQLAlchemy
2.x's dataclass-style ORM mapping) with Pydantic's `TypeAdapter` for
validation. The kernel's `_EntityMeta` metaclass walks plain
annotations and synthesises `mapped_column(...)` descriptors with the
right column types — including a custom `TypeDecorator` for embedding
Pydantic `Value` subclasses through JSONB.

This deviates from the literal "option 2" we initially agreed on
(`pydantic.dataclasses.dataclass + MappedAsDataclass`), which turns
out to be unworkable in current versions of pydantic + sqlalchemy:
pydantic's dataclass decorator hits `Generic[cls.__parameters__]` with
empty parameters because `DeclarativeBase` is itself `Generic`. The
shipped path bypasses that by combining SQLAlchemy's dataclass-mapped
class with Pydantic's standalone `TypeAdapter`.

Plugin authors don't see any of this; they just write classes.

## What plugins cannot do

- Import from `sqlalchemy` directly.
- Access `Session`, `engine`, or `Connection` objects.
- Execute raw SQL.
- Declare `Mapped[T]` annotations or `mapped_column(...)` directly
  (allowed as an escape hatch when the annotation can't express what
  you need; the metaclass detects existing `mapped_column` and leaves
  it alone — but reach for the SDK first).
- Use SQLAlchemy event listeners or query API.
- Subclass another plugin's entity to add fields. Plugins compose by
  registering new actions and subscribing to events; they do not
  extend by inheritance ([ADR-0003](../../architecture/0003-plugin-model.md):
  "registration not inheritance").

## Open questions

The slice landed enough to validate the shape; these still need
attention as more plugins land:

- **`References()` for cross-entity FKs.** Works today via direct
  annotation (`customer: Customer`) producing a JSONB-embedded value,
  but proper FKs (with on_delete cascade/restrict/set_null) need the
  metaclass to detect Entity-typed annotations and emit
  `mapped_column(ForeignKey(...))`. Lands when the second entity
  references the first.
- **Soft delete.** Likely a class-level kwarg
  (`class Order(Entity, plugin="commerce", soft_delete=True)`) that
  adds a `deleted_at: datetime | None` field and routes `uow.delete()`
  through it.
- **Indexes and unique constraints.** Class-level kwargs
  (`indexes=[("phone",)]`, `unique=[("phone",)]`); exact shape TBD.
- **Inheritance between plugin entities.** Currently rejected by
  ADR-0003. Confirm in the slice; revisit only if pain is real.
- **Optimistic concurrency.** A `version: int` field that increments on
  every save and gets checked on update.
- **Pydantic `@model_validator(mode="after")` support.** Currently we
  use a `validate()` method instead. The decorator-based form is
  Pydantic's idiom but doesn't auto-fire on a non-BaseModel class;
  could be added by detecting `PydanticDescriptorProxy` and calling it
  manually in `__post_init__` if plugin authors prefer it.
- **Migration generation.** Today we use `metadata.create_all`; real
  Alembic-style migrations are deferred to [`kernel/migrations.md`](../kernel/migrations.md).
