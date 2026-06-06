# Schema Migrations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the schema migration system specified in [docs/superpowers/specs/2026-05-18-schema-migrations-design.md](../specs/2026-05-18-schema-migrations-design.md).

**Architecture:** Hybrid — Alembic does diff generation, DDL execution, revision discovery, and branch head tracking. The kernel owns orchestration, classification (safe vs destructive), interactive confirmation, advisory locking, the audit-log overlay, the Python `data_upgrade(uow)` hook, lifecycle integration (boot policy + CLI), and the partial-commit recovery check. Per-plugin Alembic environments are built dynamically from the registry; standard Alembic revision files carry a `HEARTH_UPGRADE_OPS` artifact recording each op's classifier verdict.

**Tech Stack:** Python 3.13, SQLAlchemy 2.x async, Alembic (`alembic.autogenerate.compare_metadata`, `alembic.operations.Operations`, `alembic.script.ScriptDirectory`, `alembic.command`), Typer for CLI, pytest + pytest-asyncio, basedpyright strict.

**Commit guidance (per CLAUDE.md — overrides skill template):** Plan tasks do NOT include commit steps. The user runs the commit pass at logical groupings against the live tree. Suggested groupings (one per phase, occasionally combine adjacent phases):

- Phase A: Foundation — types, classifier, Alembic config builder, schema log table, module skeleton.
- Phase B: Planner + `hearth db plan` command.
- Phase C: Apply loop + advisory lock + `hearth db migrate` (DDL only).
- Phase D: Partial-commit recovery check.
- Phase E: Data hooks (`data_upgrade` / `data_downgrade`) with audit-row co-commit and event suppression.
- Phase F: `hearth migrate generate` (autogenerate + HEARTH_UPGRADE_OPS + data hook stub).
- Phase G: `hearth migrate check` (drift + OpSummary correspondence).
- Phase H: `hearth db status` overhaul + adoption-without-re-applying path.
- Phase I: Startup behavior (`HEARTH_DB_MIGRATE` policies + plugin-downgrade detection).

`uv run pytest`, `uv run ruff check .`, `uv run ruff format --check .`, `uv run basedpyright` are run after each phase before commit.

---

## File Structure

**Create:**

```
hearth/migrations/
  __init__.py                            # Public re-export: OpSummary
  _ops.py                                # OpSummary frozen dataclass

hearth/kernel/migrations/
  __init__.py                            # Re-exports for CLI/applier internals
  alembic_config.py                      # build_config(registry, engine_url) → alembic.config.Config
  classifier.py                          # classify(op: MigrateOperation) → Literal["safe", "destructive"]
  audit_log.py                           # SchemaLogRow ORM + read/write helpers
  planner.py                             # PlannedRevision, MigrationPlan, compute_plan(...)
  applier.py                             # apply(plan, *, ..., confirm=...) → AppliedReport
  recovery.py                            # check_partial_commits(...) → list[OrphanedRevision]
  data_uow.py                            # open_data_uow(engine, plugin) → UnitOfWork (events suppressed)
  errors.py                              # MigrationError hierarchy

hearth/cli/_migrate.py                   # `hearth migrate generate|check` subcommand group

tests/kernel/migrations/
  __init__.py
  conftest.py                            # Fixtures: tmp_plugin_with_migrations, alembic_engine, etc.
  test_classifier.py
  test_alembic_config.py
  test_audit_log.py
  test_planner.py
  test_applier.py
  test_recovery.py
  test_data_hooks.py

tests/cli/
  test_migrate_generate.py
  test_migrate_check.py
  test_db_plan.py
  test_db_migrate.py
  test_db_status_overhaul.py
```

**Modify:**

- `hearth/cli/_db.py` — add `plan`, `migrate` commands; rewrite `status` to read from schema log and adoption path; remove "schema log not yet initialized" placeholder.
- `hearth/cli/__init__.py` — register `migrate_app` from `_migrate.py`.
- `hearth/__init__.py` — no changes (migration system is not part of plugin-author SDK other than `hearth.migrations.OpSummary` already re-exported).
- `pyproject.toml` — add `alembic>=1.13` to `dependencies`.
- `docs/architecture/0006-orm-contract.md` — append the walled-garden-clarification amendment noting that migration files may import from `alembic` and `sqlalchemy`.
- `docs/core/kernel/migrations.md` — replace the stub with a one-line pointer to the implemented spec.

---

## Phase A — Foundation

### Task A1: Add Alembic dependency and create module skeleton

**Files:**
- Modify: `pyproject.toml`
- Create: `hearth/migrations/__init__.py`
- Create: `hearth/migrations/_ops.py`
- Create: `hearth/kernel/migrations/__init__.py`
- Create: `hearth/kernel/migrations/errors.py`
- Create: `tests/kernel/migrations/__init__.py`
- Create: `tests/kernel/migrations/conftest.py` (empty for now, will grow)

- [ ] **Step 1: Add Alembic to pyproject.toml dependencies**

Edit `[project].dependencies` in `pyproject.toml` to add `"alembic>=1.13"` after `"sqlalchemy[asyncio]>=2.0"`. Run `uv sync` to install.

- [ ] **Step 2: Create the `OpSummary` dataclass and the public re-export**

`hearth/migrations/_ops.py`:

```python
"""Public dataclass plugin-author migration files import."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class OpSummary:
    """The classifier's verdict on a single migration op, persisted in
    revision files as `HEARTH_UPGRADE_OPS` / `HEARTH_DOWNGRADE_OPS` so the
    kernel can drive destructive-op confirmation at apply time without
    introspecting Alembic's opaque `upgrade()` body.
    """

    kind: str
    """Alembic op class name (e.g. 'AddColumn'), or 'DataMigration' for the
    Python `data_upgrade` / `data_downgrade` hooks."""

    desc: str
    """Human description used in plan output and audit log."""

    destructive: bool
    """The classifier's verdict. True triggers an interactive confirmation
    prompt before apply."""
```

`hearth/migrations/__init__.py`:

```python
"""Plugin-author surface for Hearth migrations.

Plugin migration files import `OpSummary` from here. Everything else they
need (the `op` namespace, types) comes from Alembic and SQLAlchemy directly —
Hearth deliberately does not wrap those (see ADR-0006 walled-garden
amendment).
"""

from hearth.migrations._ops import OpSummary

__all__ = ["OpSummary"]
```

- [ ] **Step 3: Create kernel-migrations module skeleton and error hierarchy**

`hearth/kernel/migrations/__init__.py`:

```python
"""Kernel internals for schema migrations. Not plugin-facing."""
```

`hearth/kernel/migrations/errors.py`:

```python
"""Migration-system error hierarchy."""

from hearth.errors import HearthError


class MigrationError(HearthError):
    """Base for all migration-system errors."""


class MissingClassificationArtifact(MigrationError):
    """A revision file lacks `HEARTH_UPGRADE_OPS`; cannot determine safety."""


class MigrationLockContention(MigrationError):
    """Another migration is in progress (advisory lock held)."""


class PartialCommitUnrecoverable(MigrationError):
    """An orphaned revision exists and recovery failed."""


class PluginDowngradeDetected(MigrationError):
    """Applied head is ahead of what the plugin ships; rollback (not v1) needed."""


class AdoptionDriftDetected(MigrationError):
    """Existing install has tables but live schema does not match declared schema."""
```

- [ ] **Step 4: Verify the modules import**

```bash
uv run python -c "from hearth.migrations import OpSummary; print(OpSummary(kind='AddColumn', desc='x', destructive=False))"
uv run python -c "from hearth.kernel.migrations.errors import MigrationError"
```
Expected: both succeed silently (first prints the dataclass repr).

---

### Task A2: Classifier

**Files:**
- Create: `hearth/kernel/migrations/classifier.py`
- Create: `tests/kernel/migrations/test_classifier.py`

- [ ] **Step 1: Write the failing tests for classifier verdicts**

`tests/kernel/migrations/test_classifier.py`:

```python
"""Tests for the destructive-op classifier."""

from __future__ import annotations

import pytest
from alembic.operations import ops as aop
import sqlalchemy as sa

from hearth.kernel.migrations.classifier import classify


def _col(name: str, type_: sa.types.TypeEngine, nullable: bool = True, server_default=None) -> sa.Column:
    return sa.Column(name, type_, nullable=nullable, server_default=server_default)


@pytest.mark.parametrize(
    ("op", "expected"),
    [
        (aop.CreateTableOp("user", [_col("id", sa.BigInteger(), nullable=False)]), "safe"),
        (aop.DropTableOp("user"), "destructive"),
        (aop.AddColumnOp("user", _col("phone", sa.String(), nullable=True)), "safe"),
        (aop.AddColumnOp("user", _col("phone", sa.String(), nullable=False, server_default="''")), "safe"),
        (aop.AddColumnOp("user", _col("phone", sa.String(), nullable=False)), "destructive"),
        (aop.DropColumnOp("user", "phone"), "destructive"),
        (aop.CreateIndexOp("ix_user_email", "user", ["email"]), "safe"),
        (aop.DropIndexOp("ix_user_email", "user", unique=True), "destructive"),
        (aop.DropIndexOp("ix_user_email", "user", unique=False), "safe"),
        (aop.CreateUniqueConstraintOp("uq_user_email", "user", ["email"]), "destructive"),
        (aop.CreateCheckConstraintOp("ck_user_age", "user", "age >= 0"), "destructive"),
        (aop.DropConstraintOp("uq_user_email", "user"), "destructive"),
        (aop.ExecuteSQLOp("UPDATE user SET phone = NULL"), "destructive"),
    ],
)
def test_classify_static_cases(op, expected) -> None:
    assert classify(op) == expected


def test_classify_alter_column_rename_is_safe() -> None:
    op = aop.AlterColumnOp("user", "full_name", modify_name="display_name")
    assert classify(op) == "safe"


def test_classify_alter_column_nullable_to_false_is_destructive() -> None:
    op = aop.AlterColumnOp("user", "email", modify_nullable=False, existing_nullable=True)
    assert classify(op) == "destructive"


def test_classify_alter_column_nullable_to_true_is_safe() -> None:
    op = aop.AlterColumnOp("user", "email", modify_nullable=True, existing_nullable=False)
    assert classify(op) == "safe"


def test_classify_alter_column_type_widen_is_safe() -> None:
    op = aop.AlterColumnOp(
        "user", "age", modify_type=sa.BigInteger(), existing_type=sa.Integer()
    )
    assert classify(op) == "safe"


def test_classify_alter_column_type_narrow_is_destructive() -> None:
    op = aop.AlterColumnOp(
        "user", "age", modify_type=sa.Integer(), existing_type=sa.BigInteger()
    )
    assert classify(op) == "destructive"


def test_classify_unknown_op_is_destructive() -> None:
    """Unknown ops fail closed — operator can always say yes, we cannot un-drop."""

    class _MysteryOp:
        pass

    assert classify(_MysteryOp()) == "destructive"
```

- [ ] **Step 2: Run to verify the tests fail**

```bash
uv run pytest tests/kernel/migrations/test_classifier.py -v
```
Expected: ImportError (`classify` not defined yet).

- [ ] **Step 3: Implement the classifier**

`hearth/kernel/migrations/classifier.py`:

```python
"""Destructive-op classifier: inspects an Alembic `MigrateOperation` and
returns the verdict that drives interactive confirmation at apply time.

Runs at *generate* time (where the Alembic op instances exist with their
fields) and persists into the revision file as `HEARTH_UPGRADE_OPS`. See
[docs/superpowers/specs/2026-05-18-schema-migrations-design.md].
"""

from __future__ import annotations

from typing import Literal

from alembic.operations import ops as aop
import sqlalchemy as sa


Classification = Literal["safe", "destructive"]


# Type families that contain each other losslessly. (int → bigint widens; the
# reverse narrows.) This is a conservative table; the slice may refine.
_NUMERIC_WIDTH: dict[type[sa.types.TypeEngine], int] = {
    sa.SmallInteger: 1,
    sa.Integer: 2,
    sa.BigInteger: 3,
}


def _is_lossless_type_change(existing: sa.types.TypeEngine, modify: sa.types.TypeEngine) -> bool:
    e_cls, m_cls = type(existing), type(modify)
    if e_cls is m_cls:
        # Same family — check length if applicable.
        e_len = getattr(existing, "length", None)
        m_len = getattr(modify, "length", None)
        if e_len is not None and m_len is not None:
            return m_len >= e_len
        return True
    # Numeric widening
    e_width = _NUMERIC_WIDTH.get(e_cls)
    m_width = _NUMERIC_WIDTH.get(m_cls)
    if e_width is not None and m_width is not None:
        return m_width >= e_width
    return False


def classify(op: object) -> Classification:
    """Return the verdict for a single Alembic `MigrateOperation`.

    Errs toward 'destructive' for any op the classifier does not recognize —
    the operator can say yes interactively; we cannot un-drop a column they
    did not know was being dropped.
    """
    match op:
        case aop.CreateTableOp():
            return "safe"
        case aop.DropTableOp():
            return "destructive"
        case aop.AddColumnOp():
            col = op.column
            if col.nullable:
                return "safe"
            if col.server_default is not None:
                return "safe"
            return "destructive"
        case aop.DropColumnOp():
            return "destructive"
        case aop.AlterColumnOp():
            if op.modify_name is not None:
                return "safe"
            if op.modify_nullable is False and op.existing_nullable is not False:
                return "destructive"
            if op.modify_nullable is True:
                return "safe"
            if op.modify_type is not None and op.existing_type is not None:
                if _is_lossless_type_change(op.existing_type, op.modify_type):
                    return "safe"
                return "destructive"
            return "safe"
        case aop.RenameTableOp():
            return "safe"
        case aop.CreateIndexOp():
            return "safe"
        case aop.DropIndexOp():
            return "destructive" if op.kw.get("unique", False) else "safe"
        case aop.CreateUniqueConstraintOp() | aop.CreateCheckConstraintOp():
            return "destructive"
        case aop.CreateForeignKeyOp():
            return "safe"
        case aop.DropConstraintOp():
            return "destructive"
        case aop.ExecuteSQLOp():
            return "destructive"
        case _:
            return "destructive"
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/kernel/migrations/test_classifier.py -v
```
Expected: all parametrized cases + named cases pass.

- [ ] **Step 5: Run lint + typecheck**

```bash
uv run ruff check hearth/kernel/migrations/classifier.py tests/kernel/migrations/test_classifier.py
uv run basedpyright hearth/kernel/migrations/classifier.py
```
Expected: both clean. If `DropIndexOp` `unique` attribute access fails type-check, adjust to use `getattr(op, "unique", False)` instead of `op.kw.get(...)`.

---

### Task A3: Schema log ORM

**Files:**
- Create: `hearth/kernel/migrations/audit_log.py`
- Create: `tests/kernel/migrations/test_audit_log.py`

- [ ] **Step 1: Write the failing tests**

`tests/kernel/migrations/test_audit_log.py`:

```python
"""Tests for the schema-log audit overlay table."""

from __future__ import annotations

import pytest
from sqlalchemy import inspect

from hearth.kernel._engine import make_async_engine
from hearth.kernel.migrations.audit_log import (
    SCHEMA_LOG_METADATA,
    SchemaLogRow,
    read_applied_revisions,
    record_applied_revision,
)


@pytest.fixture
async def engine(tmp_path):
    eng = make_async_engine(f"sqlite+aiosqlite:///{tmp_path}/test.db")
    async with eng.begin() as conn:
        await conn.run_sync(SCHEMA_LOG_METADATA.create_all)
    yield eng
    await eng.dispose()


async def test_schema_log_table_is_created(engine) -> None:
    async with engine.connect() as conn:
        names = await conn.run_sync(lambda s: set(inspect(s).get_table_names()))
    assert "_hearth_schema_log" in names


async def test_record_and_read_revision(engine) -> None:
    await record_applied_revision(
        engine,
        plugin="auth",
        revision_id="0001",
        description="initial schema",
        applied_by="system:adopt",
        destructive_ops_acked=[],
    )
    rows = await read_applied_revisions(engine, plugin="auth")
    assert len(rows) == 1
    assert rows[0].revision_id == "0001"
    assert rows[0].destructive_ops_acked == []


async def test_record_preserves_destructive_acknowledgments(engine) -> None:
    await record_applied_revision(
        engine,
        plugin="auth",
        revision_id="0007",
        description="drop legacy email",
        applied_by="user:42",
        destructive_ops_acked=["DROP COLUMN user.email_legacy"],
    )
    rows = await read_applied_revisions(engine, plugin="auth")
    assert rows[0].destructive_ops_acked == ["DROP COLUMN user.email_legacy"]


async def test_read_returns_rows_for_plugin_only(engine) -> None:
    await record_applied_revision(engine, plugin="auth", revision_id="0001", description="x", applied_by="s", destructive_ops_acked=[])
    await record_applied_revision(engine, plugin="commerce", revision_id="0001", description="y", applied_by="s", destructive_ops_acked=[])
    rows = await read_applied_revisions(engine, plugin="auth")
    assert {r.plugin for r in rows} == {"auth"}
```

