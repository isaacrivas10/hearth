# Spec: `Entity`

> **Source module**: `hearth/primitives/entity.py`
> **Parent ADRs**: [ADR-0005 (the seven primitives)](../../architecture/0005-primitives.md), [ADR-0006 (ORM contract)](../../architecture/0006-orm-contract.md) (with 2026-05-02 amendment), [ADR-0008 (built-in plugins)](../../architecture/0008-built-in-plugins.md).

## What `Entity` is

The kernel primitive for *things with identity and lifecycle*. Plugin
authors subclass `Entity` to declare new entity types. Instances are
persisted as rows that get updated; the kernel manages identity
assignment, lifecycle metadata, and registry lookup.

`Entity` instances are **mutable** but their mutations are not persisted
until an `Action` calls `await uow.save(entity)`. Entities are never read
or written outside an `Action`'s `UnitOfWork`.

## Declaration

```python
from hearth import Entity, Field, References
from hearth_commons import PhoneNumber       # opt-in via pip install hearth[commons]

class Customer(Entity, plugin="commerce"):
    phone: PhoneNumber                           # required; valid by construction
    name: str | None = None                       # nullable; defaults to None
    notes: str = Field(default="", description="freeform notes for the merchant")
```

Three things to note about the shape:

- **The annotation is the source of truth.** `phone: PhoneNumber`
  declares both the Python type and the column type — the kernel
  dispatches the column from the annotation. No `fields.Text(...)`,
  no `fields.Value(PhoneNumber)`. See [`fields.md`](../fields.md) for
  the full annotation-to-column dispatch table.
- **`Field(...)` is needed only when there's a kwarg.** A bare
  annotation with an optional default value handles the common case;
  `Field()` carries `default_factory`, `description`, `auto_now_add`,
  `server_default`, and similar.
- **There is no field-level validation on the entity.** `PhoneNumber`
  validates itself at construction (see
  [`value.md`](value.md)). Any entity with `phone: PhoneNumber` is
  guaranteed to have a valid phone — the entity has nothing to defend
  against.

The class-level keyword arguments configure per-entity options:

| kwarg | required? | meaning |
| --- | --- | --- |
| `plugin: str` | yes | The owning plugin's name. Drives registry, migration grouping, and cross-plugin reference resolution. |
| (reserved) | — | Future kwargs: `audit=`, `soft_delete=`, `index=`, etc. Each gets its own ADR before being added. |

Because `Entity` uses `typing.dataclass_transform` (PEP 681), type
annotations on fields are real field declarations. Plugin authors get
type-checked constructors and attribute access:

```python
customer = Customer(phone=PhoneNumber(raw="+50499998888"))   # type-checked
customer.phone        # PhoneNumber, not Any
customer.name         # str | None, not Any
```

## Fields

Field declarations are driven by the type annotation. The kernel
translates annotations into SQLAlchemy columns; plugin authors never
import from `sqlalchemy`. The full annotation-to-column dispatch table
lives in [`fields.md`](../fields.md). The summary:

| Annotation | Column |
| --- | --- |
| `str` | TEXT |
| `int` | BIGINT |
| `bool` | BOOLEAN |
| `datetime` | TIMESTAMPTZ (TZ-aware always) |
| an enum subclass | ENUM (or VARCHAR + CHECK on SQLite) |
| `dict \| list` | JSONB |
| a `Value` subclass | embedded column (JSON-encoded by default) |
| an `Entity` subclass via `References()` | foreign key |

`T | None` makes the column NULL-allowed; `T` makes it NOT NULL. The
annotation is the source of truth for both Python type and DB
nullability — no `nullable=True` kwarg.

Plugin-defined values plug in by their type alone — no wrapper needed:

```python
from hearth import Entity, References
from hearth_commons import Money, Address    # commons plugin

class Order(Entity, plugin="commerce"):
    customer: Customer = References()
    total: Money                              # required; embedded value
    shipping_address: Address | None = None    # optional; embedded value
```

Defaults: a field without a default value is required at construction.
Plugins should prefer explicit defaults to nullables when the column
has a sensible empty value (`name: str = ""` over `name: str | None = None`
when an empty string is meaningful).

## Identity

Every `Entity` has an `id` field, supplied by the kernel:

- Type: `EntityId` (a kernel-shipped value, ULID-backed by default for
  monotonic ordering and outbox-friendly sorting; see ADR-0007).
- Assigned at first persist (inside the first `await uow.save(entity)`),
  not at construction. Plugin authors do not assign `id` themselves.
- Equality and hash are identity-based: two `Customer` instances are
  equal iff their `id`s are assigned and equal. Unsaved instances are
  equal only to themselves (`id is None` until persisted).

```python
c1 = Customer(phone=PhoneNumber(raw="+50499998888"))
c2 = Customer(phone=PhoneNumber(raw="+50499998888"))
c1 == c2        # False — both unsaved
c1 == c1        # True

await uow.save(c1)
loaded = await uow.get(Customer, c1.id)
loaded == c1    # True — same id
```

See [`identifiers.md`](../identifiers.md) for the `EntityId` value type.

## Validation

Hearth uses Pydantic v2 for validation; the patterns are Pydantic's. See
[`value.md`](value.md) for the wider rationale (best-in-class library
underneath, narrow surface above).

There are three places validation can live, with a strict rule for
where each kind belongs:

- **Field-level validation belongs on the `Value` type, not the
  entity.** If `PhoneNumber` validates E.164 in its own `field_validator`,
  any entity with `phone: PhoneNumber` is automatically guaranteed to
  have a valid phone. There's nothing for the entity to check. See
  [`value.md`](value.md).
