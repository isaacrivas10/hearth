# Spec: schema migrations

> **Source modules**: `hearth/kernel/migrations/`, `hearth/migrations/`
> **Parent ADRs**: [ADR-0006](../../architecture/0006-orm-contract.md) (with 2026-05-18 amendment).
> **Design**: [docs/superpowers/specs/2026-05-18-schema-migrations-design.md](../../superpowers/specs/2026-05-18-schema-migrations-design.md)

## Status

Implemented. Source-of-truth design lives in the linked design spec.

## Brief

The kernel orchestrates per-plugin schema evolution using Alembic as the diff/DDL/discovery engine. Plugin authors ship standard Alembic revision files (with a `HEARTH_UPGRADE_OPS` classification artifact) and optional `data_upgrade(uow)` hooks for entity-typed seed data and backfills. Operators apply via `hearth db migrate` (interactive confirmation for destructive ops) or boot policy via `HEARTH_DB_MIGRATE`. See the design spec for full detail.