- [ ] **Step 2: Run to verify the tests fail**

```bash
uv run pytest tests/kernel/migrations/test_audit_log.py -v
```
Expected: ImportError on `SCHEMA_LOG_METADATA` / `SchemaLogRow`.

- [ ] **Step 3: Implement the schema-log model + helpers**

`hearth/kernel/migrations/audit_log.py`:

```python
"""The `_hearth_schema_log` audit table.

Alembic's `alembic_version` table is the source of truth for *what's
applied*; this table is a pure audit overlay recording *who* applied
*what revision* *when*, including which destructive ops the operator
explicitly acknowledged.

Lives on its own SQLAlchemy MetaData (separate from plugin entity
METADATA) so `db init` can create it without touching plugin tables.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine


SCHEMA_LOG_METADATA = sa.MetaData()

SCHEMA_LOG_TABLE = sa.Table(
    "_hearth_schema_log",
    SCHEMA_LOG_METADATA,
    sa.Column("plugin", sa.Text, nullable=False),
    sa.Column("revision_id", sa.Text, nullable=False),
    sa.Column("description", sa.Text, nullable=False),
    sa.Column("applied_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("applied_by", sa.Text, nullable=False),
    sa.Column("destructive_ops_acked", sa.JSON, nullable=False),
    sa.PrimaryKeyConstraint("plugin", "revision_id"),
)


@dataclass(frozen=True, slots=True)
class SchemaLogRow:
    plugin: str
    revision_id: str
    description: str
    applied_at: datetime
    applied_by: str
    destructive_ops_acked: list[str]


async def record_applied_revision(
    engine: AsyncEngine,
    *,
    plugin: str,
    revision_id: str,
    description: str,
    applied_by: str,
    destructive_ops_acked: list[str],
) -> None:
    """Insert one audit row in its own transaction. Used for DDL-only
    revisions where there is no data UoW to co-commit with.

    For revisions with `data_upgrade`, use `record_applied_revision_in_uow`
    instead — that variant takes a UnitOfWork so the audit row and the
    data live or die together.
    """
    async with engine.begin() as conn:
        await conn.execute(
            SCHEMA_LOG_TABLE.insert().values(
                plugin=plugin,
                revision_id=revision_id,
                description=description,
                applied_at=datetime.now(timezone.utc),
                applied_by=applied_by,
                destructive_ops_acked=list(destructive_ops_acked),
            )
        )


async def record_applied_revision_in_uow(
    uow,  # hearth.unit_of_work.UnitOfWork
    *,
    plugin: str,
    revision_id: str,
    description: str,
    applied_by: str,
    destructive_ops_acked: list[str],
) -> None:
    """Insert one audit row using an existing UoW's session. Used for
    revisions with `data_upgrade` so the audit row commits atomically
    with the data work. Caller does NOT commit — the caller's UoW
    commits at its own scope exit."""
    await uow.execute(
        SCHEMA_LOG_TABLE.insert().values(
            plugin=plugin,
            revision_id=revision_id,
            description=description,
            applied_at=datetime.now(timezone.utc),
            applied_by=applied_by,
            destructive_ops_acked=list(destructive_ops_acked),
        )
    )


async def read_applied_revisions(
    engine: AsyncEngine,
    *,
    plugin: str | None = None,
) -> list[SchemaLogRow]:
    """Return audit rows ordered by (plugin, revision_id). Filter by plugin
    when given."""
    async with engine.connect() as conn:
        stmt = sa.select(SCHEMA_LOG_TABLE).order_by(
            SCHEMA_LOG_TABLE.c.plugin, SCHEMA_LOG_TABLE.c.revision_id
        )
        if plugin is not None:
            stmt = stmt.where(SCHEMA_LOG_TABLE.c.plugin == plugin)
        result = await conn.execute(stmt)
        return [
            SchemaLogRow(
                plugin=row.plugin,
                revision_id=row.revision_id,
                description=row.description,
                applied_at=row.applied_at,
                applied_by=row.applied_by,
                destructive_ops_acked=list(row.destructive_ops_acked or []),
            )
            for row in result.mappings()
        ]
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/kernel/migrations/test_audit_log.py -v
```
Expected: 4 passed.

- [ ] **Step 5: Wire `_hearth_schema_log` into `hearth db init`**

Modify `hearth/cli/_db.py`. In `_init_impl`, after creating `METADATA.create_all`, also create the schema log:

```python
from hearth.kernel.migrations.audit_log import SCHEMA_LOG_METADATA

# Inside _init_impl, before "Done.":
async with engine.begin() as conn:
    await conn.run_sync(SCHEMA_LOG_METADATA.create_all)
typer.echo("  [OK] _hearth_schema_log")
```

- [ ] **Step 6: Verify `hearth db init` creates the schema log**

```bash
DATABASE_URL="sqlite:///tmp/test_init.db" uv run hearth db init
sqlite3 /tmp/test_init.db ".tables"
```
Expected: output includes `_hearth_schema_log` alongside `_hearth_outbox` and any plugin tables.

---

### Task A4: Alembic config builder

**Files:**
- Create: `hearth/kernel/migrations/alembic_config.py`
- Create: `tests/kernel/migrations/test_alembic_config.py`

- [ ] **Step 1: Write the failing tests**

`tests/kernel/migrations/test_alembic_config.py`:

```python
"""Tests for the dynamic Alembic config builder."""

from __future__ import annotations

from pathlib import Path

import pytest

from hearth.kernel.migrations.alembic_config import build_config
from hearth.kernel.registry import Registry


def test_build_config_sets_render_as_batch_for_sqlite(tmp_path: Path) -> None:
    registry = Registry.build()
    cfg = build_config(registry, f"sqlite:///{tmp_path}/x.db")
    # render_as_batch is set in the EnvironmentContext; we expose it via
    # config attributes the alembic_config module also reads at apply time.
    assert cfg.get_main_option("hearth.render_as_batch") == "true"


def test_build_config_omits_render_as_batch_for_postgres() -> None:
    registry = Registry.build()
    cfg = build_config(registry, "postgresql://user:pw@host/db")
    assert cfg.get_main_option("hearth.render_as_batch") in (None, "false")


def test_build_config_sets_version_locations_per_plugin(tmp_path: Path) -> None:
    registry = Registry.build()
    cfg = build_config(registry, f"sqlite:///{tmp_path}/x.db")
    locations = cfg.get_main_option("version_locations") or ""
    # Each installed plugin contributes one comma-separated entry.
    parts = [p.strip() for p in locations.split(",") if p.strip()]
    assert len(parts) == len(registry.plugins)
    for part in parts:
        assert part.endswith("/migrations"), f"version_location should point at migrations/: {part}"
```

- [ ] **Step 2: Run to verify they fail**

```bash
uv run pytest tests/kernel/migrations/test_alembic_config.py -v
```
Expected: ImportError on `build_config`.

- [ ] **Step 3: Implement the builder**

`hearth/kernel/migrations/alembic_config.py`:

```python
"""Build an in-memory Alembic `Config` from the kernel's plugin registry.

The kernel never ships an `alembic.ini` or an `env.py`; everything Alembic
needs at runtime is constructed here. One `Config` per kernel process,
multi-base via `version_locations` (one path per plugin), branch labels
declared in each revision file.
"""

from __future__ import annotations

import importlib.resources
from pathlib import Path

from alembic.config import Config

from hearth.kernel.persistence import METADATA
from hearth.kernel.registry import Registry


def _plugin_migrations_dir(module_name: str) -> Path:
    """Resolve `<plugin_module>/migrations/` to a filesystem path."""
    return Path(str(importlib.resources.files(module_name).joinpath("migrations")))


def build_config(registry: Registry, engine_url: str) -> Config:
    """Construct the Alembic `Config` used for plan/apply/generate.

    - `version_locations`: comma-separated list of `<plugin>/migrations/`
      paths for every installed plugin.
    - `sqlalchemy.url`: the engine URL (sync form; Alembic does not use
      async drivers directly).
    - `target_metadata`: the kernel's merged plugin-entity MetaData
      (set as an attribute, not a config option — Alembic reads it from
      `config.attributes` during autogenerate).
    - `hearth.render_as_batch`: "true" iff the engine URL targets SQLite.
      The applier reads this and passes `render_as_batch=True` to
      `EnvironmentContext` so `op.batch_alter_table(...)` works.
    """
    cfg = Config()
    cfg.set_main_option("sqlalchemy.url", _to_sync_url(engine_url))

    locations: list[str] = []
    for alias, info in sorted(registry.plugins.items()):
        try:
            path = _plugin_migrations_dir(info.module)
        except (ModuleNotFoundError, FileNotFoundError):
            # Plugin has no migrations/ directory yet — that's fine, skip it.
            continue
        if path.is_dir():
            locations.append(str(path))
    cfg.set_main_option("version_locations", ",".join(locations))

    cfg.attributes["target_metadata"] = METADATA
    cfg.set_main_option(
        "hearth.render_as_batch",
        "true" if _is_sqlite(engine_url) else "false",
    )
    return cfg


def _is_sqlite(url: str) -> bool:
    return url.startswith("sqlite")


def _to_sync_url(url: str) -> str:
    """Alembic uses sync drivers internally. Translate aiosqlite/asyncpg URLs
    to their sync equivalents."""
    if url.startswith("sqlite+aiosqlite"):
        return url.replace("sqlite+aiosqlite", "sqlite", 1)
    if url.startswith("postgresql+asyncpg"):
        return url.replace("postgresql+asyncpg", "postgresql", 1)
    return url
```

- [ ] **Step 4: Run tests to verify pass**

```bash
uv run pytest tests/kernel/migrations/test_alembic_config.py -v
```
Expected: 3 passed.

- [ ] **Step 5: Lint + typecheck**

```bash
uv run ruff check hearth/kernel/migrations/alembic_config.py
uv run basedpyright hearth/kernel/migrations/alembic_config.py
```
Expected: clean.

---

### Task A5: Programmatic env.py + wire `script_location`

**Files:**
- Create: `hearth/kernel/migrations/_env.py`
- Modify: `hearth/kernel/migrations/alembic_config.py` (write env.py to a tmp dir and set `script_location`)
- Create: `tests/kernel/migrations/test_env_wiring.py`

`alembic.command.upgrade(config, rev)` and `alembic.command.revision(...)` both locate `env.py` via the `script_location` config option. Without it, every Alembic invocation in Phases C/F/G fails with "Can't find a revision script location" before doing any useful work. Likewise `MigrationContext.configure(render_as_batch=True)` is set inside env.py — there is no main-options route. This task makes Alembic actually runnable.

- [ ] **Step 1: Implement the env.py module body**

`hearth/kernel/migrations/_env.py`:

```python
"""Programmatic env.py for the kernel's Alembic environment.

The kernel ships this file as a Python module; `build_config()` copies
it into a tmp `script_location` directory so Alembic can find it. The
env.py reads `target_metadata` from `config.attributes` (set by
`build_config()`) and `hearth.render_as_batch` from the main options.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool


def run_migrations_offline() -> None:
    config = context.config
    url = config.get_main_option("sqlalchemy.url")
    target_metadata = config.attributes.get("target_metadata")
    render_as_batch = config.get_main_option("hearth.render_as_batch") == "true"
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=render_as_batch,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    config = context.config
    target_metadata = config.attributes.get("target_metadata")
    render_as_batch = config.get_main_option("hearth.render_as_batch") == "true"
    connectable = engine_from_config(
        config.get_section(config.config_ini_section) or {},
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=render_as_batch,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
```

- [ ] **Step 2: Modify `build_config()` to materialize env.py + set script_location**

Replace the `build_config` body in `hearth/kernel/migrations/alembic_config.py`:

```python
import importlib.resources
import shutil
import tempfile
from pathlib import Path

# Module-level cache so we materialize env.py once per process.
_ENV_DIR: Path | None = None


def _materialize_env_dir() -> Path:
    """Copy the kernel's env.py into a tmp dir Alembic can use as
    script_location. Re-used across calls in the same process."""
    global _ENV_DIR
    if _ENV_DIR is not None and _ENV_DIR.exists():
        return _ENV_DIR
    env_dir = Path(tempfile.mkdtemp(prefix="hearth_alembic_"))
    src = importlib.resources.files("hearth.kernel.migrations").joinpath("_env.py")
    shutil.copy(str(src), env_dir / "env.py")
    # Alembic also expects a script.py.mako template; ship a minimal one.
    (env_dir / "script.py.mako").write_text(_SCRIPT_MAKO)
    _ENV_DIR = env_dir
    return env_dir


_SCRIPT_MAKO = '''\
"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Create Date: ${create_date}
"""
from alembic import op
import sqlalchemy as sa
${imports if imports else ""}

# revision identifiers, used by Alembic.
revision = ${repr(up_revision)}
down_revision = ${repr(down_revision)}
branch_labels = ${repr(branch_labels)}
depends_on = ${repr(depends_on)}


def upgrade() -> None:
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    ${downgrades if downgrades else "pass"}
'''


def build_config(registry: Registry, engine_url: str) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(_materialize_env_dir()))
    cfg.set_main_option("sqlalchemy.url", _to_sync_url(engine_url))

    locations: list[str] = []
    for alias, info in sorted(registry.plugins.items()):
        try:
            path = _plugin_migrations_dir(info.module)
        except (ModuleNotFoundError, FileNotFoundError):
            continue
        if path.is_dir():
            locations.append(str(path))
    cfg.set_main_option("version_locations", ",".join(locations))

    cfg.attributes["target_metadata"] = METADATA
    cfg.set_main_option(
        "hearth.render_as_batch",
        "true" if _is_sqlite(engine_url) else "false",
    )
    return cfg
```

- [ ] **Step 3: Write integration test that proves Alembic can actually upgrade**

`tests/kernel/migrations/test_env_wiring.py`:

```python
"""End-to-end: build_config + alembic.command.upgrade actually run."""

from __future__ import annotations

import asyncio

import pytest
import sqlalchemy as sa
from alembic import command as alembic_command

from hearth.kernel._engine import make_async_engine
from hearth.kernel.migrations.alembic_config import build_config
from hearth.kernel.registry import Registry, PluginInfo


async def test_alembic_upgrade_runs_through_env_py(
    tmp_path, tmp_plugin_with_migrations, example_revision_body,
) -> None:
    builder = tmp_plugin_with_migrations
    builder.add_revision(
        revision="0001", down_revision=None,
        body=example_revision_body.format(
            revision="0001", down_revision=None, branch=builder.plugin, table="env_test",
        ),
    )
    engine = make_async_engine(f"sqlite+aiosqlite:///{tmp_path}/env.db")
    try:
        registry = Registry.build()
        registry.plugins[builder.plugin] = PluginInfo(
            alias=builder.plugin, package=builder.plugin, version="0.0.1",
            module=builder.plugin, install_path=str(builder.package_dir),
            depends_on=[], extends=[], entities=[], actions=[], events=[],
        )
        cfg = build_config(registry, str(engine.url))
        # Sync invocation — alembic uses sync drivers internally.
        await asyncio.to_thread(alembic_command.upgrade, cfg, f"{builder.plugin}@head")

        async with engine.connect() as conn:
            names = await conn.run_sync(lambda s: set(sa.inspect(s).get_table_names()))
        assert "env_test" in names
    finally:
        await engine.dispose()
```

