# Spec: schema migrations

> **Source module**: `hearth/kernel/migrations.py`
> **Parent ADRs**: [ADR-0006 (ORM contract)](../../architecture/0006-orm-contract.md).

## Status

Stub. The exact mechanism (Alembic-backed autogenerate vs. a
kernel-native schema journal) is an open decision — ADR-0006
§"Migrations" explicitly defers it.

## Brief

Schema migration generation, ordering, and application. Plugins do not
write migration files; the kernel computes a versioned schema from
their entity declarations and applies migrations on startup or via an
explicit `migrate` command.

Migration ordering follows the plugin dependency graph: a plugin
referencing another plugin's entity migrates after the referenced
plugin.

## Open questions

- **The big one: Alembic-backed autogenerate vs. kernel-native schema
  journal.** Alembic is mature but couples the migration story to
  SQLAlchemy directly; a journal gives the kernel more control but is
  more code.
- **Plugin-level schema versioning.** Each plugin tracks its own
  version vs. one global version.
- **Online migration (zero-downtime) story.** Initially out of scope;
  document the rebuild-and-redeploy pattern for now.
- **Rollback semantics.** Supported, or always forward?
- **`fields.Value(ValueClass)` evolution.** What happens when an embedded
  value's shape changes — is it a destructive migration, a json
  reshape, or a kernel-managed rewrite?
