# Spec: outbox table and dispatcher

> **Source module**: `hearth/kernel/outbox.py`
> **Parent ADRs**: [ADR-0007 (transactional contract)](../../architecture/0007-transactional-contract.md).

## Status

Stub. ADR-0007 §"Implementation sketch" is the starting point.

## Brief

The outbox table holds events emitted inside transactions, persisted
atomically with entity mutations. A separate dispatcher worker reads
the outbox in commit order and delivers events to subscribers with
at-least-once semantics and exponential-backoff retry.

The dispatcher is itself implemented as a `Job` (per ADR-0005).

## Sketched schema

(From ADR-0007 §"Implementation sketch" — non-binding.)

```
events (
  id              ULID primary key,
  aggregate_type  text,
  aggregate_id    text,
  event_type      text,
  payload         jsonb,
  actor           jsonb,         -- serialized Identity
  causation_id    ULID nullable,
  correlation_id  ULID nullable,
  created_at      timestamptz,
  dispatched_at   timestamptz nullable,
  attempts        integer default 0
)
```

## Open questions

- **Polling vs. LISTEN/NOTIFY.** Postgres LISTEN/NOTIFY for low latency,
  polling fallback for cross-database support. Likely both.
- **Per-subscriber dedup table.** The `Once` utility ADR-0007 mentions —
  table shape, key derivation.
- **Replay semantics.** Filter language, rewind to a specific
  subscriber.
- **Dead-letter shape.** Parked column flag, separate table, alerting
  hook.
- **Concurrency.** Row-level locks, advisory locks, partition-by-aggregate.
- **Retention.** When (if ever) to delete dispatched events.