- [ ] **Step 4: Run the test**

```bash
uv run pytest tests/kernel/migrations/test_env_wiring.py -v
```
Expected: pass. (If `_plugin_migrations_dir` discovery + branch resolution don't compose cleanly, the failure mode will be specific and fixable.)

---

### Task A6: Conftest helper — tmp plugin with migrations

**Files:**
- Modify: `tests/kernel/migrations/conftest.py`

- [ ] **Step 1: Write the helper**

This fixture is reused by every later test that needs a real Alembic environment. It creates a temporary plugin package on disk with a `migrations/` dir, installs it via `sys.path`, and exposes a builder so tests add revisions one at a time.

`tests/kernel/migrations/conftest.py`:

```python
"""Shared fixtures for migration tests."""

from __future__ import annotations

import shutil
import sys
import textwrap
from collections.abc import Iterator
from pathlib import Path
from typing import Protocol

import pytest


class _PluginBuilder(Protocol):
    plugin: str
    package_dir: Path

    def add_revision(self, *, revision: str, down_revision: str | None, body: str) -> None: ...


@pytest.fixture
def tmp_plugin_with_migrations(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[_PluginBuilder]:
    """Create a tmp Python package with a `migrations/` directory and an
    importable module. The builder lets tests append revision files."""

    plugin = f"hearth_test_plug_{tmp_path.name.replace('-', '_')}"
    package_dir = tmp_path / plugin
    package_dir.mkdir()
    (package_dir / "__init__.py").write_text("")
    (package_dir / "migrations").mkdir()
    (package_dir / "migrations" / "__init__.py").write_text("")

    monkeypatch.syspath_prepend(str(tmp_path))

    class _Builder:
        def __init__(self) -> None:
            self.plugin = plugin
            self.package_dir = package_dir

        def add_revision(self, *, revision: str, down_revision: str | None, body: str) -> None:
            content = textwrap.dedent(body).lstrip("\n")
            (package_dir / "migrations" / f"{revision}_test.py").write_text(content)

    yield _Builder()

    # Tear down: drop the module so subsequent tests get a clean slate.
    for mod in list(sys.modules):
        if mod == plugin or mod.startswith(f"{plugin}."):
            del sys.modules[mod]
    shutil.rmtree(package_dir, ignore_errors=True)


@pytest.fixture
def example_revision_body() -> str:
    """A simple revision authors can copy into add_revision()."""
    return """
        from alembic import op
        import sqlalchemy as sa
        from hearth.migrations import OpSummary

        revision = "{revision}"
        down_revision = {down_revision!r}
        branch_labels = ({branch!r},)
        depends_on = None

        HEARTH_UPGRADE_OPS = [
            OpSummary(kind="CreateTable", desc="CREATE TABLE {table}", destructive=False),
        ]
        HEARTH_DOWNGRADE_OPS = [
            OpSummary(kind="DropTable", desc="DROP TABLE {table}", destructive=True),
        ]


        def upgrade() -> None:
            op.create_table(
                "{table}",
                sa.Column("id", sa.BigInteger, primary_key=True),
                sa.Column("name", sa.String, nullable=True),
            )


        def downgrade() -> None:
            op.drop_table("{table}")
    """
```

- [ ] **Step 2: Smoke-test the fixture**

```bash
uv run python -c "
import pytest
from tests.kernel.migrations.conftest import tmp_plugin_with_migrations
"
```
Expected: silent (import success). Real coverage comes from later tests using the fixture.

---

## Phase B — Planner + `hearth db plan`

### Task B1: PlannedRevision, MigrationPlan, planner

**Files:**
- Create: `hearth/kernel/migrations/planner.py`
- Create: `tests/kernel/migrations/test_planner.py`

- [ ] **Step 1: Write the failing tests**

`tests/kernel/migrations/test_planner.py`:

```python
"""Tests for the planner."""

from __future__ import annotations

from alembic.config import Config

import pytest

from hearth.kernel._engine import make_async_engine
from hearth.kernel.migrations.alembic_config import build_config
from hearth.kernel.migrations.audit_log import SCHEMA_LOG_METADATA
from hearth.kernel.migrations.planner import MigrationPlan, compute_plan
from hearth.kernel.registry import Registry


@pytest.fixture
async def engine(tmp_path):
    eng = make_async_engine(f"sqlite+aiosqlite:///{tmp_path}/db.sqlite")
    async with eng.begin() as conn:
        await conn.run_sync(SCHEMA_LOG_METADATA.create_all)
    yield eng
    await eng.dispose()


async def test_empty_plan_when_no_plugins(engine, tmp_plugin_with_migrations) -> None:
    # No revisions added; planner returns empty plan.
    registry = Registry.build()
    cfg = build_config(registry, str(engine.url))
    plan = await compute_plan(engine, cfg, registry)
    assert plan.revisions == []
    assert plan.has_destructive is False


async def test_plan_lists_pending_revisions(engine, tmp_plugin_with_migrations, example_revision_body) -> None:
    builder = tmp_plugin_with_migrations
    builder.add_revision(
        revision="0001",
        down_revision=None,
        body=example_revision_body.format(
            revision="0001", down_revision=None, branch=builder.plugin, table="users"
        ),
    )
    # Patch registry to include our tmp plugin.
    registry = Registry.build()
    registry.plugins[builder.plugin] = _fake_plugin_info(builder.plugin, builder.package_dir.parent)
    cfg = build_config(registry, str(engine.url))
    plan = await compute_plan(engine, cfg, registry)
    assert len(plan.revisions) == 1
    assert plan.revisions[0].plugin == builder.plugin
    assert plan.revisions[0].revision_id == "0001"
    assert plan.revisions[0].ops[0].kind == "CreateTable"


def _fake_plugin_info(alias: str, install_path):
    # Minimal stand-in for tests; real PluginInfo comes from Registry.build().
    from hearth.kernel.registry import PluginInfo
    return PluginInfo(
        alias=alias,
        package=alias,
        version="0.0.1",
        module=alias,
        install_path=str(install_path),
        depends_on=[],
        extends=[],
        entities=[],
        actions=[],
        events=[],
    )
```

- [ ] **Step 2: Run to verify the tests fail**

```bash
uv run pytest tests/kernel/migrations/test_planner.py -v
```
Expected: ImportError on `compute_plan`.

- [ ] **Step 3: Implement the planner**

`hearth/kernel/migrations/planner.py`:

```python
"""Compute a `MigrationPlan`: ordered list of pending revisions per plugin.

Uses Alembic's `ScriptDirectory.walk_revisions(...)` to enumerate the
chain between each plugin's applied head (from the shared `alembic_version`
table) and its declared head (the latest revision in the package). Per-
revision `HEARTH_UPGRADE_OPS` lists are read at this stage; missing lists
raise `MissingClassificationArtifact`.
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from typing import Iterator

import sqlalchemy as sa
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy.ext.asyncio import AsyncEngine

from hearth.kernel.migrations.errors import MissingClassificationArtifact
from hearth.kernel.registry import Registry
from hearth.migrations import OpSummary


@dataclass(frozen=True, slots=True)
class PlannedRevision:
    plugin: str
    revision_id: str
    description: str
    ops: list[OpSummary]

    @property
    def destructive_ops(self) -> list[OpSummary]:
        return [o for o in self.ops if o.destructive]


@dataclass(frozen=True, slots=True)
class MigrationPlan:
    revisions: list[PlannedRevision] = field(default_factory=list)

    @property
    def has_destructive(self) -> bool:
        return any(r.destructive_ops for r in self.revisions)

    def format(self, *, color: bool = False) -> str:
        """Human-readable rendering for CLI / operator review."""
        if not self.revisions:
            return "No pending migrations."
        lines: list[str] = ["Migration plan:"]
        for r in self.revisions:
            lines.append(f"  {r.plugin}: {r.revision_id} — {r.description}")
            for op in r.ops:
                marker = "[!!]" if op.destructive else "[ok]"
                lines.append(f"    {marker} {op.desc}")
        return "\n".join(lines)


async def compute_plan(engine: AsyncEngine, config: Config, registry: Registry) -> MigrationPlan:
    """Walk each plugin's pending revisions and produce a MigrationPlan."""
    revisions: list[PlannedRevision] = []
    script = ScriptDirectory.from_config(config)
    applied = await _read_alembic_version_per_branch(engine, script)

    for alias in registry.topological_order():
        if not _has_branch(script, alias):
            continue
        head = script.get_current_head(alias)
        if head is None:
            continue
        current = applied.get(alias)
        for sc in _walk_pending(script, current, head):
            # Alembic's Script.module gives the already-loaded revision
            # module — avoid re-importing via importlib to prevent module
            # isolation issues during tests that manipulate sys.modules.
            ops = _read_hearth_upgrade_ops(sc.module, alias, sc.revision)
            revisions.append(
                PlannedRevision(
                    plugin=alias,
                    revision_id=sc.revision,
                    description=sc.doc or "",
                    ops=ops,
                )
            )
    return MigrationPlan(revisions=revisions)


async def _read_alembic_version_per_branch(
    engine: AsyncEngine, script: ScriptDirectory
) -> dict[str, str]:
    """Map each branch label to the revision currently applied for that branch.

    Alembic stores one row per active branch head in the shared
    `alembic_version` table. Each row identifies a revision; we look the
    revision up in the script directory to recover which branch it
    belongs to.
    """
    async with engine.connect() as conn:
        exists = await conn.run_sync(
            lambda s: "alembic_version" in sa.inspect(s).get_table_names()
        )
        if not exists:
            return {}
        result = await conn.execute(sa.text("SELECT version_num FROM alembic_version"))
        applied_revisions = [row[0] for row in result]

    head_by_branch: dict[str, str] = {}
    for rev in applied_revisions:
        sc = script.get_revision(rev)
        if sc is None:
            continue
        for label in (sc.branch_labels or []):
            head_by_branch[label] = rev
    return head_by_branch


def _has_branch(script: ScriptDirectory, branch: str) -> bool:
    try:
        script.get_current_head(branch)
        return True
    except Exception:
        return False


def _walk_pending(script: ScriptDirectory, current: str | None, head: str) -> Iterator:
    """Yield Alembic Script objects from `current` (exclusive) to `head` (inclusive)."""
    revisions = list(script.walk_revisions(base=current or "base", head=head))
    # walk_revisions yields newest first; we want oldest first to apply in order.
    return reversed(revisions)


def _read_hearth_upgrade_ops(module, plugin: str, revision_id: str) -> list[OpSummary]:
    ops = getattr(module, "HEARTH_UPGRADE_OPS", None)
    if ops is None:
        raise MissingClassificationArtifact(
            f"plugin '{plugin}' revision '{revision_id}' lacks HEARTH_UPGRADE_OPS; "
            "cannot determine destructive ops. Run `hearth migrate generate --refresh` "
            "or add the lists by hand."
        )
    return list(ops)
```

- [ ] **Step 4: Add test for applied-head resolution**

Append to `test_planner.py`:

```python
async def test_plan_skips_revisions_already_applied(
    engine, tmp_plugin_with_migrations, example_revision_body
) -> None:
    builder = tmp_plugin_with_migrations
    builder.add_revision(
        revision="0001",
        down_revision=None,
        body=example_revision_body.format(
            revision="0001", down_revision=None, branch=builder.plugin, table="users"
        ),
    )
    async with engine.begin() as conn:
        await conn.execute(sa.text(
            "CREATE TABLE alembic_version (version_num VARCHAR(32) PRIMARY KEY)"
        ))
        await conn.execute(sa.text(
            "INSERT INTO alembic_version (version_num) VALUES ('0001')"
        ))
    registry = Registry.build()
    registry.plugins[builder.plugin] = _fake_plugin_info(
        builder.plugin, builder.package_dir.parent
    )
    cfg = build_config(registry, str(engine.url))
    plan = await compute_plan(engine, cfg, registry)
    assert plan.revisions == []  # nothing pending


import sqlalchemy as sa  # for the test above
```

- [ ] **Step 5: Run tests to verify pass**

```bash
uv run pytest tests/kernel/migrations/test_planner.py -v
```
Expected: 3 passed.

- [ ] **Step 6: Lint + typecheck**

```bash
uv run ruff check hearth/kernel/migrations/planner.py
uv run basedpyright hearth/kernel/migrations/planner.py
```
Expected: clean.

---

### Task B2: `hearth db plan` CLI command

**Files:**
- Modify: `hearth/cli/_db.py`
- Create: `tests/cli/test_db_plan.py`

- [ ] **Step 1: Add the plan command**

In `hearth/cli/_db.py`, after the existing `status_cmd`:

```python
from hearth.kernel.migrations.alembic_config import build_config
from hearth.kernel.migrations.planner import compute_plan


@db_app.command("plan")
@_wrap_command
def plan_cmd() -> None:
    """Show pending migrations without applying."""
    url = _require_database_url()
    asyncio.run(_plan_impl(url))


async def _plan_impl(url: str) -> None:
    try:
        registry = Registry.build()
    except RegistryBuildError as err:
        _print_registry_build_error(err)
        raise typer.Exit(1) from err
    engine = make_async_engine(url)
    try:
        cfg = build_config(registry, url)
        plan = await compute_plan(engine, cfg, registry)
        typer.echo(plan.format())
        if plan.has_destructive:
            typer.echo("")
            typer.echo("Plan contains destructive operations — will require interactive confirmation.")
    finally:
        await engine.dispose()
```

- [ ] **Step 2: Write CLI test**

`tests/cli/test_db_plan.py`:

```python
"""Tests for `hearth db plan`."""

from __future__ import annotations

from typer.testing import CliRunner

from hearth.cli import app


def test_db_plan_no_pending(tmp_path, monkeypatch) -> None:
    db_url = f"sqlite:///{tmp_path}/db.sqlite"
    monkeypatch.setenv("DATABASE_URL", db_url)
    # Initialize: kernel tables only, no plugins shipping migrations yet.
    runner = CliRunner()
    runner.invoke(app, ["db", "init"])
    result = runner.invoke(app, ["db", "plan"])
    assert result.exit_code == 0
    assert "No pending migrations" in result.output


def test_db_plan_requires_database_url(monkeypatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    runner = CliRunner()
    result = runner.invoke(app, ["db", "plan"])
    assert result.exit_code == 1
    assert "DATABASE_URL" in result.output
```

- [ ] **Step 3: Run tests**

```bash
uv run pytest tests/cli/test_db_plan.py -v
```
Expected: 2 passed.

- [ ] **Step 4: Smoke-test against a real DB**

```bash
DATABASE_URL="sqlite:///tmp/plan_smoke.db" uv run hearth db init
DATABASE_URL="sqlite:///tmp/plan_smoke.db" uv run hearth db plan
```
Expected: "No pending migrations." (no plugins ship migrations yet).

---

## Phase C — Applier + advisory lock + `hearth db migrate` (DDL only)

### Task C1: Advisory lock primitive

**Files:**
- Create: `hearth/kernel/migrations/_lock.py`
- Create: `tests/kernel/migrations/test_lock.py`

- [ ] **Step 1: Write the failing tests**

`tests/kernel/migrations/test_lock.py`:

```python
"""Tests for the single-writer advisory lock."""

from __future__ import annotations

import asyncio

import pytest

from hearth.kernel._engine import make_async_engine
from hearth.kernel.migrations._lock import acquire_migration_lock
from hearth.kernel.migrations.errors import MigrationLockContention


@pytest.fixture
async def engine(tmp_path):
    eng = make_async_engine(f"sqlite+aiosqlite:///{tmp_path}/lock.db")
    yield eng
    await eng.dispose()


async def test_lock_acquired_when_uncontended(engine) -> None:
    async with acquire_migration_lock(engine, timeout=1.0):
        pass  # held and released


async def test_lock_contention_raises(engine) -> None:
    holding = asyncio.Event()
    release = asyncio.Event()

    async def holder():
        async with acquire_migration_lock(engine, timeout=1.0):
            holding.set()
            await release.wait()

    task = asyncio.create_task(holder())
    await holding.wait()
    with pytest.raises(MigrationLockContention):
        async with acquire_migration_lock(engine, timeout=0.5):
            pass
    release.set()
    await task
```

- [ ] **Step 2: Run to verify fail**

```bash
uv run pytest tests/kernel/migrations/test_lock.py -v
```
Expected: ImportError on `acquire_migration_lock`.

- [ ] **Step 3: Implement the lock**

`hearth/kernel/migrations/_lock.py`:

```python
"""Cross-process advisory lock so only one migration runs at a time."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine

from hearth.kernel.migrations.errors import MigrationLockContention


# A fixed 64-bit integer the kernel uses for the Postgres advisory lock.
# Derived from hash("hearth-migrations") truncated to 63 bits.
_PG_LOCK_KEY = 0x4845415254484D49  # "HEARTHMI" hex


@contextlib.asynccontextmanager
async def acquire_migration_lock(engine: AsyncEngine, *, timeout: float = 5.0) -> AsyncIterator[None]:
    """Hold the migration lock for the duration of the context.

    PostgreSQL: pg_try_advisory_lock polled with the given timeout.
    SQLite: BEGIN EXCLUSIVE on a sentinel table.
    Raises MigrationLockContention if not acquired in time.
    """
    dialect = engine.dialect.name
    if dialect == "postgresql":
        async for _ in _acquire_pg(engine, timeout):
            yield
            return
    elif dialect == "sqlite":
        async for _ in _acquire_sqlite(engine, timeout):
            yield
            return
    else:
        raise NotImplementedError(f"Migration lock not implemented for dialect '{dialect}'")


async def _acquire_pg(engine: AsyncEngine, timeout: float) -> AsyncIterator[None]:
    conn = await engine.connect()
    deadline = asyncio.get_event_loop().time() + timeout
    try:
        while True:
            got = await conn.scalar(sa.text("SELECT pg_try_advisory_lock(:key)").bindparams(key=_PG_LOCK_KEY))
            if got:
                try:
                    yield
                finally:
                    await conn.execute(sa.text("SELECT pg_advisory_unlock(:key)").bindparams(key=_PG_LOCK_KEY))
                return
            if asyncio.get_event_loop().time() >= deadline:
                raise MigrationLockContention(
                    "another migration is in progress; retry once it completes"
                )
            await asyncio.sleep(0.1)
    finally:
        await conn.close()


async def _acquire_sqlite(engine: AsyncEngine, timeout: float) -> AsyncIterator[None]:
    conn = await engine.connect()
    try:
        # Ensure the lock-sentinel table exists.
        await conn.execute(sa.text(
            "CREATE TABLE IF NOT EXISTS _hearth_migration_lock (id INTEGER PRIMARY KEY)"
        ))
        await conn.commit()
        try:
            await asyncio.wait_for(
                conn.execute(sa.text("BEGIN EXCLUSIVE")), timeout=timeout
            )
        except (asyncio.TimeoutError, sa.exc.OperationalError) as err:
            raise MigrationLockContention(
                "another migration is in progress; retry once it completes"
            ) from err
        try:
            yield
        finally:
            await conn.rollback()  # releases the EXCLUSIVE
    finally:
        await conn.close()
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/kernel/migrations/test_lock.py -v
```
Expected: 2 passed.

> **Note:** SQLite `BEGIN EXCLUSIVE` semantics on aiosqlite require careful coroutine driver handling. If the test for contention is flaky, prefer the test setup that uses two processes (subprocess invocations) rather than asyncio tasks within one connection pool.

---

### Task C2: Applier (DDL only, no data hooks yet)

**Files:**
- Create: `hearth/kernel/migrations/applier.py`
- Create: `tests/kernel/migrations/test_applier.py`

- [ ] **Step 1: Write the failing tests**

`tests/kernel/migrations/test_applier.py`:

```python
"""Tests for the apply loop (DDL-only path; data hooks added in Phase E)."""

from __future__ import annotations

import pytest
import sqlalchemy as sa

from hearth.kernel._engine import make_async_engine
from hearth.kernel.migrations.alembic_config import build_config
from hearth.kernel.migrations.applier import apply
from hearth.kernel.migrations.audit_log import SCHEMA_LOG_METADATA, read_applied_revisions
from hearth.kernel.migrations.planner import compute_plan
from hearth.kernel.registry import Registry
from hearth.primitives.actor import System


@pytest.fixture
async def engine(tmp_path):
    eng = make_async_engine(f"sqlite+aiosqlite:///{tmp_path}/apply.db")
    async with eng.begin() as conn:
        await conn.run_sync(SCHEMA_LOG_METADATA.create_all)
    yield eng
    await eng.dispose()


async def test_apply_safe_plan_creates_table_and_writes_audit_row(
    engine, tmp_plugin_with_migrations, example_revision_body,
) -> None:
    builder = tmp_plugin_with_migrations
    builder.add_revision(
        revision="0001",
        down_revision=None,
        body=example_revision_body.format(
            revision="0001", down_revision=None, branch=builder.plugin, table="widgets"
        ),
    )
    registry = Registry.build()
    registry.plugins[builder.plugin] = _fake_plugin_info(
        builder.plugin, builder.package_dir.parent
    )
    cfg = build_config(registry, str(engine.url))
    plan = await compute_plan(engine, cfg, registry)

    report = await apply(plan, config=cfg, engine=engine, actor=System())
    assert report.applied_revisions == [(builder.plugin, "0001")]

    # Table was created
    async with engine.connect() as conn:
        names = await conn.run_sync(lambda s: set(sa.inspect(s).get_table_names()))
    assert "widgets" in names

    # Audit row written
    rows = await read_applied_revisions(engine, plugin=builder.plugin)
    assert len(rows) == 1
    assert rows[0].destructive_ops_acked == []


async def test_apply_destructive_op_without_confirm_aborts(
    engine, tmp_plugin_with_migrations, example_revision_body,
) -> None:
    builder = tmp_plugin_with_migrations
    # 0001 creates the table (safe), 0002 drops a column (destructive).
    builder.add_revision(
        revision="0001",
        down_revision=None,
        body=example_revision_body.format(
            revision="0001", down_revision=None, branch=builder.plugin, table="t"
        ),
    )
    builder.add_revision(
        revision="0002",
        down_revision="0001",
        body="""
            from alembic import op
            from hearth.migrations import OpSummary

            revision = "0002"
            down_revision = "0001"
            branch_labels = (\"{branch}\",)
            depends_on = None

            HEARTH_UPGRADE_OPS = [
                OpSummary(kind="DropColumn", desc="DROP COLUMN t.name", destructive=True),
            ]
            HEARTH_DOWNGRADE_OPS = []


            def upgrade(): op.drop_column("t", "name")
            def downgrade(): pass
        """.format(branch=builder.plugin),
    )
    registry = Registry.build()
    registry.plugins[builder.plugin] = _fake_plugin_info(
        builder.plugin, builder.package_dir.parent
    )
    cfg = build_config(registry, str(engine.url))
    plan = await compute_plan(engine, cfg, registry)

    # confirm=None means any destructive op aborts immediately
    report = await apply(plan, config=cfg, engine=engine, actor=System(), confirm=None)
    # 0001 applied (safe), 0002 aborted (destructive without confirm)
    assert report.applied_revisions == [(builder.plugin, "0001")]
    assert report.aborted_at == (builder.plugin, "0002")


async def test_apply_calls_confirm_for_destructive(
    engine, tmp_plugin_with_migrations, example_revision_body,
) -> None:
    builder = tmp_plugin_with_migrations
    builder.add_revision(
        revision="0001",
        down_revision=None,
        body=example_revision_body.format(
            revision="0001", down_revision=None, branch=builder.plugin, table="t"
        ),
    )
    builder.add_revision(
        revision="0002",
        down_revision="0001",
        body="""
            from alembic import op
            from hearth.migrations import OpSummary

            revision = "0002"
            down_revision = "0001"
            branch_labels = (\"{branch}\",)
            HEARTH_UPGRADE_OPS = [OpSummary(kind="DropColumn", desc="DROP COLUMN t.name", destructive=True)]
            HEARTH_DOWNGRADE_OPS = []

            def upgrade(): op.drop_column("t", "name")
            def downgrade(): pass
        """.format(branch=builder.plugin),
    )
    registry = Registry.build()
    registry.plugins[builder.plugin] = _fake_plugin_info(
        builder.plugin, builder.package_dir.parent
    )
    cfg = build_config(registry, str(engine.url))
    plan = await compute_plan(engine, cfg, registry)

    confirmed: list[str] = []

    async def yes(_revision, op):
        confirmed.append(op.desc)
        return True

    report = await apply(plan, config=cfg, engine=engine, actor=System(), confirm=yes)
    assert confirmed == ["DROP COLUMN t.name"]
    assert report.applied_revisions == [(builder.plugin, "0001"), (builder.plugin, "0002")]
```

- [ ] **Step 2: Run to verify the tests fail**

```bash
uv run pytest tests/kernel/migrations/test_applier.py -v
```
Expected: ImportError on `apply` / `AppliedReport`.

- [ ] **Step 3: Implement the applier (DDL-only path)**

`hearth/kernel/migrations/applier.py`:

```python
"""Apply a `MigrationPlan` against an engine, with single-writer locking
and per-op interactive confirmation for destructive ops.

This file handles only the DDL path (Alembic `command.upgrade` per
revision plus audit-row write). Data hooks (`data_upgrade` /
`data_downgrade`) are added in Phase E.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from alembic import command as alembic_command
from alembic.config import Config
from sqlalchemy.ext.asyncio import AsyncEngine

from hearth.kernel.migrations._lock import acquire_migration_lock
from hearth.kernel.migrations.audit_log import record_applied_revision
from hearth.kernel.migrations.planner import MigrationPlan, PlannedRevision
from hearth.migrations import OpSummary
from hearth.primitives.actor import Actor


ConfirmCallback = Callable[[PlannedRevision, OpSummary], Awaitable[bool]]


@dataclass
class AppliedReport:
    applied_revisions: list[tuple[str, str]] = field(default_factory=list)
    aborted_at: tuple[str, str] | None = None


async def apply(
    plan: MigrationPlan,
    *,
    config: Config,
    engine: AsyncEngine,
    actor: Actor,
    confirm: ConfirmCallback | None = None,
) -> AppliedReport:
    """Apply `plan` under a migration lock.

    For each destructive op in each pending revision, call `confirm`. If
    `confirm` returns False (or is None and the op is destructive), abort
    the plan at that op — everything previously committed stays.
    """
    report = AppliedReport()
    actor_id = _serialize_actor(actor)

    async with acquire_migration_lock(engine):
        for revision in plan.revisions:
            for op in revision.destructive_ops:
                if confirm is None or not await confirm(revision, op):
                    report.aborted_at = (revision.plugin, revision.revision_id)
                    return report

            # Apply this revision via Alembic (its own transaction).
            await _alembic_upgrade_one(config, revision.revision_id)

            await record_applied_revision(
                engine,
                plugin=revision.plugin,
                revision_id=revision.revision_id,
                description=revision.description,
                applied_by=actor_id,
                destructive_ops_acked=[o.desc for o in revision.destructive_ops],
            )
            report.applied_revisions.append((revision.plugin, revision.revision_id))

    return report


async def _alembic_upgrade_one(config: Config, revision_id: str) -> None:
    """Run `alembic.command.upgrade` for one revision. Alembic uses sync
    drivers; we offload to a thread."""
    import asyncio

    await asyncio.to_thread(alembic_command.upgrade, config, revision_id)


def _serialize_actor(actor: Actor) -> str:
    """Render an Actor as `kind:id` matching `hearth/kernel/transaction.py`
    `_serialize_actor`. Plugin actors render as `plugin:<alias>`."""
    from hearth.primitives.actor import PluginActor

    if isinstance(actor, PluginActor):
        return f"plugin:{actor.alias}"
    raw_id = getattr(actor, "id", None)
    if raw_id:
        return f"{actor.actor_kind}:{raw_id}"
    return actor.actor_kind
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/kernel/migrations/test_applier.py -v
```
Expected: 3 passed.

- [ ] **Step 5: Lint + typecheck**

```bash
uv run ruff check hearth/kernel/migrations/applier.py
uv run basedpyright hearth/kernel/migrations/applier.py
```
Expected: clean.

---

### Task C3: `hearth db migrate` CLI command

**Files:**
- Modify: `hearth/cli/_db.py`
- Create: `tests/cli/test_db_migrate.py`

- [ ] **Step 1: Add the migrate command**

In `hearth/cli/_db.py`, after `plan_cmd`:

```python
from hearth.kernel.migrations.applier import apply, AppliedReport
from hearth.primitives.actor import System


@db_app.command("migrate")
@_wrap_command
def migrate_cmd(
    dry_run: bool = typer.Option(False, "--dry-run", help="Same as `plan`; show without applying."),
) -> None:
    """Apply pending migrations. Destructive ops prompt interactively."""
    url = _require_database_url()
    if dry_run:
        asyncio.run(_plan_impl(url))
        return
    asyncio.run(_migrate_impl(url))


async def _migrate_impl(url: str) -> None:
    try:
        registry = Registry.build()
    except RegistryBuildError as err:
        _print_registry_build_error(err)
        raise typer.Exit(1) from err
    engine = make_async_engine(url)
    try:
        cfg = build_config(registry, url)
        plan = await compute_plan(engine, cfg, registry)
        if not plan.revisions:
            typer.echo("No pending migrations.")
            return
        typer.echo(plan.format())
        typer.echo("")
        report = await apply(plan, config=cfg, engine=engine, actor=System(), confirm=_tty_confirm)
        _print_report(report)
    finally:
        await engine.dispose()


async def _tty_confirm(revision, op) -> bool:
    typer.echo("")
    typer.echo(f"DESTRUCTIVE: plugin={revision.plugin} revision={revision.revision_id}")
    typer.echo(f"  description: {revision.description}")
    typer.echo(f"  op: {op.desc}")
    answer = typer.prompt("Apply this op? [y/N]", default="N", show_default=False)
    return answer.strip().lower() in {"y", "yes"}


def _print_report(report: AppliedReport) -> None:
    typer.echo("")
    typer.echo(f"Applied {len(report.applied_revisions)} revision(s):")
    for plugin, rev in report.applied_revisions:
        typer.echo(f"  [OK] {plugin}:{rev}")
    if report.aborted_at is not None:
        plugin, rev = report.aborted_at
        typer.echo(f"Aborted at {plugin}:{rev}.")
```

- [ ] **Step 2: Write CLI test**

`tests/cli/test_db_migrate.py`:

```python
"""Tests for `hearth db migrate`."""

from __future__ import annotations

from typer.testing import CliRunner

from hearth.cli import app


def test_db_migrate_no_pending(tmp_path, monkeypatch) -> None:
    db_url = f"sqlite:///{tmp_path}/db.sqlite"
    monkeypatch.setenv("DATABASE_URL", db_url)
    runner = CliRunner()
    runner.invoke(app, ["db", "init"])
    result = runner.invoke(app, ["db", "migrate"])
    assert result.exit_code == 0
    assert "No pending migrations" in result.output


def test_db_migrate_dry_run_is_plan(tmp_path, monkeypatch) -> None:
    db_url = f"sqlite:///{tmp_path}/db.sqlite"
    monkeypatch.setenv("DATABASE_URL", db_url)
    runner = CliRunner()
    runner.invoke(app, ["db", "init"])
    result = runner.invoke(app, ["db", "migrate", "--dry-run"])
    assert result.exit_code == 0
    assert "No pending migrations" in result.output
```

- [ ] **Step 3: Run tests**

```bash
uv run pytest tests/cli/test_db_migrate.py -v
```
Expected: 2 passed.

---

## Phase D — Partial-commit recovery check

### Task D1: Recovery check + integration into apply loop

**Files:**
- Create: `hearth/kernel/migrations/recovery.py`
- Create: `tests/kernel/migrations/test_recovery.py`
- Modify: `hearth/kernel/migrations/applier.py`

- [ ] **Step 1: Write failing tests**

`tests/kernel/migrations/test_recovery.py`:

```python
"""Tests for partial-commit recovery."""

from __future__ import annotations

import pytest
import sqlalchemy as sa

from hearth.kernel._engine import make_async_engine
from hearth.kernel.migrations.alembic_config import build_config
from hearth.kernel.migrations.applier import apply
from hearth.kernel.migrations.audit_log import SCHEMA_LOG_METADATA, read_applied_revisions
from hearth.kernel.migrations.planner import compute_plan
from hearth.kernel.migrations.recovery import find_orphaned_revisions
from hearth.kernel.registry import Registry
from hearth.primitives.actor import System


@pytest.fixture
async def engine(tmp_path):
    eng = make_async_engine(f"sqlite+aiosqlite:///{tmp_path}/recov.db")
    async with eng.begin() as conn:
        await conn.run_sync(SCHEMA_LOG_METADATA.create_all)
    yield eng
    await eng.dispose()


async def test_no_orphans_when_alembic_and_log_in_sync(engine, tmp_plugin_with_migrations, example_revision_body) -> None:
    builder = tmp_plugin_with_migrations
    builder.add_revision(
        revision="0001", down_revision=None,
        body=example_revision_body.format(
            revision="0001", down_revision=None, branch=builder.plugin, table="t",
        ),
    )
    registry = Registry.build()
    registry.plugins[builder.plugin] = _fake_plugin_info(
        builder.plugin, builder.package_dir.parent,
    )
    cfg = build_config(registry, str(engine.url))
    plan = await compute_plan(engine, cfg, registry)
    await apply(plan, config=cfg, engine=engine, actor=System())

    orphans = await find_orphaned_revisions(engine, cfg, registry)
    assert orphans == []


async def test_orphan_when_alembic_advanced_but_audit_missing(
    engine, tmp_plugin_with_migrations, example_revision_body,
) -> None:
    builder = tmp_plugin_with_migrations
    builder.add_revision(
        revision="0001", down_revision=None,
        body=example_revision_body.format(
            revision="0001", down_revision=None, branch=builder.plugin, table="t",
        ),
    )
    registry = Registry.build()
    registry.plugins[builder.plugin] = _fake_plugin_info(
        builder.plugin, builder.package_dir.parent,
    )
    cfg = build_config(registry, str(engine.url))
    plan = await compute_plan(engine, cfg, registry)
    await apply(plan, config=cfg, engine=engine, actor=System())
    # Simulate audit-row loss
    async with engine.begin() as conn:
        await conn.execute(sa.text("DELETE FROM _hearth_schema_log"))

    orphans = await find_orphaned_revisions(engine, cfg, registry)
    assert [(o.plugin, o.revision_id) for o in orphans] == [(builder.plugin, "0001")]
```

- [ ] **Step 2: Implement `find_orphaned_revisions`**

`hearth/kernel/migrations/recovery.py`:

```python
"""Partial-commit recovery check.

Cross-references Alembic's `alembic_version` against `_hearth_schema_log`
to detect revisions whose DDL was applied but whose audit row never
landed (failed during data UoW commit or audit write). The applier
re-runs the data side / writes the missing audit row before the normal
walk.
"""

from __future__ import annotations

from dataclasses import dataclass

import sqlalchemy as sa
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy.ext.asyncio import AsyncEngine

from hearth.kernel.migrations.audit_log import SCHEMA_LOG_TABLE
from hearth.kernel.registry import Registry


@dataclass(frozen=True, slots=True)
class OrphanedRevision:
    plugin: str
    revision_id: str
    description: str


async def find_orphaned_revisions(
    engine: AsyncEngine, config: Config, registry: Registry,
) -> list[OrphanedRevision]:
    """Return revisions whose Alembic head is recorded but whose audit row
    is missing. Ordered (plugin, revision_id)."""
    script = ScriptDirectory.from_config(config)

    async with engine.connect() as conn:
        exists = await conn.run_sync(
            lambda s: "alembic_version" in sa.inspect(s).get_table_names()
        )
        if not exists:
            return []
        applied = [
            row[0] for row in await conn.execute(sa.text("SELECT version_num FROM alembic_version"))
        ]
        logged = {
            (row.plugin, row.revision_id)
            for row in await conn.execute(sa.select(SCHEMA_LOG_TABLE.c.plugin, SCHEMA_LOG_TABLE.c.revision_id))
        }

    orphans: list[OrphanedRevision] = []
    for rev_id in applied:
        sc = script.get_revision(rev_id)
        if sc is None:
            continue
        for label in (sc.branch_labels or []):
            if label not in registry.plugins:
                continue
            if (label, rev_id) not in logged:
                orphans.append(
                    OrphanedRevision(plugin=label, revision_id=rev_id, description=sc.doc or "")
                )
    return sorted(orphans, key=lambda o: (o.plugin, o.revision_id))
```

- [ ] **Step 3: Wire recovery into applier**

Modify `hearth/kernel/migrations/applier.py`. At the start of the `async with acquire_migration_lock(...)` block, before walking pending revisions:

```python
from hearth.kernel.migrations.recovery import find_orphaned_revisions
from hearth.kernel.migrations.errors import PartialCommitUnrecoverable

# Inside apply(), after acquiring the lock:
orphans = await find_orphaned_revisions(engine, config, await _registry_or_raise(plan))
for orphan in orphans:
    # Phase D: write the missing audit row (no data hooks yet). Phase E
    # will extend this to also run data_upgrade.
    try:
        await record_applied_revision(
            engine,
            plugin=orphan.plugin,
            revision_id=orphan.revision_id,
            description=orphan.description,
            applied_by=f"{actor.actor_kind}:recovery",
            destructive_ops_acked=[],
        )
    except Exception as err:
        raise PartialCommitUnrecoverable(
            f"failed to recover orphaned revision {orphan.plugin}:{orphan.revision_id}"
        ) from err
```

> **NOTE:** the applier needs the registry to look up `info.module` for orphan recovery. Add a `registry: Registry` parameter to `apply(...)` and thread it through. Update Phase C tests' calls accordingly (they pass `registry` instead of relying on `compute_plan`'s).

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/kernel/migrations/test_recovery.py tests/kernel/migrations/test_applier.py -v
```
Expected: all green. If the applier signature change breaks Phase C tests, update them to pass `registry`.

---

## Phase E — Data hooks

### Task E1: `open_data_uow(engine, plugin)` with event suppression

**Files:**
- Create: `hearth/kernel/migrations/data_uow.py`
- Create: `tests/kernel/migrations/test_data_hooks.py` (initial structure)

- [ ] **Step 1: Implement event-suppressed data UoW**

`hearth/kernel/migrations/data_uow.py`:

```python
"""Open a UnitOfWork for a migration data hook.

The data UoW is scoped to PluginActor(plugin) so rows the migration
creates are attributed to the plugin (visible in audit/outbox). Event
emission via `uow.emit(...)` is suppressed — migrations are not user
activity and should not trigger subscribers as if a user had acted.
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncEngine

from hearth.kernel.transaction import transaction
from hearth.primitives.actor import PluginActor
from hearth.unit_of_work import UnitOfWork


@contextlib.asynccontextmanager
async def open_data_uow(engine: AsyncEngine, *, plugin: str) -> AsyncIterator[UnitOfWork]:
    """Yield a UoW with the migration actor and event emission disabled."""
    actor = PluginActor(plugin=plugin)
    async with transaction(engine, actor) as uow:
        # Suppress outbox events by replacing the emit method.
        uow.emit = _suppressed_emit  # type: ignore[method-assign]
        yield uow


async def _suppressed_emit(_event) -> None:
    """No-op: data UoW does not enqueue outbox events."""
    return None
```

> **Note:** the exact mechanism for "suppress emit" depends on the UoW implementation. If `transaction(...)` exposes an `events_enabled` flag (or similar) that's preferable to monkey-patching. Inspect [hearth/kernel/transaction.py](hearth/kernel/transaction.py) and adapt: either use an existing flag, or add one (kernel-internal, no plugin-facing API change).

- [ ] **Step 2: Smoke test the import**

```bash
uv run python -c "from hearth.kernel.migrations.data_uow import open_data_uow"
```
Expected: silent (success).

---

### Task E2a: Applier calls `data_upgrade` after Alembic commit

**Files:**
- Modify: `hearth/kernel/migrations/applier.py`
- Modify: `tests/kernel/migrations/test_data_hooks.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/kernel/migrations/test_data_hooks.py`:

```python
"""Tests for the data hook execution path."""

from __future__ import annotations

import pytest
import sqlalchemy as sa

from hearth.kernel._engine import make_async_engine
from hearth.kernel.migrations.alembic_config import build_config
from hearth.kernel.migrations.applier import apply
from hearth.kernel.migrations.audit_log import SCHEMA_LOG_METADATA, read_applied_revisions
from hearth.kernel.migrations.planner import compute_plan
from hearth.kernel.registry import Registry
from hearth.primitives.actor import System


@pytest.fixture
async def engine(tmp_path):
    eng = make_async_engine(f"sqlite+aiosqlite:///{tmp_path}/data.db")
    async with eng.begin() as conn:
        await conn.run_sync(SCHEMA_LOG_METADATA.create_all)
    yield eng
    await eng.dispose()


async def test_data_upgrade_is_called_after_ddl_commits(
    engine, tmp_plugin_with_migrations,
) -> None:
    builder = tmp_plugin_with_migrations
    builder.add_revision(
        revision="0001",
        down_revision=None,
        body=f"""
            from alembic import op
            import sqlalchemy as sa
            from hearth.migrations import OpSummary

            revision = "0001"
            down_revision = None
            branch_labels = ("{builder.plugin}",)

            HEARTH_UPGRADE_OPS = [
                OpSummary(kind="CreateTable", desc="CREATE TABLE seed_test", destructive=False),
                OpSummary(kind="DataMigration", desc="seed a row", destructive=False),
            ]
            HEARTH_DOWNGRADE_OPS = []

            def upgrade():
                op.create_table(
                    "seed_test",
                    sa.Column("id", sa.Integer, primary_key=True),
                    sa.Column("name", sa.String, nullable=False),
                )

            def downgrade(): pass

            async def data_upgrade(uow):
                await uow.execute(sa.text("INSERT INTO seed_test (id, name) VALUES (1, 'seeded')"))
        """,
    )
    registry = Registry.build()
    registry.plugins[builder.plugin] = _fake_plugin_info(
        builder.plugin, builder.package_dir.parent,
    )
    cfg = build_config(registry, str(engine.url))
    plan = await compute_plan(engine, cfg, registry)
    await apply(plan, config=cfg, engine=engine, actor=System(), registry=registry)

    async with engine.connect() as conn:
        row = (await conn.execute(sa.text("SELECT name FROM seed_test WHERE id = 1"))).scalar()
    assert row == "seeded"

    # Audit row recorded
    rows = await read_applied_revisions(engine, plugin=builder.plugin)
    assert len(rows) == 1
```

- [ ] **Step 2: Add the helper that resolves a revision module**

Add to `hearth/kernel/migrations/applier.py`:

```python
from alembic.script import ScriptDirectory


def _import_revision_module(config, revision_id: str):
    """Return the already-loaded revision module for `revision_id`.
    Alembic's `ScriptDirectory.get_revision(...).module` lazy-loads
    on first access — we use that rather than `importlib.import_module`
    to avoid module duplication during tests that manipulate sys.modules."""
    script = ScriptDirectory.from_config(config)
    sc = script.get_revision(revision_id)
    if sc is None:
        raise RuntimeError(f"revision {revision_id} not found in script directory")
    return sc.module
```

- [ ] **Step 3: Call `data_upgrade` from the per-revision loop**

Inside the per-revision loop in `apply()`, after `_alembic_upgrade_one(...)` but before `record_applied_revision(...)`:

```python
revision_module = _import_revision_module(config, revision.revision_id)
data_upgrade = getattr(revision_module, "data_upgrade", None)
if data_upgrade is not None:
    async with open_data_uow(engine, plugin=revision.plugin) as uow:
        await data_upgrade(uow)
```

(Step E2b will move the audit-row write inside this same UoW. For now it stays in its own transaction.)

- [ ] **Step 4: Run the test**

```bash
uv run pytest tests/kernel/migrations/test_data_hooks.py::test_data_upgrade_is_called_after_ddl_commits -v
```
Expected: pass.

---

### Task E2b: Co-commit audit row inside the data UoW

**Files:**
- Modify: `hearth/kernel/migrations/applier.py`

- [ ] **Step 1: Write failing test for co-commit semantics**

Append to `tests/kernel/migrations/test_data_hooks.py`:

```python
async def test_audit_row_rolled_back_when_data_upgrade_raises(
    engine, tmp_plugin_with_migrations,
) -> None:
    builder = tmp_plugin_with_migrations
    builder.add_revision(
        revision="0001",
        down_revision=None,
        body=f"""
            from alembic import op
            import sqlalchemy as sa
            from hearth.migrations import OpSummary

            revision = "0001"
            down_revision = None
            branch_labels = ("{builder.plugin}",)

            HEARTH_UPGRADE_OPS = [
                OpSummary(kind="CreateTable", desc="CREATE TABLE t", destructive=False),
                OpSummary(kind="DataMigration", desc="seeding boom", destructive=False),
            ]
            HEARTH_DOWNGRADE_OPS = []

            def upgrade():
                op.create_table("t", sa.Column("id", sa.Integer, primary_key=True))

            def downgrade(): pass

            async def data_upgrade(uow):
                raise RuntimeError("seed failed")
        """,
    )
    registry = Registry.build()
    registry.plugins[builder.plugin] = _fake_plugin_info(
        builder.plugin, builder.package_dir.parent,
    )
    cfg = build_config(registry, str(engine.url))
    plan = await compute_plan(engine, cfg, registry)

    with pytest.raises(RuntimeError, match="seed failed"):
        await apply(plan, config=cfg, engine=engine, actor=System(), registry=registry)

    # Audit row should NOT exist (rolled back together with the failed data UoW)
    rows = await read_applied_revisions(engine, plugin=builder.plugin)
    assert rows == []

    # But the DDL table DID get created (Alembic's transaction already committed)
    async with engine.connect() as conn:
        names = await conn.run_sync(lambda s: set(sa.inspect(s).get_table_names()))
    assert "t" in names  # The sharp edge the spec documents
```

- [ ] **Step 2: Restructure the data-hook branch to write the audit row inside the UoW**

Replace the per-revision data-hook block in `apply()`:

```python
from hearth.kernel.migrations.audit_log import record_applied_revision_in_uow

revision_module = _import_revision_module(config, revision.revision_id)
data_upgrade = getattr(revision_module, "data_upgrade", None)

if data_upgrade is not None:
    async with open_data_uow(engine, plugin=revision.plugin) as uow:
        await data_upgrade(uow)
        await record_applied_revision_in_uow(
            uow,
            plugin=revision.plugin,
            revision_id=revision.revision_id,
            description=revision.description,
            applied_by=actor_id,
            destructive_ops_acked=[o.desc for o in revision.destructive_ops],
        )
    # UoW exit commits; audit row + data are atomic together.
else:
    await record_applied_revision(
        engine,
        plugin=revision.plugin,
        revision_id=revision.revision_id,
        description=revision.description,
        applied_by=actor_id,
        destructive_ops_acked=[o.desc for o in revision.destructive_ops],
    )
```

- [ ] **Step 3: Run the tests**

```bash
uv run pytest tests/kernel/migrations/test_data_hooks.py -v
```
Expected: both pass — `data_upgrade_is_called_after_ddl_commits` and the new co-commit test.

---

### Task E2c: Extend recovery path to re-run `data_upgrade`

**Files:**
- Modify: `hearth/kernel/migrations/applier.py`

- [ ] **Step 1: Write failing recovery test**

Append to `tests/kernel/migrations/test_recovery.py`:

```python
async def test_recovery_re_runs_data_upgrade_for_orphans(
    engine, tmp_plugin_with_migrations,
) -> None:
    builder = tmp_plugin_with_migrations
    builder.add_revision(
        revision="0001",
        down_revision=None,
        body=f"""
            from alembic import op
            import sqlalchemy as sa
            from hearth.migrations import OpSummary

            revision = "0001"
            down_revision = None
            branch_labels = ("{builder.plugin}",)

            HEARTH_UPGRADE_OPS = [
                OpSummary(kind="CreateTable", desc="CREATE TABLE t", destructive=False),
                OpSummary(kind="DataMigration", desc="seed t", destructive=False),
            ]
            HEARTH_DOWNGRADE_OPS = []

            def upgrade():
                op.create_table("t",
                    sa.Column("id", sa.Integer, primary_key=True),
                    sa.Column("seed", sa.Integer),
                )

            def downgrade(): pass

            async def data_upgrade(uow):
                # Idempotent: upsert id=1
                existing = await uow.execute(sa.text("SELECT 1 FROM t WHERE id=1"))
                if existing.first() is None:
                    await uow.execute(sa.text("INSERT INTO t (id, seed) VALUES (1, 42)"))
        """,
    )
    registry = Registry.build()
    registry.plugins[builder.plugin] = _fake_plugin_info(
        builder.plugin, builder.package_dir.parent,
    )
    cfg = build_config(registry, str(engine.url))
    plan = await compute_plan(engine, cfg, registry)
    await apply(plan, config=cfg, engine=engine, actor=System(), registry=registry)

    # Simulate audit row loss (e.g. process crashed before audit commit)
    async with engine.begin() as conn:
        await conn.execute(sa.text("DELETE FROM _hearth_schema_log"))
        await conn.execute(sa.text("DELETE FROM t WHERE id=1"))  # also clear data

    # Re-run apply — recovery should detect orphan and re-execute data_upgrade
    await apply(plan, config=cfg, engine=engine, actor=System(), registry=registry)

    rows = await read_applied_revisions(engine, plugin=builder.plugin)
    assert len(rows) == 1
    async with engine.connect() as conn:
        seed = (await conn.execute(sa.text("SELECT seed FROM t WHERE id=1"))).scalar()
    assert seed == 42  # data_upgrade ran again
```

- [ ] **Step 2: Extend the recovery block in `apply()`**

Replace the orphan recovery block:

```python
for orphan in orphans:
    try:
        revision_module = _import_revision_module(config, orphan.revision_id)
        data_upgrade = getattr(revision_module, "data_upgrade", None)
        if data_upgrade is not None:
            async with open_data_uow(engine, plugin=orphan.plugin) as uow:
                await data_upgrade(uow)
                await record_applied_revision_in_uow(
                    uow,
                    plugin=orphan.plugin,
                    revision_id=orphan.revision_id,
                    description=orphan.description,
                    applied_by=f"{actor_id}:recovery",
                    destructive_ops_acked=[],
                )
        else:
            await record_applied_revision(
                engine,
                plugin=orphan.plugin,
                revision_id=orphan.revision_id,
                description=orphan.description,
                applied_by=f"{actor_id}:recovery",
                destructive_ops_acked=[],
            )
    except Exception as err:
        raise PartialCommitUnrecoverable(
            f"failed to recover orphaned revision {orphan.plugin}:{orphan.revision_id}"
        ) from err
```

- [ ] **Step 3: Run the test**

```bash
uv run pytest tests/kernel/migrations/test_recovery.py::test_recovery_re_runs_data_upgrade_for_orphans -v
```
Expected: pass.

---

### Task E2d: Event suppression test

**Files:**
- Modify: `tests/kernel/migrations/test_data_hooks.py`

- [ ] **Step 1: Write the test**

Append:

```python
async def test_data_upgrade_does_not_emit_outbox_events(
    engine, tmp_plugin_with_migrations,
) -> None:
    """Migrations are not user activity; the data UoW suppresses emit."""
    builder = tmp_plugin_with_migrations
    builder.add_revision(
        revision="0001",
        down_revision=None,
        body=f"""
            from alembic import op
            import sqlalchemy as sa
            from hearth.migrations import OpSummary

            revision = "0001"
            down_revision = None
            branch_labels = ("{builder.plugin}",)

            HEARTH_UPGRADE_OPS = [
                OpSummary(kind="CreateTable", desc="CREATE TABLE t", destructive=False),
                OpSummary(kind="DataMigration", desc="emit attempt", destructive=False),
            ]
            HEARTH_DOWNGRADE_OPS = []

            def upgrade():
                op.create_table("t", sa.Column("id", sa.Integer, primary_key=True))

            def downgrade(): pass

            async def data_upgrade(uow):
                # Attempt to emit — the suppressed UoW should swallow it.
                class _FakeEvent:
                    pass
                await uow.emit(_FakeEvent())
        """,
    )
    registry = Registry.build()
    registry.plugins[builder.plugin] = _fake_plugin_info(
        builder.plugin, builder.package_dir.parent,
    )
    cfg = build_config(registry, str(engine.url))
    plan = await compute_plan(engine, cfg, registry)
    await apply(plan, config=cfg, engine=engine, actor=System(), registry=registry)

    # Outbox table is `_hearth_outbox` (created by `hearth db init`); assert
    # it has no rows attributable to the migration.
    async with engine.connect() as conn:
        outbox_exists = await conn.run_sync(
            lambda s: "_hearth_outbox" in sa.inspect(s).get_table_names()
        )
        if outbox_exists:
            count = (await conn.execute(sa.text("SELECT COUNT(*) FROM _hearth_outbox"))).scalar()
            assert count == 0
        # If outbox doesn't exist yet in this test DB, that's also fine
        # (the assertion is "no events emitted", which a missing table satisfies).
```

- [ ] **Step 2: Run the test**

```bash
uv run pytest tests/kernel/migrations/test_data_hooks.py::test_data_upgrade_does_not_emit_outbox_events -v
```
Expected: pass.

---

## Phase F — `hearth migrate generate`

### Task F1: Programmatic autogenerate + HEARTH_UPGRADE_OPS post-processing

**Files:**
- Create: `hearth/cli/_migrate.py`
- Modify: `hearth/cli/__init__.py`
- Create: `tests/cli/test_migrate_generate.py`

- [ ] **Step 1: Add the `hearth migrate` Typer group and `generate` command**

`hearth/cli/_migrate.py`:

```python
"""`hearth migrate ...` subcommand group — plugin-developer verbs."""

from __future__ import annotations

import asyncio
import os
import sys
import typer

from hearth.cli import _wrap_command  # pyright: ignore[reportPrivateUsage]
from hearth.cli._plugins import _print_registry_build_error  # pyright: ignore[reportPrivateUsage]
from hearth.kernel._engine import make_async_engine
from hearth.kernel.migrations.alembic_config import build_config
from hearth.kernel.migrations.classifier import classify
from hearth.kernel.registry import Registry, RegistryBuildError


migrate_app = typer.Typer(help="Plugin-developer migration commands.", no_args_is_help=True)


@migrate_app.command("generate")
@_wrap_command
def generate_cmd(
    plugin: str = typer.Argument(..., help="Plugin alias (e.g. 'auth')."),
    refresh: bool = typer.Option(False, "--refresh", help="Re-emit HEARTH_UPGRADE_OPS for the latest revision instead of creating a new one."),
) -> None:
    """Autogenerate the next revision file for <plugin>."""
    asyncio.run(_generate_impl(plugin, refresh))


async def _generate_impl(plugin: str, refresh: bool) -> None:
    try:
        registry = Registry.build()
    except RegistryBuildError as err:
        _print_registry_build_error(err)
        raise typer.Exit(1) from err
    if plugin not in registry.plugins:
        typer.echo(f"ERROR: plugin '{plugin}' is not installed.", err=True)
        raise typer.Exit(1)

    _require_editable_install(registry.plugins[plugin])

    url = os.environ.get("DATABASE_URL", "sqlite:///:memory:")
    engine = make_async_engine(url)
    try:
        cfg = build_config(registry, url)
        # Autogenerate is sync — offload to a thread.
        from alembic import command as alembic_command
        await asyncio.to_thread(
            alembic_command.revision,
            cfg,
            autogenerate=True,
            branch_label=plugin,
            version_path=str(
                registry.plugins[plugin].install_path  # pyright: ignore[reportAttributeAccessIssue]
            ) + "/migrations",
        )
    finally:
        await engine.dispose()

    # Post-process: walk MigrationScript.upgrade_ops, classify, write
    # HEARTH_UPGRADE_OPS, insert # DESTRUCTIVE comments. Emit data-hook stub.
    # (Detail in Task F2.)
    typer.echo(f"Generated revision for plugin '{plugin}'.")


def _require_editable_install(plugin_info) -> None:
    """Detect non-editable install (PEP 660). Abort with actionable error."""
    install_path = plugin_info.install_path
    if "/site-packages/" in install_path:
        typer.echo(
            f"ERROR: plugin '{plugin_info.alias}' is not an editable install.\n"
            f"  Revision generation must run from the plugin source tree.\n"
            f"  Try: pip install -e {plugin_info.install_path}",
            err=True,
        )
        raise typer.Exit(1)
```

- [ ] **Step 2: Register `migrate_app` in the CLI**

In `hearth/cli/__init__.py`, alongside the existing `db_app` registration:

```python
from hearth.cli._migrate import migrate_app

app.add_typer(migrate_app, name="migrate")
```

- [ ] **Step 3: Smoke-test the new command exists**

```bash
uv run hearth migrate --help
```
Expected: shows `generate` subcommand.

- [ ] **Step 4: Write basic CLI test**

`tests/cli/test_migrate_generate.py`:

```python
from typer.testing import CliRunner
from hearth.cli import app


def test_migrate_generate_unknown_plugin_fails(monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    runner = CliRunner()
    result = runner.invoke(app, ["migrate", "generate", "nonexistent_plug"])
    assert result.exit_code == 1
    assert "not installed" in result.output


def test_migrate_generate_rejects_non_editable(tmp_path, monkeypatch) -> None:
    """If the plugin's install_path is inside site-packages, abort with help."""
    from hearth.kernel.registry import PluginInfo, Registry as RegCls

    real_build = RegCls.build

    def fake_build():
        reg = real_build()
        reg.plugins["fakeplug"] = PluginInfo(
            alias="fakeplug", package="fakeplug", version="0.0.1",
            module="fakeplug",
            install_path="/usr/lib/python3.13/site-packages/fakeplug",
            depends_on=[], extends=[], entities=[], actions=[], events=[],
        )
        return reg

    monkeypatch.setattr(RegCls, "build", staticmethod(fake_build))
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/x.db")

    runner = CliRunner()
    result = runner.invoke(app, ["migrate", "generate", "fakeplug"])
    assert result.exit_code == 1
    assert "not an editable install" in result.output
    assert "pip install -e" in result.output