- **Cross-field invariants belong in `@model_validator(mode="after")`
  on the entity.** Things no single value can express — relationships
  between two fields, state-machine consistency, ordering constraints.
  Run by Pydantic at construction and on `validate_assignment`.
- **Cross-entity invariants belong in actions.** Anything that requires
  reading another entity from the database (uniqueness checks,
  reference integrity beyond FKs) lives in the action handler, where
  the `UnitOfWork` is in scope.

Cross-field invariants — the entity-level case:

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

State-machine consistency:

```python
from hearth import Entity, model_validator

class Order(Entity, plugin="commerce"):
    status: OrderStatus = OrderStatus.DRAFT
    paid_at: datetime | None = None

    @model_validator(mode="after")
    def paid_orders_have_paid_at(self) -> "Order":
        if self.status is OrderStatus.PAID and self.paid_at is None:
            raise ValueError("paid orders must record paid_at")
        return self
```

Pydantic's `@field_validator` is also available on entities for the
rare case where a single-field validation can't sensibly be pushed
into a Value type — but the default move is to push it down. If you
find yourself writing a `@field_validator` on an entity, ask whether
the field's type should carry the constraint instead.

The kernel does **not** expose `before_save` / `after_save` /
`before_delete` / `after_delete` hooks on `Entity`. The reasons:

- Cross-entity side effects belong in `Action`s, where the `UnitOfWork`
  is in scope and the operation is transactional.
- Reactions to commits belong in `Event` subscribers, which run
  post-commit with at-least-once delivery (ADR-0007).
- Mixing entity-level hooks with the outbox model would let plugin
  authors bypass the transactional contract by accident.

## Cross-plugin references

`References()` is the default form. The target entity is inferred from
the type annotation; the referenced class is imported and known at
type-check time:

```python
from commerce import Customer  # commerce is a declared dependency

class Conversation(Entity, plugin="whatsapp"):
    customer: Customer = References()                          # default FK
    primary: Customer = References(on_delete="cascade")        # FK with kwargs
```

The string form `customer: "Customer"` is reserved for forward
references within the same module — circular references between two
entities defined in the same file. Cross-plugin references that use a
string-form annotation are rejected at startup; this is intentional.
Use the import.

`on_delete` accepts:

- `"restrict"` (default) — kernel raises if the referenced entity is
  deleted while references exist.
- `"cascade"` — referencing entities are deleted when the referenced
  entity is deleted.
- `"set_null"` — references become `None`. The reference field must be
  nullable.

See [`references.md`](../references.md) for the full `References` spec.

## Persistence interface

Plugins do not call `entity.save()`, `entity.delete()`, or query methods
on entity classes. All persistence goes through `UnitOfWork`, passed into
actions:

```python
class CreateCustomer(Action):
    phone: PhoneNumber

    async def handle(self, uow: UnitOfWork, identity: Identity) -> Customer:
        customer = Customer(phone=self.phone)
        await uow.save(customer)
        uow.emit(CustomerCreated(customer_id=customer.id))
        return customer
```

The full `UnitOfWork` interface is specified in
[`unit-of-work.md`](../unit-of-work.md). The minimum surface entities
need:

- `await uow.get(Customer, id)` — load by id, raises if missing.
- `await uow.find_one(Customer, **filters)` — narrow lookup, returns
  `Customer | None`.
- `await uow.save(customer)` — persist (insert or update).
- `await uow.delete(customer)` — delete.
- `uow.emit(event)` — append to in-transaction outbox buffer (sync; no
  I/O).

General queries — reporting, presentation, complex joins — go through
`View` primitives, not `UnitOfWork`. See ADR-0006 §"Read paths" and
[`view.md`](view.md).

## What plugins cannot do

- Import from `sqlalchemy` directly.
- Access `Session`, `engine`, or `Connection` objects.
- Execute raw SQL.
- Declare `relationship()` or `mapped_column(...)`.
- Use SQLAlchemy event listeners or query API.
- Override `Entity.__init_subclass__`, `Entity.__class_getitem__`, or
  other kernel-internal class-creation hooks.
- Subclass another plugin's entity to add fields. Plugins compose by
  registering new actions and subscribing to events; they do not extend
  by inheritance ([ADR-0003](../../architecture/0003-plugin-model.md):
  "registration not inheritance").

## Open questions

The slice will force decisions on these. Listed so the spec is honest
about what's deferred.

- **Soft delete.** Likely a class-level kwarg
  (`class Order(Entity, plugin="commerce", soft_delete=True)`) that adds
  a `deleted_at: datetime | None` field and routes `uow.delete()`
  through it. Deferred until a plugin needs it.
- **Indexes and unique constraints.** Class-level kwargs in the long run
  (`indexes=[("phone",)]`, `unique=[("phone",)]`); exact shape TBD.
- **Inheritance between plugin entities.** A plugin's `PremiumCustomer`
  inheriting from `Customer` to add fields — currently rejected by
  ADR-0003. Confirm in the slice; revisit only if the pain is real.
- **Optimistic concurrency.** A `version: int` field that increments on
  every save and gets checked on update. Likely a kwarg
  (`optimistic_locking=True`) but not in the slice.
- **Polymorphic entities (single-table inheritance).** Probably never.
  Composition via references and views covers what STI does, without
  the schema lock-in. Listed here so the rejection is on record.
- **Whether `EntityId` should be exposed as a typed alias per entity**
  (`CustomerId = NewType('CustomerId', EntityId)`) for stronger typing
  on `References` and action commands. Likely yes; deferred to
  [`identifiers.md`](../identifiers.md).
