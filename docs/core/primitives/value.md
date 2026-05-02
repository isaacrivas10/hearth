# Spec: `Value`

> **Source module**: `hearth/primitives/value.py`
> **Parent ADRs**: [ADR-0005 (primitives)](../../architecture/0005-primitives.md) (with 2026-05-02 amendment), [ADR-0008 (built-in plugins)](../../architecture/0008-built-in-plugins.md).

## What `Value` is

The kernel primitive for embedded immutable types — "no identity,
immutable, structural equality." Plugins subclass `Value` to declare
value types whose instances are compared by structure (not identity),
are immutable after construction, and are embedded into entities
through type annotation rather than persisted as their own rows.

## Implementation: Pydantic v2

`hearth.Value` is a `pydantic.BaseModel` with `frozen=True` and
`validate_assignment=True`. Plugin authors writing values use Pydantic's
validation idioms directly.

```python
# hearth/primitives/value.py
from pydantic import BaseModel, ConfigDict

class Value(BaseModel):
    """Kernel primitive: no identity, immutable, embeddable."""
    model_config = ConfigDict(
        frozen=True,
        validate_assignment=True,
        extra="forbid",
    )
```

This is the same strategy ADR-0006 takes with SQLAlchemy: best-in-class
library underneath, exposed through `hearth.*` as the canonical import
path for stability. **Unlike SQLAlchemy, Pydantic has no escape hatches
that break kernel contracts** — a plugin importing `pydantic` directly
isn't dangerous, just coupled to Pydantic's release cycle. The wall is
thinner: `hearth.*` is the recommended path, but `import pydantic` is
allowed.

## Declaration

Plugin authors subclass `Value`. Validation patterns are Pydantic's:

```python
# hearth_commons/phone.py — what plugin authors write
import re
from hearth import Value, field_validator

E164 = re.compile(r"^\+[1-9]\d{1,14}$")

class PhoneNumber(Value):
    raw: str

    @field_validator("raw")
    @classmethod
    def must_be_e164(cls, v: str) -> str:
        if not E164.fullmatch(v):
            raise ValueError("must be E.164 format (e.g., +50499998888)")
        return v
```

```python
PhoneNumber(raw="+50499998888")    # OK
PhoneNumber(raw="not-a-phone")     # raises pydantic.ValidationError at construction
```

The pattern: **invalid inputs cannot be represented as a `PhoneNumber`
instance.** Any entity with `phone: PhoneNumber` is therefore guaranteed
to have a valid phone — no `is_valid()` method on the value, no
defensive checks at the entity level. This is "make illegal states
unrepresentable" applied to the value layer.

## What the kernel ships

Per [ADR-0008](../../architecture/0008-built-in-plugins.md), the kernel
ships:

- The `Value` base class itself (the contract; subclass of
  `pydantic.BaseModel` with frozen config).
- `EntityId` — see [`../identifiers.md`](../identifiers.md).
- `Reference` — see [`../references.md`](../references.md).

Domain values (`Money`, `PhoneNumber`, etc.) live in plugins (`commons`
for shared vocabulary; domain plugins for domain-specific values).

## What plugin authors import

The canonical path is through `hearth.*`:

```python
from hearth import Value, field_validator, model_validator
# all three are re-exports of pydantic.BaseModel (frozen)
# and pydantic's validator decorators
```

Direct `import pydantic` is allowed but discouraged. The `hearth.*`
namespace is the stable contract; future kernel evolution may swap
underlying implementations, but `hearth.*` will keep its shape across
versions. Plugins that import from Pydantic directly couple themselves
to Pydantic's release cycle.

The kernel re-exports specifically:

- `Value` — `pydantic.BaseModel` with frozen config.
- `field_validator`, `model_validator`, `computed_field` — Pydantic's
  decorators, unchanged.
- `Field` — see [`../fields.md`](../fields.md). Hearth's `Field()` is
  a thin wrapper over `pydantic.Field` that adds a few kernel-specific
  kwargs (`auto_now_add`, `auto_now`, `server_default`).
- A small set of `Annotated` constraint helpers (`StringConstraints`,
  etc.) re-exported from Pydantic for ergonomics.

## Equality and hashing

Pydantic provides structural equality and hashing for free. Two
`PhoneNumber` instances with the same `raw` are equal and hash the
same:

```python
PhoneNumber(raw="+50499998888") == PhoneNumber(raw="+50499998888")  # True
hash(PhoneNumber(raw="+50499998888"))                                # works (frozen)
{PhoneNumber(raw="+50499998888")}                                    # works in sets
```

This is the contract `Value` requires; Pydantic's frozen config
delivers it.

## JSON serialization

Values serialize to JSON via Pydantic's `.model_dump_json()` /
`.model_validate_json()`. This is what the outbox uses for event
payloads and what API plugins use for serialization.

```python
phone = PhoneNumber(raw="+50499998888")
phone.model_dump()                              # {"raw": "+50499998888"}
phone.model_dump_json()                         # '{"raw":"+50499998888"}'
PhoneNumber.model_validate_json('{"raw":"+50499998888"}')   # round-trip
```

Plugin authors don't need to write `__eq__`, `__hash__`, `__repr__`,
or serialization code — Pydantic provides all of it.

## Immutability and "modification"

Values are frozen. To "change" a value, construct a new one with
Pydantic's `model_copy`:

```python
phone = PhoneNumber(raw="+50499998888")
fixed = phone.model_copy(update={"raw": "+50499998999"})  # new instance
phone.raw                                                  # unchanged
```

Direct attribute assignment raises:

```python
phone.raw = "something"   # raises ValidationError (validate_assignment) or FrozenInstanceError
```

## Anti-patterns

- **Don't write `is_valid()` methods.** If a value can be invalid, your
  validators aren't doing their job. Construction is the validation
  gate; validation that runs later is too late.
- **Don't expose mutability.** `frozen=True` enforces immutability; do
  not work around it. Use `model_copy(update=...)` to produce a new
  instance.
- **Don't put cross-value logic on a value.** Values are leaves in the
  data graph. Logic that depends on another value or entity belongs
  in actions or in `Entity` model validators.

## Open questions

- **Embedding storage.** Whether the persistence layer JSON-encodes a
  value into a single column (default) or unpacks it into per-field
  columns. Likely value-class opt-in via a class-level kwarg. Detail
  in [`../kernel/persistence.md`](../kernel/persistence.md).
- **Custom Pydantic types.** When plugins need types Pydantic doesn't
  ship (e.g., a `Decimal`-with-currency for `Money`), they author
  Pydantic types directly. The pattern is well-documented; we just
  need to confirm it works through our re-export layer.
- **Strict vs. lax validation modes.** Pydantic supports both; Hearth
  defaults TBD. Strict (no implicit type coercion) is probably right
  for entity persistence; lax is better for API input parsing. May
  end up depending on context.