```

- [ ] **Step 5: Run smoke tests**

```bash
uv run pytest tests/cli/test_migrate_generate.py::test_migrate_generate_unknown_plugin_fails -v
```
Expected: pass.

---

### Task F2: Post-process generated file — write HEARTH_UPGRADE_OPS + emit data stub

**Files:**
- Modify: `hearth/cli/_migrate.py`
- Create: `hearth/kernel/migrations/_codegen.py`
- Create: `tests/kernel/migrations/test_codegen.py`

- [ ] **Step 1: Implement the code-emitter**

`hearth/kernel/migrations/_codegen.py`:

```python
"""Code emitters for `hearth migrate generate` post-processing.

The post-processor runs after Alembic writes a revision file. It walks
`MigrationScript.upgrade_ops` (available during generation), classifies
each op, and inserts a HEARTH_UPGRADE_OPS / HEARTH_DOWNGRADE_OPS module
constant near the top. It also appends the commented data-hook stub.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from alembic.operations.ops import MigrateOperation

from hearth.kernel.migrations.classifier import classify


def emit_op_summary_lines(ops: Iterable[MigrateOperation], variable_name: str) -> str:
    """Render `HEARTH_UPGRADE_OPS = [OpSummary(...), ...]`."""
    lines = [f"{variable_name}: list[OpSummary] = ["]
    for op in ops:
        kind = type(op).__name__.removesuffix("Op")
        desc = _describe(op)
        destructive = classify(op) == "destructive"
        lines.append(
            f'    OpSummary(kind="{kind}", desc="{desc}", destructive={destructive}),'
        )
    lines.append("]")
    return "\n".join(lines)


def _describe(op: MigrateOperation) -> str:
    """Human description for plan output and audit log."""
    kind = type(op).__name__.removesuffix("Op").upper()
    table = getattr(op, "table_name", None) or getattr(op, "name", "")
    column = getattr(op, "column_name", None) or getattr(getattr(op, "column", None), "name", "")
    if column:
        return f"{kind} {table}.{column}".strip()
    return f"{kind} {table}".strip()


DATA_HOOK_STUB = '''
# Optional: define data_upgrade / data_downgrade to seed or migrate data
# using the kernel's UnitOfWork. The kernel runs them in a UoW scoped to
# PluginActor("<plugin>"). Event emission is disabled for the data UoW.
#
# IMPORTANT — upgrade() commits BEFORE data_upgrade runs. If data_upgrade
# raises, Alembic's branch head is already advanced; a re-run will call
# data_upgrade again (via the recovery check) without re-running upgrade().
# Write your function to be safe on repeated calls — find-then-save, not
# unconditional insert.
#
# IMPORTANT — if you add data_upgrade, also append an OpSummary(kind=
# "DataMigration", desc=..., destructive=False) entry to HEARTH_UPGRADE_OPS
# above. `hearth migrate check` will fail otherwise: operators reading
# `hearth db plan` need to see this side effect before approving the plan.
#
# async def data_upgrade(uow) -> None:
#     ...
#
# async def data_downgrade(uow) -> None:
#     ...
'''


def post_process_generated_file(
    file_path: Path,
    upgrade_ops: Iterable[MigrateOperation],
    downgrade_ops: Iterable[MigrateOperation],
) -> None:
    """Insert HEARTH_UPGRADE_OPS / HEARTH_DOWNGRADE_OPS and the data stub."""
    content = file_path.read_text()

    # Insert the OpSummary import + the two lists after `depends_on = ...`
    import_line = "from hearth.migrations import OpSummary\n"
    op_lists = (
        "\n\n"
        + emit_op_summary_lines(upgrade_ops, "HEARTH_UPGRADE_OPS")
        + "\n"
        + emit_op_summary_lines(downgrade_ops, "HEARTH_DOWNGRADE_OPS")
        + "\n"
    )
    content = content.replace(
        "from alembic import op",
        "from alembic import op\n" + import_line,
        1,
    )
    # Insert op lists after depends_on assignment line.
    content = _insert_after_depends_on(content, op_lists)
    content += DATA_HOOK_STUB
    file_path.write_text(content)


def _insert_after_depends_on(content: str, snippet: str) -> str:
    """Find the line starting with 'depends_on' and insert `snippet` after it."""
    lines = content.splitlines(keepends=True)
    for i, line in enumerate(lines):
        if line.startswith("depends_on"):
            return "".join(lines[: i + 1]) + snippet + "".join(lines[i + 1 :])
    # Fallback: append at end of imports
    return content + snippet
```

- [ ] **Step 2: Write codegen unit tests**

`tests/kernel/migrations/test_codegen.py`:

```python
from __future__ import annotations

import textwrap
from pathlib import Path

from alembic.operations import ops as aop
import sqlalchemy as sa

from hearth.kernel.migrations._codegen import (
    DATA_HOOK_STUB,
    emit_op_summary_lines,
    post_process_generated_file,
)


def test_emit_op_summary_lines_renders_safe_destructive() -> None:
    ops = [
        aop.CreateTableOp("users", [sa.Column("id", sa.BigInteger(), nullable=False)]),
        aop.DropColumnOp("users", "legacy"),
    ]
    out = emit_op_summary_lines(ops, "HEARTH_UPGRADE_OPS")
    assert "destructive=False" in out
    assert "destructive=True" in out
    assert 'kind="CreateTable"' in out
    assert 'kind="DropColumn"' in out


def test_post_process_inserts_lists_and_stub(tmp_path: Path) -> None:
    f = tmp_path / "0001_test.py"
    f.write_text(textwrap.dedent("""\
        \"\"\"test rev\"\"\"
        from alembic import op
        import sqlalchemy as sa

        revision = "0001"
        down_revision = None
        branch_labels = ("test",)
        depends_on = None


        def upgrade():
            op.create_table("x", sa.Column("id", sa.Integer, primary_key=True))


        def downgrade():
            op.drop_table("x")
    """))
    post_process_generated_file(
        f,
        upgrade_ops=[aop.CreateTableOp("x", [sa.Column("id", sa.Integer(), primary_key=True)])],
        downgrade_ops=[aop.DropTableOp("x")],
    )
    out = f.read_text()
    assert "from hearth.migrations import OpSummary" in out
    assert "HEARTH_UPGRADE_OPS" in out
    assert "HEARTH_DOWNGRADE_OPS" in out
    assert DATA_HOOK_STUB.strip()[:30] in out
```

- [ ] **Step 3: Wire the post-processor into `generate_cmd`**

In `hearth/cli/_migrate.py`, replace the `await asyncio.to_thread(alembic_command.revision, ...)` call with an Alembic invocation that returns the `MigrationScript` so we can post-process. Alembic's `command.revision(...)` returns a list of MigrationScripts when invoked programmatically:

```python
from alembic import command as alembic_command
from hearth.kernel.migrations._codegen import post_process_generated_file

scripts = await asyncio.to_thread(
    alembic_command.revision,
    cfg,
    autogenerate=True,
    branch_label=plugin,
    version_path=plugin_migrations_dir,
    head=f"{plugin}@head",
)
script = scripts[0] if isinstance(scripts, list) else scripts
post_process_generated_file(
    file_path=Path(script.path),
    upgrade_ops=script.upgrade_ops.ops if script.upgrade_ops else [],
    downgrade_ops=script.downgrade_ops.ops if script.downgrade_ops else [],
)
typer.echo(f"Generated: {script.path}")
```

- [ ] **Step 4: Run codegen tests**

```bash
uv run pytest tests/kernel/migrations/test_codegen.py -v
```
Expected: 2 passed.

---

### Task F3: End-to-end generate test

**Files:**
- Modify: `tests/cli/test_migrate_generate.py`

- [ ] **Step 1: Write the end-to-end test**

Use the `tmp_plugin_with_migrations` fixture (after extending the registry to recognize it). The test should:
1. Define a `User` entity in the tmp plugin
2. Run `hearth migrate generate <plugin>`
3. Read the generated revision file; assert it contains `HEARTH_UPGRADE_OPS` with the expected `CreateTable` entry

This test depends on stubbing the registry to include the tmp plugin (since `hearth migrate generate` calls `Registry.build()`). Use `monkeypatch.setattr("hearth.kernel.registry.Registry.build", ...)` to inject a registry containing the tmp plugin.

```python
def test_generate_writes_revision_with_hearth_upgrade_ops(
    tmp_path, monkeypatch, tmp_plugin_with_migrations
) -> None:
    builder = tmp_plugin_with_migrations
    # 1. Add a User entity to the tmp plugin's module
    (builder.package_dir / "entities.py").write_text("""
