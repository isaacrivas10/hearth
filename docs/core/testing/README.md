# Testing

Specs for `hearth/testing/` — the in-process test harness plugins use
to write fast, dependency-free tests.

The harness is a first-class part of the SDK
([ADR-0004](../../architecture/0004-sdk-first.md) §3): plugin tests
must run without Postgres, without Docker, without the kernel HTTP
server, in milliseconds.

## Layout

- [`harness.md`](harness.md) — in-memory `UnitOfWork`, in-memory event
  bus / outbox, plugin-set fixtures.
