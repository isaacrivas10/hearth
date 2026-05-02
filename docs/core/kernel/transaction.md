# Spec: kernel transaction management

> **Source module**: `hearth/kernel/transaction.py`
> **Parent ADRs**: [ADR-0006](../../architecture/0006-orm-contract.md), [ADR-0007](../../architecture/0007-transactional-contract.md).

## Status

Stub.

## Brief

Implements `UnitOfWork` (the plugin-facing Protocol in
[`../unit-of-work.md`](../unit-of-work.md)) on top of an async
SQLAlchemy session. Owns transaction begin/commit/rollback,
identity-map management, dirty tracking, and the in-transaction outbox
buffer that flushes on commit.

## Open questions

- **Identity-map scope** (per-UoW vs. shared) and lifetime.
- **In-transaction event buffer.** How `uow.emit` queues events and
  flushes them atomically with entity changes — single SQL transaction
  with the outbox row writes appended just before commit.
- **Subscriber UoWs.** Subscribers run in their own UoW; how the
  kernel orchestrates this (subscriber dispatcher creates fresh UoW per
  delivery attempt, with retry semantics).
- **Connection pool sizing, lifetime.** Single-tenant per ADR-0002, so
  no per-tenant isolation needed.
- **Async session lifecycle** with FastAPI / Starlette / job worker
  contexts.