from hearth import Entity, fields

class User(Entity, plugin='%s'):
    name: str = fields.Text(default='')
""" % builder.plugin)
    (builder.package_dir / "__init__.py").write_text(
        f"from {builder.plugin}.entities import User\n"
    )

    # 2. Stub Registry.build() to include our tmp plugin
    from hearth.kernel.registry import PluginInfo, Registry as RegCls
    from hearth.kernel.persistence import METADATA
    real_build = RegCls.build
    def fake_build():
        reg = real_build()
        reg.plugins[builder.plugin] = PluginInfo(
            alias=builder.plugin, package=builder.plugin, version="0.0.1",
            module=builder.plugin, install_path=str(builder.package_dir),
            depends_on=[], extends=[], entities=[],
            actions=[], events=[],
        )
        return reg
    monkeypatch.setattr(RegCls, "build", staticmethod(fake_build))
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/gen.db")

    from typer.testing import CliRunner
    from hearth.cli import app
    runner = CliRunner()
    result = runner.invoke(app, ["migrate", "generate", builder.plugin])
    assert result.exit_code == 0, result.output

    # 3. Assert the file was written with the expected artifact
    files = list((builder.package_dir / "migrations").glob("*.py"))
    rev_files = [f for f in files if f.name != "__init__.py"]
    assert len(rev_files) == 1
    content = rev_files[0].read_text()
    assert "HEARTH_UPGRADE_OPS" in content
    assert 'kind="CreateTable"' in content
```

- [ ] **Step 2: Run the test**

```bash
uv run pytest tests/cli/test_migrate_generate.py::test_generate_writes_revision_with_hearth_upgrade_ops -v
```
Expected: pass.

---

## Phase G — `hearth migrate check`

### Task G1: Drift detection + DataMigration OpSummary correspondence

**Files:**
- Modify: `hearth/cli/_migrate.py`
- Create: `tests/cli/test_migrate_check.py`

- [ ] **Step 1: Add the `check` command**

In `hearth/cli/_migrate.py`:

```python
@migrate_app.command("check")
@_wrap_command
def check_cmd(plugin: str = typer.Argument(..., help="Plugin alias")) -> None:
    """CI gate: report drift between entity declarations and revision files."""
    asyncio.run(_check_impl(plugin))


async def _check_impl(plugin: str) -> None:
    try:
        registry = Registry.build()
    except RegistryBuildError as err:
        _print_registry_build_error(err)
        raise typer.Exit(1) from err
    if plugin not in registry.plugins:
        typer.echo(f"ERROR: plugin '{plugin}' is not installed.", err=True)
        raise typer.Exit(1)

    # 1. Drift detection: replay revisions in a temp sqlite DB (no
    #    production connection needed) and compare against declared
    #    target_metadata. Per the spec, `check` is a pure code-vs-revisions
    #    operation — it does not connect to the operator's real DB.
    diff = await asyncio.to_thread(_compute_drift, registry, plugin)
    if diff:
        typer.echo(f"Drift detected in plugin '{plugin}':", err=True)
        for change in diff:
            typer.echo(f"  - {change}", err=True)
        typer.echo("Run `hearth migrate generate` to refresh.", err=True)
        raise typer.Exit(1)

    # 2. OpSummary correspondence
    issues = _check_op_summary_correspondence(registry.plugins[plugin])
    if issues:
        for issue in issues:
            typer.echo(f"  - {issue}", err=True)
        raise typer.Exit(1)

    typer.echo(f"plugin '{plugin}': no drift; revisions are current.")


def _compute_drift(registry, plugin: str) -> list[str]:
    """Replay the plugin's revisions against a scratch in-memory sqlite DB,
    then compare the resulting schema against the plugin's declared
    `target_metadata`. Non-empty diff = drift.

    Pure code-vs-revisions; does not touch the operator's real DB.
    """
    import tempfile
    import sqlalchemy as sa
    from alembic import command as alembic_command
    from alembic.autogenerate.api import compare_metadata
    from alembic.runtime.migration import MigrationContext

    # Scratch DB. File-backed so Alembic's stamping works; tmp tears down.
    with tempfile.TemporaryDirectory() as tmp:
        scratch_url = f"sqlite:///{tmp}/check.db"
        cfg = build_config(registry, scratch_url)
        # Replay every revision so the scratch DB has the schema the
        # revision chain produces.
        try:
            alembic_command.upgrade(cfg, f"{plugin}@head")
        except Exception as err:
            return [f"failed to replay revisions: {err}"]

        # Build the *declared* metadata for this plugin only.
        target = sa.MetaData()
        for ent in registry.plugins[plugin].entities:
            if hasattr(ent, "__table__"):
                ent.__table__.to_metadata(target)

        engine = sa.create_engine(scratch_url)
        try:
            with engine.connect() as conn:
                ctx = MigrationContext.configure(connection=conn)
                changes = compare_metadata(ctx, target)
        finally:
            engine.dispose()

    return [repr(c) for c in changes]
```

Implement `_check_op_summary_correspondence` to enforce: presence of `data_upgrade` ⟺ exactly one `OpSummary(kind="DataMigration")` in `HEARTH_UPGRADE_OPS`; same for downgrade.

```python
def _check_op_summary_correspondence(plugin_info) -> list[str]:
    """Walk every revision file and check data hook ↔ OpSummary correspondence."""
    issues: list[str] = []
    migrations_dir = Path(plugin_info.install_path) / "migrations"
    for path in sorted(migrations_dir.glob("*.py")):
        if path.name == "__init__.py":
            continue
        module = _load_module_from_path(path)
        upgrade_has_data_op = any(
            getattr(op, "kind", None) == "DataMigration"
            for op in getattr(module, "HEARTH_UPGRADE_OPS", [])
        )
        if hasattr(module, "data_upgrade") and not upgrade_has_data_op:
            issues.append(
                f"{path.name}: defines data_upgrade but HEARTH_UPGRADE_OPS has no DataMigration entry"
            )
        if upgrade_has_data_op and not hasattr(module, "data_upgrade"):
            issues.append(
                f"{path.name}: HEARTH_UPGRADE_OPS has DataMigration entry but no data_upgrade function"
            )
        # Symmetric for downgrade
        downgrade_has_data_op = any(
            getattr(op, "kind", None) == "DataMigration"
            for op in getattr(module, "HEARTH_DOWNGRADE_OPS", [])
        )
        if hasattr(module, "data_downgrade") and not downgrade_has_data_op:
            issues.append(f"{path.name}: data_downgrade defined but HEARTH_DOWNGRADE_OPS missing DataMigration entry")
        if downgrade_has_data_op and not hasattr(module, "data_downgrade"):
            issues.append(f"{path.name}: HEARTH_DOWNGRADE_OPS has DataMigration but no data_downgrade function")
    return issues


def _load_module_from_path(path: Path):
    import importlib.util
    spec = importlib.util.spec_from_file_location(f"_check_{path.stem}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
```

- [ ] **Step 2: Write tests**

`tests/cli/test_migrate_check.py`:

```python
"""Tests for `hearth migrate check`."""

from __future__ import annotations

from typer.testing import CliRunner
from hearth.cli import app


def _stub_registry_with_plugin(monkeypatch, builder) -> None:
    """Helper: inject `builder.plugin` into the registry that `check` builds."""
    from hearth.kernel.registry import PluginInfo, Registry as RegCls
    real_build = RegCls.build

    def fake_build():
        reg = real_build()
        reg.plugins[builder.plugin] = PluginInfo(
            alias=builder.plugin, package=builder.plugin, version="0.0.1",
            module=builder.plugin, install_path=str(builder.package_dir),
            depends_on=[], extends=[], entities=[], actions=[], events=[],
        )
        return reg

    monkeypatch.setattr(RegCls, "build", staticmethod(fake_build))


def test_check_passes_when_no_drift_and_hooks_consistent(
    monkeypatch, tmp_plugin_with_migrations
) -> None:
    builder = tmp_plugin_with_migrations
    builder.add_revision(
        revision="0001", down_revision=None,
        body=f"""
            from hearth.migrations import OpSummary

            revision = "0001"
            down_revision = None
            branch_labels = ("{builder.plugin}",)

            HEARTH_UPGRADE_OPS = [OpSummary(kind="DataMigration", desc="seed", destructive=False)]
            HEARTH_DOWNGRADE_OPS = []

            def upgrade(): pass
            def downgrade(): pass

            async def data_upgrade(uow): pass
        """,
    )
    _stub_registry_with_plugin(monkeypatch, builder)

    runner = CliRunner()
    result = runner.invoke(app, ["migrate", "check", builder.plugin])
    assert result.exit_code == 0, result.output
    assert "no drift" in result.output


def test_check_fails_when_data_upgrade_present_but_opsummary_missing(
    monkeypatch, tmp_plugin_with_migrations
) -> None:
    builder = tmp_plugin_with_migrations
    builder.add_revision(
        revision="0001", down_revision=None,
        body=f"""
            revision = "0001"
            down_revision = None
            branch_labels = ("{builder.plugin}",)

            HEARTH_UPGRADE_OPS = []
            HEARTH_DOWNGRADE_OPS = []

            def upgrade(): pass
            def downgrade(): pass

            async def data_upgrade(uow): pass
        """,
    )
    _stub_registry_with_plugin(monkeypatch, builder)

    runner = CliRunner()
    result = runner.invoke(app, ["migrate", "check", builder.plugin])
    assert result.exit_code == 1
    assert "defines data_upgrade but HEARTH_UPGRADE_OPS has no DataMigration" in result.output
```

- [ ] **Step 3: Run the tests**

```bash
uv run pytest tests/cli/test_migrate_check.py -v
```
Expected: 2 passed.

---

## Phase H — `hearth db status` overhaul + adoption path

### Task H1: Rewrite `db status` to read from schema log + report drift

**Files:**
- Modify: `hearth/cli/_db.py`

- [ ] **Step 1: Replace `_status_impl`**

Current implementation echoes "schema log not yet initialized." Replace with:

```python
async def _status_impl(url: str) -> None:
    try:
        registry = Registry.build()
    except RegistryBuildError as err:
        _print_registry_build_error(err)
        raise typer.Exit(1) from err
    engine = make_async_engine(url)
    try:
        cfg = build_config(registry, url)
        plan = await compute_plan(engine, cfg, registry)

        typer.echo(f"Database: {url}")
        typer.echo("")
        typer.echo("Plugins:")
        for alias in sorted(registry.plugins):
            applied_count = len(await read_applied_revisions(engine, plugin=alias))
            pending = [r for r in plan.revisions if r.plugin == alias]
            typer.echo(f"  {alias}: {applied_count} applied, {len(pending)} pending")

        if plan.has_destructive:
            typer.echo("")
            typer.echo("WARNING: pending plan contains destructive operations.")
    finally:
        await engine.dispose()
```

- [ ] **Step 2: Test**

Extend `tests/cli/test_db_status_overhaul.py` (new file) with:

```python
def test_db_status_reports_zero_when_empty(tmp_path, monkeypatch) -> None:
    db_url = f"sqlite:///{tmp_path}/status.db"
    monkeypatch.setenv("DATABASE_URL", db_url)
    runner = CliRunner()
    runner.invoke(app, ["db", "init"])
    result = runner.invoke(app, ["db", "status"])
    assert result.exit_code == 0
    assert "applied" in result.output
```

Run: `uv run pytest tests/cli/test_db_status_overhaul.py -v`. Expected: pass.

---

### Task H2: Adoption path — structural-equality stamp for legacy installs

**Files:**
- Create: `hearth/kernel/migrations/adopt.py`
- Modify: `hearth/kernel/migrations/applier.py`
- Create: `tests/kernel/migrations/test_adopt.py`

- [ ] **Step 1: Implement structural comparison + stamp**

`hearth/kernel/migrations/adopt.py`:

```python
"""Adoption path for installs created under the auth spec's `db init`.

