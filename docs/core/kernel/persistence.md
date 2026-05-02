# Spec: SQLAlchemy persistence layer

> **Source module**: `hearth/kernel/persistence.py`
> **Parent ADRs**: [ADR-0006 (ORM contract)](../../architecture/0006-orm-contract.md).

## Status

Stub.

## Brief

The mapping layer that turns plugin-authored `Entity` declarations into
SQLAlchemy ORM models the kernel can query and persist with. Plugin
authors never see SQLAlchemy; this module is what makes that promise
hold.

Responsibilities:

- Translating `fields.*` declarations to SQLAlchemy `Mapped[...]` /
  `mapped_column(...)`.
- Translating `References(EntityClass)` to SQLAlchemy `relationship()`
  + foreign keys.
- Translating `fields.Value(ValueClass)` to columns (single-column
  JSON encoding by default; multi-column unpacking optional).
- Setting up the SQLAlchemy registry / declarative base.
- Connecting plugin entity declarations to the runtime SQLAlchemy
  metadata across plugin loads.

## Open questions

- **One SQLAlchemy registry, or per-plugin registries.**
- **`dataclass_transform` interaction with SQLAlchemy 2.x mapped
  classes.** Pydantic v2 + SQLAlchemy 2.x has solved patterns; we adopt
  one.
- **Column naming conventions.** Snake_case from class field names by
  default; opt-out for legacy schemas.
- **Index/unique constraint generation** from class-level kwargs.
- **Embedded `Value` column strategy.** JSON-encoded blob (default) vs.
  column-per-field unpacking. May be value-class opt-in.