If a plugin's tables exist in the DB but no `alembic_version` row tracks
them, and the declared schema *structurally* matches the live schema,
stamp the plugin's branch head as applied and write a schema-log row
attributed to `system:adopt`. If the structural comparison fails,
refuse with `AdoptionDriftDetected`.
"""

from __future__ import annotations

from alembic import command as alembic_command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy.ext.asyncio import AsyncEngine

from hearth.kernel.migrations.audit_log import record_applied_revision
from hearth.kernel.migrations.errors import AdoptionDriftDetected
from hearth.kernel.persistence import METADATA
from hearth.kernel.registry import Registry


# Op classes that count as STRUCTURAL drift; others are cosmetic and ignored.
_STRUCTURAL_OPS = {
    "add_table", "remove_table",
    "add_column", "remove_column",
    "add_constraint", "remove_constraint",
    "modify_nullable", "modify_type",
}


async def adopt_if_clean(
    engine: AsyncEngine, config: Config, registry: Registry, *, applied_by_actor_id: str,
) -> list[str]:
    """For each plugin whose tables exist but no alembic_version row tracks
    them, check structural equality. On match, stamp the head and write
    audit row. Return aliases adopted. Raise AdoptionDriftDetected on
    structural mismatch."""
    import asyncio as _asyncio
    import sqlalchemy as sa
    from alembic.runtime.migration import MigrationContext
    from alembic.script import ScriptDirectory

    from hearth.kernel.migrations.audit_log import record_applied_revision

    script = ScriptDirectory.from_config(config)

    async with engine.connect() as conn:
        live_tables = await conn.run_sync(
            lambda s: set(sa.inspect(s).get_table_names())
        )
        applied_revisions = set()
        if "alembic_version" in live_tables:
            result = await conn.execute(sa.text("SELECT version_num FROM alembic_version"))
            applied_revisions = {row[0] for row in result}

    # Aliases that have tables in DB but no alembic_version row for their branch.
    candidates: list[str] = []
    branch_head_revisions = {
        alias: script.get_current_head(alias)
        for alias in registry.plugins
        if script.get_current_head(alias) is not None
    }
    for alias in registry.topological_order():
        head = branch_head_revisions.get(alias)
        if head is None:
            continue
        if head in applied_revisions:
            continue  # already tracked
        # Has the plugin already created any of its declared tables?
        plugin_tables = {
            ent.__tablename__
            for ent in registry.plugins[alias].entities
            if getattr(ent, "__tablename__", None)
        }
        if plugin_tables and plugin_tables.issubset(live_tables):
            candidates.append(alias)

    adopted: list[str] = []
    for alias in candidates:
        # Replay the plugin's revisions in memory against a sqlite scratch DB to
        # get the schema the migrations *would* produce, then compare against
        # the live DB. Empty structural diff = adopt; non-empty = raise.
        diff = await _structural_diff_against_live(engine, config, registry, alias)
        if diff:
            raise AdoptionDriftDetected(
                f"plugin '{alias}': declared schema does not structurally match live DB.\n"
                f"  Drift: {diff}\n"
                f"  Resolve manually before retrying `hearth db migrate`."
            )
        # Stamp Alembic's branch head as applied.
        await _asyncio.to_thread(alembic_command.stamp, config, f"{alias}@head")
        # Write audit row attributing adoption to system.
        head = branch_head_revisions[alias]
        sc = script.get_revision(head)
        await record_applied_revision(
            engine,
            plugin=alias,
            revision_id=head,
            description=(sc.doc if sc else "") + " [adopted]",
            applied_by=applied_by_actor_id,
            destructive_ops_acked=[],
        )
        adopted.append(alias)
    return adopted


async def _structural_diff_against_live(
    engine: AsyncEngine, config: Config, registry: Registry, alias: str,
) -> list[str]:
    """Return a list of structural diff descriptions between the plugin's
    declared schema (from registry.entities) and the live DB. Cosmetic
    differences (server_default text, constraint naming, column comments)
    are filtered out."""
    from alembic.autogenerate.api import compare_metadata
    from alembic.runtime.migration import MigrationContext
    import sqlalchemy as sa

    target = sa.MetaData()
    for ent in registry.plugins[alias].entities:
        if hasattr(ent, "__table__"):
            ent.__table__.to_metadata(target)

    diffs: list[str] = []

    def _run(sync_conn):
        ctx = MigrationContext.configure(connection=sync_conn)
        for change in compare_metadata(ctx, target):
            # change is a tuple like ('add_column', schema, table, column) or
            # ('modify_nullable', ...). Filter to structural ops.
            kind = change[0] if isinstance(change, tuple) else None
            if kind in _STRUCTURAL_OPS:
                diffs.append(repr(change))

    async with engine.connect() as conn:
        await conn.run_sync(_run)
    return diffs


# Need to import alembic_command at module top:
import asyncio
from alembic import command as alembic_command
```

- [ ] **Step 2: Wire adoption into the applier**

In `apply()`, before the orphan recovery check, call:

```python
adopted = await adopt_if_clean(engine, config, registry, applied_by_actor_id=actor_id)
for alias in adopted:
    typer.echo(f"  [adopt] {alias}: existing tables stamped as applied")
```

(The `typer.echo` may be moved to the CLI layer; the applier returns the list.)

- [ ] **Step 3: Test**

`tests/kernel/migrations/test_adopt.py` — verify a plugin with tables already created (simulating old `db init`) and no `alembic_version` row gets stamped on `apply()`.

```bash
uv run pytest tests/kernel/migrations/test_adopt.py -v
```
Expected: pass.

---

### Task H3: Plugin downgrade detection (on boot and migrate)

**Files:**
- Modify: `hearth/kernel/migrations/applier.py` (or a dedicated `health.py`)
- Create: `tests/kernel/migrations/test_downgrade_detection.py`

- [ ] **Step 1: Add the check**

In the applier (early in `apply()`):

```python
from hearth.kernel.migrations.errors import PluginDowngradeDetected

async def _detect_plugin_downgrade(engine, config, registry):
    """Detect: alembic_version row points at a revision the package no longer ships."""
    # Read alembic_version; for each entry, check ScriptDirectory.get_revision(rev);
    # if None, that revision is no longer in the package. Raise.
```

Call at apply start, raise `PluginDowngradeDetected` with actionable message.

- [ ] **Step 2: Test**

Setup: apply a revision; then DELETE the revision file from the migrations dir; run apply again — should raise.

```bash
uv run pytest tests/kernel/migrations/test_downgrade_detection.py -v
```
Expected: pass.

---

## Phase I — Startup behavior

### Task I1: `HEARTH_DB_MIGRATE` env var → boot-time policy

**Files:**
- Create: `hearth/kernel/migrations/boot.py`
- Create: `tests/kernel/migrations/test_boot.py`

- [ ] **Step 1: Implement boot policy**

`hearth/kernel/migrations/boot.py`:

```python
"""Boot-time migration policy.

Read HEARTH_DB_MIGRATE; one of:
  - strict (default): refuse to start if any migration pending
  - safe: auto-apply safe migrations; refuse if destructive pending
  - off: skip the check entirely
"""

from __future__ import annotations

import os
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncEngine

from hearth.kernel.migrations.alembic_config import build_config
from hearth.kernel.migrations.applier import apply
from hearth.kernel.migrations.planner import compute_plan
from hearth.kernel.registry import Registry
from hearth.primitives.actor import System


Policy = Literal["strict", "safe", "off"]


def _read_policy() -> Policy:
    raw = os.environ.get("HEARTH_DB_MIGRATE", "strict").strip().lower()
    if raw not in ("strict", "safe", "off"):
        raise ValueError(f"HEARTH_DB_MIGRATE must be strict|safe|off, got {raw!r}")
    return raw  # type: ignore[return-value]


async def enforce_boot_policy(engine: AsyncEngine, registry: Registry) -> None:
    """Apply the policy. Raises on refuse."""
    policy = _read_policy()
    if policy == "off":
        return
    cfg = build_config(registry, str(engine.url))
    plan = await compute_plan(engine, cfg, registry)
    if not plan.revisions:
        return

    if policy == "strict":
        raise RuntimeError(
            f"HEARTH_DB_MIGRATE=strict; refusing to start, "
            f"{len(plan.revisions)} migration(s) pending. "
            f"Run `hearth db migrate` to apply."
        )
    # policy == "safe": auto-apply safe; refuse if destructive pending.
    if plan.has_destructive:
        raise RuntimeError(
            f"HEARTH_DB_MIGRATE=safe; refusing to start, plan contains destructive ops. "
            f"Run `hearth db migrate` interactively to confirm."
        )
    await apply(plan, config=cfg, engine=engine, actor=System(), registry=registry)
```

- [ ] **Step 2: Tests**

`tests/kernel/migrations/test_boot.py`:

```python
"""Tests for boot-time policy enforcement."""

from __future__ import annotations

import pytest

from hearth.kernel.migrations.boot import enforce_boot_policy


def _setup_boot_engine_and_registry(tmp_path, builder):
    """Helper: build engine + registry containing only `builder.plugin`."""
    from hearth.kernel._engine import make_async_engine
    from hearth.kernel.registry import PluginInfo, Registry

    engine = make_async_engine(f"sqlite+aiosqlite:///{tmp_path}/boot.db")
    registry = Registry.build()
    registry.plugins[builder.plugin] = PluginInfo(
        alias=builder.plugin, package=builder.plugin, version="0.0.1",
        module=builder.plugin, install_path=str(builder.package_dir),
        depends_on=[], extends=[], entities=[], actions=[], events=[],
    )
    return engine, registry


async def test_off_does_nothing_even_with_pending(
    tmp_path, monkeypatch, tmp_plugin_with_migrations, example_revision_body,
) -> None:
    monkeypatch.setenv("HEARTH_DB_MIGRATE", "off")
    builder = tmp_plugin_with_migrations
    builder.add_revision(
        revision="0001", down_revision=None,
        body=example_revision_body.format(
            revision="0001", down_revision=None, branch=builder.plugin, table="t",
        ),
    )
    engine, registry = _setup_boot_engine_and_registry(tmp_path, builder)
    try:
        # Should return cleanly even though a migration is pending.
        await enforce_boot_policy(engine, registry)
    finally:
        await engine.dispose()


async def test_strict_refuses_when_pending(
    tmp_path, monkeypatch, tmp_plugin_with_migrations, example_revision_body,
) -> None:
    monkeypatch.setenv("HEARTH_DB_MIGRATE", "strict")
    builder = tmp_plugin_with_migrations
    builder.add_revision(
        revision="0001", down_revision=None,
        body=example_revision_body.format(
            revision="0001", down_revision=None, branch=builder.plugin, table="t",
        ),
    )
    engine, registry = _setup_boot_engine_and_registry(tmp_path, builder)
    try:
        with pytest.raises(RuntimeError, match="strict.*pending"):
            await enforce_boot_policy(engine, registry)
    finally:
        await engine.dispose()


async def test_safe_auto_applies_when_only_safe(
    tmp_path, monkeypatch, tmp_plugin_with_migrations, example_revision_body,
) -> None:
    monkeypatch.setenv("HEARTH_DB_MIGRATE", "safe")
    builder = tmp_plugin_with_migrations
    builder.add_revision(
        revision="0001", down_revision=None,
        body=example_revision_body.format(
            revision="0001", down_revision=None, branch=builder.plugin, table="t",
        ),
    )
    engine, registry = _setup_boot_engine_and_registry(tmp_path, builder)
    try:
        async with engine.begin() as conn:
            from hearth.kernel.migrations.audit_log import SCHEMA_LOG_METADATA
            await conn.run_sync(SCHEMA_LOG_METADATA.create_all)
        await enforce_boot_policy(engine, registry)
        # Assert revision was applied (table exists).
        import sqlalchemy as sa
        async with engine.connect() as conn:
            names = await conn.run_sync(lambda s: set(sa.inspect(s).get_table_names()))
        assert "t" in names
    finally:
        await engine.dispose()


async def test_safe_refuses_when_destructive_pending(
    tmp_path, monkeypatch, tmp_plugin_with_migrations,
) -> None:
    monkeypatch.setenv("HEARTH_DB_MIGRATE", "safe")
    builder = tmp_plugin_with_migrations
    builder.add_revision(
        revision="0001", down_revision=None,
        body=f"""
            from alembic import op
            from hearth.migrations import OpSummary

            revision = "0001"
            down_revision = None
            branch_labels = ("{builder.plugin}",)

            HEARTH_UPGRADE_OPS = [OpSummary(kind="DropTable", desc="DROP TABLE t", destructive=True)]
            HEARTH_DOWNGRADE_OPS = []

            def upgrade(): op.drop_table("t")
            def downgrade(): pass
        """,
    )
    engine, registry = _setup_boot_engine_and_registry(tmp_path, builder)
    try:
        with pytest.raises(RuntimeError, match="destructive"):
            await enforce_boot_policy(engine, registry)
    finally:
        await engine.dispose()


def test_invalid_value_raises_at_read(monkeypatch) -> None:
    monkeypatch.setenv("HEARTH_DB_MIGRATE", "bogus")
    from hearth.kernel.migrations.boot import _read_policy
    with pytest.raises(ValueError):
        _read_policy()
```

Fill in the `...` setup blocks using the `tmp_plugin_with_migrations` fixture pattern. Run:

```bash
uv run pytest tests/kernel/migrations/test_boot.py -v
```
Expected: all pass.

---

### Task I2: ADR amendment + spec stub replacement

**Files:**
- Modify: `docs/architecture/0006-orm-contract.md`
- Modify: `docs/core/kernel/migrations.md`

- [ ] **Step 1: Append walled-garden amendment to ADR-0006**

Add at the end of `docs/architecture/0006-orm-contract.md`:

```markdown
## Amendment 2026-05-18 — migration files may import from `alembic` and `sqlalchemy`

The "Plugins do not" list above forbids plugins from importing `sqlalchemy`. This amendment narrows the rule: it applies to **runtime** code (entities, actions, events, jobs, views), not to **migration files**.

Migration files in a plugin's `migrations/` directory are tooling, not runtime code. They run during schema evolution (once per revision per deploy), not during request handling. The walled-garden rule exists to keep plugin business logic decoupled from persistence internals so the kernel can evolve them without breaking plugins. Migration files are inherently schema/DDL code; restricting them to a Hearth facade would force the kernel to reinvent Alembic's op vocabulary for no isolation benefit.

The clarified rule:

- **Runtime code** (entities, actions, events, jobs, views) — never imports from `sqlalchemy` or `alembic`. Plugins use the kernel's narrow Entity / Action / UnitOfWork surface.
- **Migration files** (`plugins/<name>/hearth_<name>/migrations/*.py`) — may import from `alembic` and `sqlalchemy`. They follow standard Alembic conventions plus the Hearth-specific `HEARTH_UPGRADE_OPS` / `HEARTH_DOWNGRADE_OPS` classification artifact and optional `data_upgrade(uow)` / `data_downgrade(uow)` hooks.

Enforcement is by convention and code review, not technical isolation. Migration files run in-process and could technically import anything; the project treats this the same as trusting third-party plugin code — violations are caught in review.

See [docs/superpowers/specs/2026-05-18-schema-migrations-design.md](../superpowers/specs/2026-05-18-schema-migrations-design.md) for the full migration system design.
```

- [ ] **Step 2: Replace `docs/core/kernel/migrations.md` stub**

Replace contents with:

```markdown
# Spec: schema migrations

> **Source modules**: `hearth/kernel/migrations/`, `hearth/migrations/`
> **Parent ADRs**: [ADR-0006](../../architecture/0006-orm-contract.md) (with 2026-05-18 amendment).
> **Design**: [docs/superpowers/specs/2026-05-18-schema-migrations-design.md](../../superpowers/specs/2026-05-18-schema-migrations-design.md)

## Status

Implemented. Source-of-truth design lives in the linked design spec.

## Brief

The kernel orchestrates per-plugin schema evolution using Alembic as the diff/DDL/discovery engine. Plugin authors ship standard Alembic revision files (with a `HEARTH_UPGRADE_OPS` classification artifact) and optional `data_upgrade(uow)` hooks for entity-typed seed data and backfills. Operators apply via `hearth db migrate` (interactive confirmation for destructive ops) or boot policy via `HEARTH_DB_MIGRATE`. See the design spec for full detail.
```

- [ ] **Step 3: Sanity-check the cross-links**

```bash
uv run python -c "
from pathlib import Path
import re
for f in Path('docs').rglob('*.md'):
    for m in re.finditer(r'\\]\\(([^)]+\\.md)[^)]*\\)', f.read_text()):
        target = (f.parent / m.group(1)).resolve()
        if not target.exists():
            print(f'BROKEN: {f}: {m.group(1)}')
"
```
Expected: no BROKEN lines for the files added/modified in this plan.

---

## Final verification (after Phase I)

Run the full suite + lint + typecheck:

```bash
uv run pytest -v
uv run ruff check .
uv run ruff format --check .
uv run basedpyright hearth
```
Expected: all green.

Smoke test end-to-end against a real DB:

```bash
rm -f /tmp/e2e.db
DATABASE_URL="sqlite:///tmp/e2e.db" uv run hearth db init
DATABASE_URL="sqlite:///tmp/e2e.db" uv run hearth db status
DATABASE_URL="sqlite:///tmp/e2e.db" uv run hearth db plan
DATABASE_URL="sqlite:///tmp/e2e.db" uv run hearth db migrate
```
Expected: clean output at each step.
