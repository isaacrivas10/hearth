# `Actor` Refactor + `hearth-auth` Plugin Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Do not commit anything.** This plan describes code, tests, and verification — commits are out of scope. The user authors commits separately, against the working tree, on their own cadence. Don't run `git add` or `git commit`.

**Goal:** Replace the half-built `Identity` primitive with a Protocol-based `Actor` model, ship the first real-world plugin (`hearth-auth`) with Users / Roles / Permissions / ApiKeys, and give plugins a clean way to extend the `hearth` CLI. Spec: `docs/superpowers/specs/2026-05-15-hearth-auth-and-actor-design.md`.

**Architecture:** Kernel `Actor` is a Protocol (`actor_kind: ClassVar[str]` + `async def has_permission`). Concrete actors are either Entities contributed by plugins (User, ApiKey) or dataless types shipped by the kernel (System, Anonymous, PluginActor). The `hearth-auth` plugin contributes 7 entities, 2 values, 15 actions + corresponding events, 2 enforcement helpers (`require_permission`, `@requires`), and 1 CLI command (`hearth auth bootstrap`). Plugins extend the `hearth` CLI via a new `hearth.cli` entry-point group.

**Tech Stack:** Python 3.13, Pydantic v2, SQLAlchemy 2.x async, Typer, `argon2-cffi` (new dep, auth-plugin-only), `importlib.metadata` for both `hearth.plugins` and `hearth.cli` discovery, pytest + pytest-asyncio, basedpyright strict, ruff.

---

## Pre-implementation reading

- `docs/superpowers/specs/2026-05-15-hearth-auth-and-actor-design.md` — full design.
- `CLAUDE.md` — architectural commitments + commit rules.
- `docs/architecture/0005-primitives.md` — the seven primitives ADR; this work amends it.
- `docs/architecture/0008-built-in-plugins.md` — kernel-vs-commons-vs-domain line.
- `hearth/primitives/identity.py` — the existing Identity primitive being renamed.
- `hearth/primitives/action.py` — `Action.handle(uow, identity)` signature to update.
- `hearth/kernel/transaction.py` — outbox actor serialization site.
- `hearth/kernel/persistence.py` — outbox table (the `actor` JSONB column).
- `hearth/testing/_base.py` — `_SqlAlchemyHarness.run(action, identity=...)` API to update.
- `hearth/cli/__init__.py`, `_db.py`, `_plugins.py` — CLI to extend.

---

## File structure

**New files (kernel):**
- `hearth/primitives/actor.py` — `Actor` Protocol + `System` + `Anonymous` + `PluginActor`.
- `tests/test_actor.py` — Actor Protocol + dataless actor tests.

**New files (auth plugin):**
- `plugins/auth/pyproject.toml`
- `plugins/auth/hearth_auth/__init__.py`
- `plugins/auth/hearth_auth/_base.py`
- `plugins/auth/hearth_auth/cli.py`
- `plugins/auth/hearth_auth/helpers.py`
- `plugins/auth/hearth_auth/values/__init__.py`
- `plugins/auth/hearth_auth/values/permission_name.py`
- `plugins/auth/hearth_auth/values/hashed_secret.py`
- `plugins/auth/hearth_auth/entities/__init__.py`
- `plugins/auth/hearth_auth/entities/user.py`
- `plugins/auth/hearth_auth/entities/role.py`
- `plugins/auth/hearth_auth/entities/permission.py`
- `plugins/auth/hearth_auth/entities/user_role.py`
- `plugins/auth/hearth_auth/entities/role_permission.py`
- `plugins/auth/hearth_auth/entities/api_key.py`
- `plugins/auth/hearth_auth/entities/api_key_permission.py`
- `plugins/auth/hearth_auth/actions/__init__.py`
- `plugins/auth/hearth_auth/actions/user.py`
- `plugins/auth/hearth_auth/actions/role.py`
- `plugins/auth/hearth_auth/actions/permission.py`
- `plugins/auth/hearth_auth/actions/api_key.py`
- `plugins/auth/hearth_auth/events/__init__.py`
- `plugins/auth/hearth_auth/events/user.py`
- `plugins/auth/hearth_auth/events/role.py`
- `plugins/auth/hearth_auth/events/permission.py`
- `plugins/auth/hearth_auth/events/api_key.py`
- `plugins/auth/hearth_auth/testing/__init__.py`
- `plugins/auth/tests/conftest.py`
- `plugins/auth/tests/test_user_actions.py`
- `plugins/auth/tests/test_role_actions.py`
- `plugins/auth/tests/test_permission_actions.py`
- `plugins/auth/tests/test_api_key_actions.py`
- `plugins/auth/tests/test_helpers.py`
- `plugins/auth/tests/test_bootstrap_cli.py`
- `plugins/auth/tests/test_cross_plugin_enforcement.py`

**New files (docs):**
- `docs/core/primitives/actor.md`
- `docs/core/plugins/auth.md`

**Deleted files:**
- `hearth/primitives/identity.py` (replaced by `actor.py`).
- `tests/test_identity.py` (replaced by `tests/test_actor.py`).
- `plugins/commerce/` (entire directory; placeholder slice).

**Modified files:**
- `hearth/__init__.py` — re-export rename.
- `hearth/primitives/__init__.py` — docstring.
- `hearth/primitives/action.py` — `Action.handle` signature.
- `hearth/kernel/transaction.py` — `UnitOfWork.__init__(actor=...)`, outbox actor JSON shape.
- `hearth/testing/_base.py` — harness `actor=` kwarg + System default.
- `hearth/cli/__init__.py` — `_register_plugin_cli_groups()`.
- `hearth/cli/_db.py` — `db init` calls `Registry.build()`.
- `tests/test_action.py` — switch from `identity` to `actor` kwarg and import path.
- `pyproject.toml` (root) — `auth` extra, workspace member, dev dep, `hearth-auth` source mapping.
- `docs/architecture/0005-primitives.md` — amendment section.

---

## Task 1: Drop placeholder commerce plugin

**Files:**
- Delete: `plugins/commerce/` (entire directory)
- Modify: `pyproject.toml` (root) — remove `commerce` from `[project.optional-dependencies]`, `[tool.uv.workspace]`, `[tool.uv.sources]`, and `[dependency-groups].dev`.

- [ ] **Step 1: Remove the commerce plugin directory**

```bash
rm -rf plugins/commerce
```

- [ ] **Step 2: Edit `pyproject.toml` (root)**

In `[project.optional-dependencies]` delete the `commerce = ["hearth-commerce"]` line and remove `"hearth-commerce"` from `all`:

```toml
[project.optional-dependencies]
commons = ["hearth-commons"]
all = ["hearth-commons"]
```

In `[tool.uv.workspace]` remove `"plugins/commerce"`:

```toml
[tool.uv.workspace]
members = ["plugins/commons"]
```

In `[tool.uv.sources]` remove the `hearth-commerce` line:

```toml
[tool.uv.sources]
hearth-commons = { workspace = true }
```

In `[dependency-groups]` `dev` list, remove `"hearth-commerce"`:

```toml
dev = [
    "hearth-commons",
    "pytest>=8.0",
    "pytest-asyncio>=0.23",
    "ruff>=0.6",
    "basedpyright>=1.18",
    "aiosqlite>=0.22.1",
]
```

In `[tool.pytest.ini_options]` `testpaths`, remove `"plugins/commerce/tests"`:

```toml
testpaths = ["tests", "plugins/commons/tests"]
```

- [ ] **Step 3: Sync workspace**

```bash
uv sync --dev
```

Expected: no errors. `hearth-commerce` package is uninstalled.

- [ ] **Step 4: Confirm no broken references**

```bash
grep -rn "hearth_commerce\|hearth-commerce" hearth/ tests/ docs/ --include="*.py" --include="*.toml" --include="*.md"
```

Expected: zero matches in source code; matches only in `docs/` historical notes (registry-cli spec, ADRs) are acceptable.

```bash
uv run pytest -q
```

Expected: full suite still green (the `plugins/commerce/tests` are gone with the directory).

---

## Task 2: `hearth db init` calls `Registry.build()` so plugin tables get created

**Files:**
- Modify: `hearth/cli/_db.py` — `_init_impl` function
- Modify: `tests/test_cli.py` — add a test that a plugin-shaped entity gets its table on `db init`

- [ ] **Step 1: Add the failing test**

Append to `tests/test_cli.py`:

```python
def test_db_init_creates_plugin_tables(tmp_path, monkeypatch) -> None:
    """`hearth db init` must call Registry.build() so plugin entities register
    their tables in METADATA, then `create_all` picks them up."""
    import sys
    import sqlite3
    import types

    import hearth.kernel.registry as reg_mod
    from hearth import bases_for

    mod = types.ModuleType("hearth_test_db_init_plugin")
    sys.modules["hearth_test_db_init_plugin"] = mod
    EBase, _, _ = bases_for("dbinitplugin")  # noqa: N806

    class Widget(EBase):
        name: str

    Widget.__module__ = "hearth_test_db_init_plugin"
    mod.Widget = Widget

    class FakeEP:
        name = "dbinitplugin"
        value = "hearth_test_db_init_plugin"
        dist = type("FakeDist", (), {
            "name": "pkg-dbinitplugin",
            "version": "0",
            "requires": (),
        })()

    monkeypatch.setattr(
        reg_mod.importlib.metadata,
        "entry_points",
        lambda **kw: [FakeEP()] if kw.get("group") == "hearth.plugins" else [],
    )

    db_path = tmp_path / "hearth.db"
    url = f"sqlite:///{db_path}"
    monkeypatch.setenv("DATABASE_URL", url)

    runner = CliRunner()
    result = runner.invoke(app, ["db", "init"])
    assert result.exit_code == 0

    con = sqlite3.connect(str(db_path))
    try:
        tables = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        con.close()

    assert "_hearth_outbox" in tables
    assert "dbinitplugin__widget" in tables, f"expected plugin table; got {tables}"
```

- [ ] **Step 2: Run the test — expect failure**

```bash
uv run pytest tests/test_cli.py::test_db_init_creates_plugin_tables -v
```

Expected: FAIL — `dbinitplugin__widget` missing from `tables` because `_init_impl` doesn't import the plugin module.

- [ ] **Step 3: Modify `hearth/cli/_db.py`**

Update the imports at the top of the file to include `Registry`:

```python
from hearth.cli import _wrap_command  # pyright: ignore[reportPrivateUsage]
from hearth.kernel._engine import make_async_engine  # pyright: ignore[reportPrivateUsage]
from hearth.kernel.persistence import METADATA
from hearth.kernel.registry import Registry, RegistryBuildError
```

Replace the `_init_impl` function body:

```python
async def _init_impl(url: str) -> None:
    typer.echo(f"Connecting to: {url}")
    # Build the registry first so plugin entity modules import and their
    # tables register with METADATA before create_all runs.
    try:
        registry = Registry.build()
    except RegistryBuildError as err:
        _print_registry_build_error(err)
        raise typer.Exit(1) from err
    engine = make_async_engine(url)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(METADATA.create_all)
        typer.echo("Created tables:")
        typer.echo("  [OK] _hearth_outbox")
        for alias in sorted(registry.plugins):
            info = registry.plugins[alias]
            for ent in info.entities:
                tablename = getattr(ent, "__tablename__", None)
                if tablename:
                    typer.echo(f"  [OK] {tablename}")
        typer.echo("Done.")
    finally:
        await engine.dispose()
```

Add a small helper near the top of the file (before the `db_app` declaration):

```python
def _print_registry_build_error(err: RegistryBuildError) -> None:
    typer.echo("ERROR: cannot build plugin registry.\n", err=True)
    for issue in err.issues:
        typer.echo(f"  - {issue}", err=True)
    typer.echo("\nFix the above and rerun.", err=True)
```

- [ ] **Step 4: Run the test — expect pass**

```bash
uv run pytest tests/test_cli.py::test_db_init_creates_plugin_tables -v
```

Expected: PASS. Also run the full test_cli.py file to ensure no regressions:

```bash
uv run pytest tests/test_cli.py -v
```

Expected: all 17 tests pass.

---

## Task 3: `Actor` Protocol + dataless kernel actors

**Files:**
- Create: `hearth/primitives/actor.py`
- Create: `tests/test_actor.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_actor.py`:

```python
"""Tests for the Actor primitive — see docs/core/primitives/actor.md."""

from __future__ import annotations

from typing import ClassVar

import pytest

from hearth import Actor, Anonymous, PluginActor, System
from hearth.testing import Harness


class _StubEntity:
    """A stand-in actor that satisfies the Protocol without being an Entity.
    Used to confirm the Protocol is structural, not nominal."""

    actor_kind: ClassVar[str] = "stub"

    async def has_permission(self, uow, permission):  # type: ignore[no-untyped-def]
        return permission == "stub:allowed"


def test_system_actor_kind() -> None:
    assert System().actor_kind == "system"


def test_anonymous_actor_kind() -> None:
    assert Anonymous().actor_kind == "anonymous"


def test_plugin_actor_carries_alias() -> None:
    pa = PluginActor(alias="commerce")
    assert pa.actor_kind == "plugin"
    assert pa.alias == "commerce"


async def test_system_has_all_permissions() -> None:
    harness = Harness()
    await harness.setup(entities=[])
    try:
        await harness.reset()
        async with harness.transaction() as uow:
            assert await System().has_permission(uow, "anything:goes") is True
    finally:
        await harness.teardown()


async def test_anonymous_has_no_permissions() -> None:
    harness = Harness()
    await harness.setup(entities=[])
    try:
        await harness.reset()
        async with harness.transaction() as uow:
            assert await Anonymous().has_permission(uow, "anything") is False
    finally:
        await harness.teardown()


async def test_plugin_actor_has_all_permissions() -> None:
    harness = Harness()
    await harness.setup(entities=[])
    try:
        await harness.reset()
        async with harness.transaction() as uow:
            assert await PluginActor(alias="commerce").has_permission(uow, "anything") is True
    finally:
        await harness.teardown()


def test_protocol_is_structural() -> None:
    """An object that has actor_kind + async has_permission satisfies Actor
    without inheriting from it — Actor is a Protocol."""
    stub = _StubEntity()
    # No isinstance check — Python Protocols don't require nominal inheritance.
    # The test is: stub.actor_kind exists and stub.has_permission is awaitable.
    assert stub.actor_kind == "stub"
```

- [ ] **Step 2: Run the test — expect failure**

```bash
uv run pytest tests/test_actor.py -v
```

Expected: FAIL with `ImportError: cannot import name 'Actor' from 'hearth'`.

- [ ] **Step 3: Create `hearth/primitives/actor.py`**

```python
"""Actor primitive — see docs/core/primitives/actor.md.

An Actor is the 'who' of an Action. Concrete actors are either Entities
contributed by plugins (e.g., hearth-auth's User and ApiKey) or
dataless types shipped by the kernel (System, Anonymous, PluginActor).
Both satisfy the structural Protocol below.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar, Protocol, runtime_checkable

if TYPE_CHECKING:
    from hearth.unit_of_work import UnitOfWork


@runtime_checkable
class Actor(Protocol):
    """The 'who' of an Action. Threads through actions for audit + authz.

    actor_kind convention: snake_case, lowercase. Single words for the kernel
    types (`"system"`, `"anonymous"`, `"plugin"`); multi-word actor types
    use underscore (`"api_key"`). The string is the wire-level discriminator
    for outbox audit JSON; choose carefully — once shipped it shouldn't be
    renamed.
    """

    actor_kind: ClassVar[str]

    async def has_permission(self, uow: "UnitOfWork", permission: str) -> bool:
        """Return True if this actor holds the given permission string.

        Permission strings follow the `<resource>:<action>` convention.
        Resolution semantics are the actor's responsibility — the kernel
        knows nothing about how permissions are stored or granted.
        """
        ...


@dataclass(frozen=True)
class System:
    """The kernel itself — outbox dispatch, bootstrap, schema operations."""

    actor_kind: ClassVar[str] = "system"

    async def has_permission(self, uow: "UnitOfWork", permission: str) -> bool:
        return True  # kernel-internal callers are trusted


@dataclass(frozen=True)
class Anonymous:
    """Unauthenticated public access. Used by transport layers for endpoints
    that explicitly allow no auth."""

    actor_kind: ClassVar[str] = "anonymous"

    async def has_permission(self, uow: "UnitOfWork", permission: str) -> bool:
        return False


@dataclass(frozen=True)
class PluginActor:
    """A plugin acting on its own behalf — e.g., a scheduled cron from the
    commerce plugin running nightly invoice generation."""

    actor_kind: ClassVar[str] = "plugin"
    alias: str

    async def has_permission(self, uow: "UnitOfWork", permission: str) -> bool:
        return True  # plugin code is trusted; permission checks gate external callers
```

- [ ] **Step 4: Update `hearth/__init__.py`**

Replace the line:

```python
from hearth.primitives.identity import Identity
```

with:

```python
from hearth.primitives.actor import Actor, Anonymous, PluginActor, System
```

In `__all__`, replace `"Identity"` with `"Actor"`, `"Anonymous"`, `"PluginActor"`, `"System"`. The list (alphabetical):

```python
__all__ = [
    "Action",
    "Actor",
    "Anonymous",
    "Entity",
    "EntityId",
    "EntityNotFoundError",
    "Event",
    "Field",
    "ForeignKey",
    "HearthError",
    "PluginActor",
    "Query",
    "References",
    "System",
    "UnitOfWork",
    "Value",
    "agg",
    "bases_for",
    "computed_field",
    "field_validator",
    "model_validator",
]
```

- [ ] **Step 5: Update `hearth/primitives/__init__.py` docstring**

Replace the file content with:

```python
"""The seven kernel primitives: Entity, Event, Value, Action, Job, Actor, View."""
```

- [ ] **Step 6: Run actor tests — expect pass**

```bash
uv run pytest tests/test_actor.py -v
```

Expected: 7 PASS.

---

## Task 4: Migrate kernel from `Identity` to `Actor` (signature change + transaction + harness + delete identity.py)

**Files:**
- Modify: `hearth/primitives/action.py`
- Modify: `hearth/kernel/transaction.py`
- Modify: `hearth/testing/_base.py`
- Modify: `tests/test_action.py` — switch identity→actor in kwargs and assertions
- Delete: `hearth/primitives/identity.py`
- Delete: `tests/test_identity.py`

- [ ] **Step 1: Modify `hearth/primitives/action.py`**

Replace the file content:

```python
"""Action primitive — see docs/core/primitives/action.md."""

from __future__ import annotations

from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict

from hearth.primitives.actor import Actor
from hearth.unit_of_work import UnitOfWork


class Action(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    _hearth_plugin: ClassVar[str | None] = None

    def __init_subclass__(cls, *, plugin: str | None = None, **kwargs: Any) -> None:
        if plugin is not None:
            cls._hearth_plugin = plugin
        super().__init_subclass__(**kwargs)

    async def handle(self, uow: UnitOfWork, actor: Actor) -> Any:
        raise NotImplementedError(f"{type(self).__name__} must implement handle()")
```

- [ ] **Step 2: Modify `hearth/kernel/transaction.py`**

Important: the class in this file is `_UnitOfWork` (private, underscore-prefixed). The public `UnitOfWork` is a **Protocol** in `hearth/unit_of_work.py` that `_UnitOfWork` satisfies structurally. Do NOT rename `_UnitOfWork` — it stays private.

Find the imports at the top of the file. Replace:

```python
from hearth.primitives.identity import Identity
```

with:

```python
from hearth.primitives.actor import Actor
```

Find the `_UnitOfWork` constructor (around line 32) and rename the parameter:

```python
class _UnitOfWork:  # pyright: ignore[reportUnusedClass]
    def __init__(self, session: AsyncSession, actor: Actor) -> None:
        self._session = session
        self._actor = actor
        self._event_buffer: list[Event] = []
```

Find the outbox-write block (around line 70-85) and replace the actor serialization. Old:

```python
"actor": self._identity.model_dump(mode="json"),
```

New (per spec Section 11 / Sequencing Step 3):

```python
"actor": self._serialize_actor(),
```

Add the helper method to `_UnitOfWork`:

```python
    def _serialize_actor(self) -> dict[str, Any]:
        actor = self._actor
        from hearth.primitives.actor import PluginActor
        meta: dict[str, Any] = {}
        if isinstance(actor, PluginActor):
            meta["alias"] = actor.alias
        # Entity actors expose an `.id`; dataless actors do not. EntityId is
        # already JSON-friendly (str under the hood).
        actor_id = getattr(actor, "id", None)
        return {
            "kind": actor.actor_kind,
            "id": str(actor_id) if actor_id is not None else None,
            "meta": meta,
        }
```

Also rename any other reference to `self._identity` inside the file to `self._actor`. The harness reads `_actor` to pass it to actions; tests access `_actor` if they introspect a UoW.

- [ ] **Step 3: Modify `hearth/testing/_base.py`**

Find the imports (around line 22). Replace:

```python
from hearth.primitives.identity import Identity, System
```

with:

```python
from hearth.primitives.actor import Actor, System
```

Find the `transaction` context manager (around lines 62-71). Replace it (keep `_sessionmaker`, keep `session.begin()`, keep `_UnitOfWork`):

```python
    @asynccontextmanager
    async def transaction(
        self,
        actor: Actor | None = None,
    ) -> AsyncGenerator[_UnitOfWork]:
        active_actor = actor if actor is not None else System()
        async with self._sessionmaker() as session, session.begin():
            uow = _UnitOfWork(session, active_actor)
            yield uow
            await uow._flush_events()  # pyright: ignore[reportPrivateUsage]
```

Find the `run` method (around line 73). Replace it:

```python
    async def run(self, action: Action, actor: Actor | None = None) -> Any:
        active_actor = actor if actor is not None else System()
        async with self.transaction(actor=active_actor) as uow:
            return await action.handle(uow, active_actor)
```

`session.begin()` as a context manager handles commit-on-exit and rollback-on-exception automatically — no explicit try/except needed. The variable name change `identity` → `actor` is internal; the external API exposes the `actor=` kwarg.

- [ ] **Step 4: Update `tests/test_action.py`**

Replace the import line:

```python
from hearth.primitives.identity import System, User
```

with:

```python
from hearth.primitives.actor import System
```

Find every `harness.run(<action>, identity=<x>)` call and replace `identity=` with `actor=`. The `_CheckIdentity` test class param name `identity` stays as a parameter name inside `handle` for now — but rename it to `actor` for consistency. Specifically, replace this block (around lines 92-111):

```python
async def test_action_receives_default_identity(harness: Harness) -> None:
    received: list = []

    class _CheckIdentity(Action):
        async def handle(self, uow, identity):  # type: ignore[no-untyped-def]
            received.append(identity)

    await harness.run(_CheckIdentity())
    assert isinstance(received[0], System)


async def test_action_receives_explicit_identity(harness: Harness) -> None:
    received: list = []
    user = User(user_id="alice")

    class _CheckIdentity(Action):
        async def handle(self, uow, identity):  # type: ignore[no-untyped-def]
            received.append(identity)

    await harness.run(_CheckIdentity(), identity=user)
    assert received[0] is user
```

with:

```python
async def test_action_receives_default_actor(harness: Harness) -> None:
    received: list = []

    class _CheckActor(Action):
        async def handle(self, uow, actor):  # type: ignore[no-untyped-def]
            received.append(actor)

    await harness.run(_CheckActor())
    assert isinstance(received[0], System)


async def test_action_receives_explicit_actor(harness: Harness) -> None:
    from hearth.primitives.actor import PluginActor

    received: list = []
    actor = PluginActor(alias="other")

    class _CheckActor(Action):
        async def handle(self, uow, actor_arg):  # type: ignore[no-untyped-def]
            received.append(actor_arg)

    await harness.run(_CheckActor(), actor=actor)
    assert received[0] is actor
```

In every other test in this file, replace `def handle(self, uow, identity):` with `def handle(self, uow, actor):` (parameter rename) and any `identity=` kwarg in `harness.run(...)` with `actor=`. After the changes, no occurrence of `identity` should remain in `tests/test_action.py`.

- [ ] **Step 5: Delete `hearth/primitives/identity.py` and `tests/test_identity.py`**

```bash
rm hearth/primitives/identity.py tests/test_identity.py
```

- [ ] **Step 6: Run the action + actor tests**

```bash
uv run pytest tests/test_action.py tests/test_actor.py -v
```

Expected: all pass.

---

## Task 5: Outbox serializes actor as `{"kind", "id", "meta"}` JSON

**Files:**
- Modify: `tests/test_inmemory_uow.py` or `tests/test_action.py` — add a test for the outbox actor shape

The implementation of `_serialize_actor` already landed in Task 4 Step 2. This task adds explicit verification.

- [ ] **Step 1: Add the outbox-shape test**

Append to `tests/test_action.py`:

```python
async def test_outbox_actor_serialization_for_system(harness: Harness) -> None:
    """Outbox `actor` column stores {kind, id, meta} JSON dict for System."""
    from sqlalchemy import select, text
    from hearth.kernel.persistence import OUTBOX_TABLE

    class _Emit(Action):
        async def handle(self, uow, actor):  # type: ignore[no-untyped-def]
            uow.emit(_TinyEvent(note="hello"))

    class _TinyEvent(Event):
        note: str

    await harness.run(_Emit())

    async with harness.transaction() as uow:
        result = await uow._session.execute(select(OUTBOX_TABLE.c.actor))  # pyright: ignore[reportPrivateUsage]
        actor_json = result.scalar_one()

    assert actor_json == {"kind": "system", "id": None, "meta": {}}


async def test_outbox_actor_serialization_for_plugin_actor(harness: Harness) -> None:
    """PluginActor serializes with the alias under meta."""
    from sqlalchemy import select
    from hearth.kernel.persistence import OUTBOX_TABLE
    from hearth.primitives.actor import PluginActor

    class _Emit(Action):
        async def handle(self, uow, actor):  # type: ignore[no-untyped-def]
            uow.emit(_AnotherEvent(note="hi"))

    class _AnotherEvent(Event):
        note: str

    await harness.run(_Emit(), actor=PluginActor(alias="commerce"))

    async with harness.transaction() as uow:
        result = await uow._session.execute(select(OUTBOX_TABLE.c.actor))  # pyright: ignore[reportPrivateUsage]
        actor_json = result.scalar_one()

    assert actor_json == {"kind": "plugin", "id": None, "meta": {"alias": "commerce"}}
```

Note: the import `from hearth import Event` should already be at the top of test_action.py. Confirm before running.

- [ ] **Step 2: Run the tests**

```bash
uv run pytest tests/test_action.py -v
```

Expected: all pass, including the two new outbox-shape tests.

---

## Task 6: Plugin CLI extension mechanism (`hearth.cli` entry-point group)

**Files:**
- Modify: `hearth/cli/__init__.py` — add `_register_plugin_cli_groups()`
- Modify: `tests/test_cli.py` — add tests for discovery, reserved names, failure tolerance

- [ ] **Step 1: Add the failing tests**

Append to `tests/test_cli.py`:

```python
def test_plugin_cli_group_is_registered(monkeypatch) -> None:
    """A plugin contributing a Typer app via `hearth.cli` entry-point becomes
    available as `hearth <alias> ...`."""
    import importlib
    import sys
    import types

    fake_mod = types.ModuleType("hearth_test_cliext_demo")
    sub_app = typer.Typer(help="A test plugin CLI.")

    @sub_app.callback()
    def _root() -> None:
        """Forces subcommand mode."""

    @sub_app.command("hello")
    def _hello() -> None:
        typer.echo("hi from the plugin")

    fake_mod.sub_app = sub_app
    sys.modules["hearth_test_cliext_demo"] = fake_mod

    class FakeEP:
        name = "demoplugin"
        value = "hearth_test_cliext_demo:sub_app"
        def load(self):
            return sub_app

    monkeypatch.setattr(
        "hearth.cli.importlib.metadata.entry_points",
        lambda **kw: [FakeEP()] if kw.get("group") == "hearth.cli" else [],
    )

    # Re-import the CLI module to re-run plugin discovery against the patched
    # entry_points. Module-level discovery runs at import time.
    import hearth.cli
    importlib.reload(hearth.cli)
    from hearth.cli import app as reloaded_app

    runner = CliRunner()
    result = runner.invoke(reloaded_app, ["demoplugin", "hello"])
    assert result.exit_code == 0
    assert "hi from the plugin" in result.stdout


def test_plugin_cli_reserved_name_is_skipped(monkeypatch) -> None:
    """A plugin trying to register under a kernel-reserved CLI name (`plugins`
    or `db`) is skipped with a warning."""
    import importlib
    import sys
    import types

    fake_mod = types.ModuleType("hearth_test_cliext_clash")
    sub_app = typer.Typer()

    @sub_app.callback()
    def _root() -> None:
        """Forces subcommand mode."""

    @sub_app.command("naughty")
    def _naughty() -> None:
        typer.echo("should not see this")

    fake_mod.sub_app = sub_app
    sys.modules["hearth_test_cliext_clash"] = fake_mod

    class FakeEP:
        name = "plugins"  # reserved name
        value = "hearth_test_cliext_clash:sub_app"
        def load(self):
            return sub_app

    monkeypatch.setattr(
        "hearth.cli.importlib.metadata.entry_points",
        lambda **kw: [FakeEP()] if kw.get("group") == "hearth.cli" else [],
    )

    import hearth.cli
    importlib.reload(hearth.cli)
    from hearth.cli import app as reloaded_app

    runner = CliRunner()
    result = runner.invoke(reloaded_app, ["plugins", "naughty"])
    # `plugins` still exists as the kernel-owned group; "naughty" is not its command.
    assert result.exit_code != 0
    combined = result.stdout + (result.stderr or "")
    assert "naughty" not in combined or "No such command" in combined


def test_plugin_cli_load_failure_does_not_crash(monkeypatch) -> None:
    """If a plugin's entry-point `ep.load()` raises, the CLI logs a warning
    but continues registering other plugins and remains usable."""
    import importlib

    class BrokenEP:
        name = "broken"
        value = "no.such.module:no_such_app"
        def load(self):
            raise ModuleNotFoundError("no.such.module")

    monkeypatch.setattr(
        "hearth.cli.importlib.metadata.entry_points",
        lambda **kw: [BrokenEP()] if kw.get("group") == "hearth.cli" else [],
    )

    import hearth.cli
    importlib.reload(hearth.cli)
    from hearth.cli import app as reloaded_app

    runner = CliRunner()
    # The bare CLI still works — broken plugin doesn't crash.
    result = runner.invoke(reloaded_app, ["--version"])
    assert result.exit_code == 0
```

- [ ] **Step 2: Run the tests — expect failure**

```bash
uv run pytest tests/test_cli.py::test_plugin_cli_group_is_registered tests/test_cli.py::test_plugin_cli_reserved_name_is_skipped tests/test_cli.py::test_plugin_cli_load_failure_does_not_crash -v
```

Expected: FAIL — the plugin-cli discovery doesn't exist yet.

- [ ] **Step 3: Add the discovery function to `hearth/cli/__init__.py`**

Append to the end of `hearth/cli/__init__.py` (after the existing `app.add_typer(plugins_app, ...)` and `app.add_typer(db_app, ...)` lines):

```python
_RESERVED_CLI_NAMES = frozenset({"plugins", "db"})


def _register_plugin_cli_groups() -> None:
    """Discover Typer subcommand groups contributed by plugins via the
    `[project.entry-points."hearth.cli"]` group.

    Independent of `Registry.build()`: plugin CLI loads via its own entry-point
    group so CLI diagnostics remain available even when the registry has
    problems. Failures are logged as warnings, not raised."""
    import importlib.metadata  # local import keeps module top tidy

    for ep in importlib.metadata.entry_points(group="hearth.cli"):
        if ep.name in _RESERVED_CLI_NAMES:
            typer.echo(
                f"WARNING: plugin CLI name {ep.name!r} is reserved; skipping",
                err=True,
            )
            continue
        try:
            plugin_app = ep.load()
        except Exception as exc:  # noqa: BLE001 — broad catch is intentional
            typer.echo(
                f"WARNING: failed to load CLI from plugin {ep.name!r}: "
                f"{type(exc).__name__}: {exc}",
                err=True,
            )
            continue
        app.add_typer(plugin_app, name=ep.name)


_register_plugin_cli_groups()
```

Ensure `importlib.metadata` is imported at the top of the file (`import importlib.metadata`). If only the local import inside the function is present, that's fine — but tests need to monkeypatch `hearth.cli.importlib.metadata.entry_points`, which requires a module-level reference. Add this at the top of the file with the existing imports:

```python
import importlib.metadata
```

And drop the local-scoped import inside `_register_plugin_cli_groups()` (the module-level import is in scope already).

- [ ] **Step 4: Run the tests — expect pass**

```bash
uv run pytest tests/test_cli.py -v
```

Expected: all CLI tests pass (17 existing + 3 new = 20).

---

## Task 7: `hearth-auth` plugin scaffold (pyproject + workspace + `_base`)

**Files:**
- Create: `plugins/auth/pyproject.toml`
- Create: `plugins/auth/hearth_auth/__init__.py`
- Create: `plugins/auth/hearth_auth/_base.py`
- Modify: `pyproject.toml` (root) — add the auth extra, workspace member, dev dep, and source mapping.

- [ ] **Step 1: Create `plugins/auth/pyproject.toml`**

```toml
[project]
name = "hearth-auth"
version = "0.0.1"
description = "Hearth plugin: Users, Roles, Permissions, ApiKeys, and the actions that operate on them."
requires-python = ">=3.13"
dependencies = [
    "hearth",
    "hearth-commons",
    "argon2-cffi>=23.1",
]

[project.entry-points."hearth.plugins"]
auth = "hearth_auth"

[project.entry-points."hearth.cli"]
auth = "hearth_auth.cli:auth_app"

[tool.uv.sources]
hearth = { workspace = true }
hearth-commons = { workspace = true }

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["hearth_auth"]
```

- [ ] **Step 2: Create `plugins/auth/hearth_auth/_base.py`**

```python
"""Auth plugin's local Entity / Action / Event bases. The plugin alias
'auth' is declared exactly once, here."""

from hearth import bases_for

Entity, Action, Event = bases_for("auth")
```

- [ ] **Step 3: Create `plugins/auth/hearth_auth/__init__.py`** (initial stub)

```python
"""hearth_auth: users, roles, permissions, and api keys for Hearth."""

# Re-exports are added incrementally as later tasks introduce each module.

__all__: list[str] = []
```

- [ ] **Step 4: Modify root `pyproject.toml`** to register the auth plugin.

In `[project.optional-dependencies]`:

```toml
[project.optional-dependencies]
commons = ["hearth-commons"]
auth = ["hearth-auth"]
all = ["hearth-commons", "hearth-auth"]
```

In `[tool.uv.workspace]`:

```toml
[tool.uv.workspace]
members = ["plugins/commons", "plugins/auth"]
```

In `[tool.uv.sources]`:

```toml
[tool.uv.sources]
hearth-commons = { workspace = true }
hearth-auth = { workspace = true }
```

In `[dependency-groups].dev`:

```toml
dev = [
    "hearth-commons",
    "hearth-auth",
    "pytest>=8.0",
    "pytest-asyncio>=0.23",
    "ruff>=0.6",
    "basedpyright>=1.18",
    "aiosqlite>=0.22.1",
]
```

In `[tool.pytest.ini_options].testpaths`:

```toml
testpaths = ["tests", "plugins/commons/tests", "plugins/auth/tests"]
```

In `[tool.basedpyright].include`:

```toml
include = [
    "hearth",
    "plugins/commons/hearth_commons",
    "plugins/auth/hearth_auth",
    "scripts",
]
```

- [ ] **Step 5: Sync workspace**

```bash
uv sync --dev
```

Expected: `hearth-auth` installed, `argon2-cffi` installed.

```bash
uv run python -c "import hearth_auth; print(hearth_auth.__name__)"
uv run hearth plugins list
```

Expected: `hearth_auth` printed. `hearth plugins list` shows `auth 0.0.1 hearth_auth deps: (none) extends: (none)`.

---

## Task 8: Values — `PermissionName` and `HashedSecret`

**Files:**
- Create: `plugins/auth/hearth_auth/values/__init__.py`
- Create: `plugins/auth/hearth_auth/values/permission_name.py`
- Create: `plugins/auth/hearth_auth/values/hashed_secret.py`
- Create: `plugins/auth/tests/__init__.py` (empty)
- Create: `plugins/auth/tests/test_values.py`

- [ ] **Step 1: Write the failing test**

Create `plugins/auth/tests/test_values.py`:

```python
"""Tests for the auth plugin's Value types."""

from __future__ import annotations

import pytest
from pydantic import SecretStr

from hearth_auth.values import HashedSecret, PermissionName


def test_permission_name_round_trip() -> None:
    pn = PermissionName(resource="orders", action="read")
    assert pn.resource == "orders"
    assert pn.action == "read"


def test_permission_name_equality() -> None:
    a = PermissionName(resource="orders", action="read")
    b = PermissionName(resource="orders", action="read")
    c = PermissionName(resource="orders", action="write")
    assert a == b
    assert a != c
    assert hash(a) == hash(b)


def test_hashed_secret_from_and_verify_str() -> None:
    h = HashedSecret.from_plaintext("hunter2")
    assert h.hash.startswith("$argon2id$")
    assert h.verify("hunter2") is True
    assert h.verify("wrong") is False


def test_hashed_secret_accepts_secret_str() -> None:
    h = HashedSecret.from_plaintext(SecretStr("hunter2"))
    assert h.verify(SecretStr("hunter2")) is True
    assert h.verify("hunter2") is True


def test_hashed_secret_two_hashes_differ_for_same_plaintext() -> None:
    """argon2id includes a random salt; two hashes of the same plaintext differ."""
    h1 = HashedSecret.from_plaintext("same-input")
    h2 = HashedSecret.from_plaintext("same-input")
    assert h1.hash != h2.hash
    # But both verify
    assert h1.verify("same-input") is True
    assert h2.verify("same-input") is True
```

- [ ] **Step 2: Run the test — expect failure**

```bash
uv run pytest plugins/auth/tests/test_values.py -v
```

Expected: FAIL with `ImportError`.

- [ ] **Step 3: Create `plugins/auth/hearth_auth/values/permission_name.py`**

```python
"""PermissionName Value — the (resource, action) discriminator.

When persisted on a Permission entity, the metaclass expands this Value
into two composite columns: `name__resource` and `name__action`.
"""

from __future__ import annotations

from hearth import Value


class PermissionName(Value):
    """The (resource, action) pair identifying a permission."""

    resource: str
    action: str
```

- [ ] **Step 4: Create `plugins/auth/hearth_auth/values/hashed_secret.py`**

```python
"""HashedSecret Value — argon2id hash for User passwords and ApiKey secrets.

Used as a one-column composite on entities. Hashing and verification are
CPU-bound; callers MUST wrap them in `asyncio.to_thread(...)` to avoid
blocking the event loop.
"""

from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from pydantic import SecretStr

from hearth import Value

_hasher = PasswordHasher()


def _to_str(plaintext: str | SecretStr) -> str:
    return plaintext.get_secret_value() if isinstance(plaintext, SecretStr) else plaintext


class HashedSecret(Value):
    """An argon2id hash. Used by User.password and ApiKey.key_hash.

    Both `from_plaintext` and `verify` are CPU-bound (~50–100ms by design).
    Plugin code MUST invoke them via `asyncio.to_thread(...)` to avoid
    blocking the event loop.
    """

    hash: str

    @classmethod
    def from_plaintext(cls, plaintext: str | SecretStr) -> "HashedSecret":
        return cls(hash=_hasher.hash(_to_str(plaintext)))

    def verify(self, plaintext: str | SecretStr) -> bool:
        try:
            return _hasher.verify(self.hash, _to_str(plaintext))
        except VerifyMismatchError:
            return False
```

- [ ] **Step 5: Create `plugins/auth/hearth_auth/values/__init__.py`**

```python
"""Auth plugin Value types."""

from hearth_auth.values.hashed_secret import HashedSecret
from hearth_auth.values.permission_name import PermissionName

__all__ = ["HashedSecret", "PermissionName"]
```

- [ ] **Step 6: Run the test — expect pass**

```bash
uv run pytest plugins/auth/tests/test_values.py -v
```

Expected: 5 PASS.

---

## Task 9: Entities — `User`, `Role`, `Permission`

**Files:**
- Create: `plugins/auth/hearth_auth/entities/__init__.py`
- Create: `plugins/auth/hearth_auth/entities/user.py`
- Create: `plugins/auth/hearth_auth/entities/role.py`
- Create: `plugins/auth/hearth_auth/entities/permission.py`
- Create: `plugins/auth/tests/test_entities_basic.py`

- [ ] **Step 1: Write the failing test**

Create `plugins/auth/tests/test_entities_basic.py`:

```python
"""Tests for User / Role / Permission entities (basic shape only).
`has_permission` is tested in test_entities_resolution.py once joins exist."""

from __future__ import annotations

import pytest

from hearth_auth.entities import Permission, Role, User
from hearth_auth.values import HashedSecret, PermissionName
from hearth_commons import EmailAddress


def test_user_basic_construction() -> None:
    u = User(
        email=EmailAddress(raw="alice@example.com"),
        password=HashedSecret.from_plaintext("hunter2"),
        display_name="Alice",
    )
    assert u.email.raw == "alice@example.com"
    assert u.display_name == "Alice"
    assert u.is_active is True
    assert u.last_login_at is None
    assert u.actor_kind == "user"


def test_role_basic_construction() -> None:
    r = Role(name="admin", description="The admin role.")
    assert r.name == "admin"
    assert r.description == "The admin role."


def test_permission_basic_construction() -> None:
    p = Permission(
        name=PermissionName(resource="orders", action="read"),
        description="Read orders.",
    )
    assert p.name.resource == "orders"
    assert p.name.action == "read"


def test_user_actor_kind_is_class_var() -> None:
    """Plugin authors should be able to read actor_kind off the class
    without constructing an instance."""
    assert User.actor_kind == "user"
```

- [ ] **Step 2: Run the test — expect failure**

```bash
uv run pytest plugins/auth/tests/test_entities_basic.py -v
```

Expected: FAIL with ImportError.

- [ ] **Step 3: Create `plugins/auth/hearth_auth/entities/user.py`**

```python
"""User entity — a human account with login credentials. Actor-capable."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any, ClassVar

from hearth_auth._base import Entity
from hearth_auth.values import HashedSecret
from hearth_commons import EmailAddress

if TYPE_CHECKING:
    from hearth.unit_of_work import UnitOfWork


class User(Entity):
    actor_kind: ClassVar[str] = "user"

    email: EmailAddress
    password: HashedSecret
    display_name: str | None = None
    is_active: bool = True
    last_login_at: datetime | None = None

    async def has_permission(self, uow: "UnitOfWork", permission: str) -> bool:
        """Resolve permissions via UserRole → RolePermission → Permission.
        Implemented in Task 11 once the join entities exist."""
        # Placeholder for type-checker compatibility. The real implementation
        # arrives in Task 11 — this file's import shouldn't fail in the
        # intermediate state.
        raise NotImplementedError("User.has_permission lands in Task 11")

    def __hash__(self) -> int:
        # Entities use ULID identity; hash by id so they work in sets and
        # as dict keys when relevant (the existing kernel pattern).
        return hash(self.id) if self.id is not None else id(self)
```

- [ ] **Step 4: Create `plugins/auth/hearth_auth/entities/role.py`**

```python
"""Role entity — a named bundle of permissions, assignable to Users."""

from __future__ import annotations

from hearth_auth._base import Entity


class Role(Entity):
    name: str
    description: str | None = None
```

- [ ] **Step 5: Create `plugins/auth/hearth_auth/entities/permission.py`**

```python
"""Permission entity — one row per (resource, action) tuple. Bound to Roles
via RolePermission, to ApiKeys via ApiKeyPermission."""

from __future__ import annotations

from hearth_auth._base import Entity
from hearth_auth.values import PermissionName


class Permission(Entity):
    name: PermissionName
    description: str | None = None
```

- [ ] **Step 6: Create `plugins/auth/hearth_auth/entities/__init__.py`**

```python
"""Auth plugin entity types."""

from hearth_auth.entities.permission import Permission
from hearth_auth.entities.role import Role
from hearth_auth.entities.user import User

__all__ = ["Permission", "Role", "User"]
```

- [ ] **Step 7: Run the test — expect pass**

```bash
uv run pytest plugins/auth/tests/test_entities_basic.py -v
```

Expected: 4 PASS.

---

## Task 10: Join entities — `UserRole`, `RolePermission`, `ApiKey`, `ApiKeyPermission`

**Files:**
- Create: `plugins/auth/hearth_auth/entities/user_role.py`
- Create: `plugins/auth/hearth_auth/entities/role_permission.py`
- Create: `plugins/auth/hearth_auth/entities/api_key.py`
- Create: `plugins/auth/hearth_auth/entities/api_key_permission.py`
- Modify: `plugins/auth/hearth_auth/entities/__init__.py`
- Create: `plugins/auth/tests/test_entities_joins.py`

- [ ] **Step 1: Write the failing test**

Create `plugins/auth/tests/test_entities_joins.py`:

```python
"""Tests for join entities and ApiKey."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from collections.abc import AsyncIterator, Callable

import pytest

from hearth import EntityId
from hearth.testing._base import _SqlAlchemyHarness
from hearth_auth.entities import (
    ApiKey,
    ApiKeyPermission,
    Permission,
    Role,
    RolePermission,
    User,
    UserRole,
)
from hearth_auth.values import HashedSecret, PermissionName
from hearth_commons import EmailAddress


@pytest.fixture
async def harness(
    make_harness: Callable[[], _SqlAlchemyHarness],
) -> AsyncIterator[_SqlAlchemyHarness]:
    h = make_harness()
    await h.setup(entities=[
        User, Role, Permission, UserRole, RolePermission, ApiKey, ApiKeyPermission,
    ])
    try:
        await h.reset()
        yield h
    finally:
        await h.teardown()


async def test_user_role_round_trip(harness: _SqlAlchemyHarness) -> None:
    async with harness.transaction() as uow:
        u = User(
            email=EmailAddress(raw="alice@example.com"),
            password=HashedSecret.from_plaintext("p"),
        )
        await uow.save(u)
        r = Role(name="admin")
        await uow.save(r)
        ur = UserRole(user_id=u.id, role_id=r.id)
        await uow.save(ur)
        assert ur.granted_at is not None
        assert ur.granted_by_id is None


async def test_role_permission_round_trip(harness: _SqlAlchemyHarness) -> None:
    async with harness.transaction() as uow:
        r = Role(name="admin")
        await uow.save(r)
        p = Permission(name=PermissionName(resource="orders", action="read"))
        await uow.save(p)
        rp = RolePermission(role_id=r.id, permission_id=p.id)
        await uow.save(rp)


async def test_api_key_round_trip(harness: _SqlAlchemyHarness) -> None:
    async with harness.transaction() as uow:
        k = ApiKey(
            name="webhook-tigo",
            key_prefix="hak_abc12",
            key_hash=HashedSecret.from_plaintext("hak_abc12345_long_secret"),
            owner_id=None,
            expires_at=None,
            revoked_at=None,
        )
        await uow.save(k)
        assert k.id is not None
        assert k.actor_kind == "api_key"


async def test_api_key_permission_round_trip(harness: _SqlAlchemyHarness) -> None:
    async with harness.transaction() as uow:
        k = ApiKey(
            name="webhook",
            key_prefix="hak_xyz",
            key_hash=HashedSecret.from_plaintext("hak_xyz_secret"),
            owner_id=None,
            expires_at=None,
            revoked_at=None,
        )
        await uow.save(k)
        p = Permission(name=PermissionName(resource="webhooks", action="receive"))
        await uow.save(p)
        akp = ApiKeyPermission(api_key_id=k.id, permission_id=p.id)
        await uow.save(akp)
```

- [ ] **Step 2: Run the test — expect failure**

```bash
uv run pytest plugins/auth/tests/test_entities_joins.py -v
```

Expected: FAIL — ImportError on the new join entities.

- [ ] **Step 3: Create `plugins/auth/hearth_auth/entities/user_role.py`**

```python
"""UserRole — many-to-many join between Users and Roles."""

from __future__ import annotations

from datetime import datetime

from hearth import EntityId, Field, ForeignKey, References
from hearth_auth._base import Entity
from hearth_auth.entities.role import Role
from hearth_auth.entities.user import User


class UserRole(Entity):
    user_id: EntityId = ForeignKey()
    user: User = References(User)
    role_id: EntityId = ForeignKey()
    role: Role = References(Role)
    granted_at: datetime = Field(auto_now_add=True)
    granted_by_id: EntityId | None = None
    granted_by: User | None = References(User)
```

- [ ] **Step 4: Create `plugins/auth/hearth_auth/entities/role_permission.py`**

```python
"""RolePermission — many-to-many join between Roles and Permissions."""

from __future__ import annotations

from hearth import EntityId, ForeignKey, References
from hearth_auth._base import Entity
from hearth_auth.entities.permission import Permission
from hearth_auth.entities.role import Role


class RolePermission(Entity):
    role_id: EntityId = ForeignKey()
    role: Role = References(Role)
    permission_id: EntityId = ForeignKey()
    permission: Permission = References(Permission)
```

- [ ] **Step 5: Create `plugins/auth/hearth_auth/entities/api_key.py`**

```python
"""ApiKey entity — a programmatic actor authenticated by an opaque key string.
Actor-capable."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, ClassVar

from hearth import EntityId, ForeignKey, References
from hearth_auth._base import Entity
from hearth_auth.entities.user import User
from hearth_auth.values import HashedSecret

if TYPE_CHECKING:
    from hearth.unit_of_work import UnitOfWork


class ApiKey(Entity):
    actor_kind: ClassVar[str] = "api_key"

    name: str
    key_prefix: str
    key_hash: HashedSecret
    owner_id: EntityId | None = None
    owner: User | None = References(User)
    expires_at: datetime | None = None
    revoked_at: datetime | None = None

    async def has_permission(self, uow: "UnitOfWork", permission: str) -> bool:
        """Resolve permissions via ApiKeyPermission → Permission.
        Implemented in Task 11."""
        raise NotImplementedError("ApiKey.has_permission lands in Task 11")
```

- [ ] **Step 6: Create `plugins/auth/hearth_auth/entities/api_key_permission.py`**

```python
"""ApiKeyPermission — direct grant from ApiKey to Permission (no role layer)."""

from __future__ import annotations

from hearth import EntityId, ForeignKey, References
from hearth_auth._base import Entity
from hearth_auth.entities.api_key import ApiKey
from hearth_auth.entities.permission import Permission


class ApiKeyPermission(Entity):
    api_key_id: EntityId = ForeignKey()
    api_key: ApiKey = References(ApiKey)
    permission_id: EntityId = ForeignKey()
    permission: Permission = References(Permission)
```

- [ ] **Step 7: Update `plugins/auth/hearth_auth/entities/__init__.py`**

```python
"""Auth plugin entity types."""

from hearth_auth.entities.api_key import ApiKey
from hearth_auth.entities.api_key_permission import ApiKeyPermission
from hearth_auth.entities.permission import Permission
from hearth_auth.entities.role import Role
from hearth_auth.entities.role_permission import RolePermission
from hearth_auth.entities.user import User
from hearth_auth.entities.user_role import UserRole

__all__ = [
    "ApiKey",
    "ApiKeyPermission",
    "Permission",
    "Role",
    "RolePermission",
    "User",
    "UserRole",
]
```

- [ ] **Step 8: Run the test — expect pass**

```bash
uv run pytest plugins/auth/tests/test_entities_joins.py -v
```

Expected: 4 PASS.

---

## Task 11: `User.has_permission` and `ApiKey.has_permission` implementations

**Files:**
- Modify: `plugins/auth/hearth_auth/entities/user.py` — implement `has_permission`
- Modify: `plugins/auth/hearth_auth/entities/api_key.py` — implement `has_permission`
- Create: `plugins/auth/tests/test_has_permission.py`

- [ ] **Step 1: Write the failing test**

Create `plugins/auth/tests/test_has_permission.py`:

```python
"""Tests for has_permission resolution on User and ApiKey."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime, timedelta

import pytest

from hearth import EntityId
from hearth.testing._base import _SqlAlchemyHarness
from hearth_auth.entities import (
    ApiKey,
    ApiKeyPermission,
    Permission,
    Role,
    RolePermission,
    User,
    UserRole,
)
from hearth_auth.values import HashedSecret, PermissionName
from hearth_commons import EmailAddress

ENTITY_LIST = [User, Role, Permission, UserRole, RolePermission, ApiKey, ApiKeyPermission]


@pytest.fixture
async def harness(
    make_harness: Callable[[], _SqlAlchemyHarness],
) -> AsyncIterator[_SqlAlchemyHarness]:
    h = make_harness()
    await h.setup(entities=ENTITY_LIST)
    try:
        await h.reset()
        yield h
    finally:
        await h.teardown()


async def _make_user_with_perm(
    harness: _SqlAlchemyHarness, perm_resource: str, perm_action: str
) -> User:
    async with harness.transaction() as uow:
        u = User(email=EmailAddress(raw=f"u-{perm_resource}@example.com"),
                 password=HashedSecret.from_plaintext("x"))
        await uow.save(u)
        r = Role(name=f"role-{perm_resource}")
        await uow.save(r)
        p = Permission(name=PermissionName(resource=perm_resource, action=perm_action))
        await uow.save(p)
        await uow.save(UserRole(user_id=u.id, role_id=r.id))
        await uow.save(RolePermission(role_id=r.id, permission_id=p.id))
        return u


async def test_user_has_granted_permission(harness: _SqlAlchemyHarness) -> None:
    u = await _make_user_with_perm(harness, "orders", "read")
    async with harness.transaction() as uow:
        u_fresh = await uow.query(User).where(User.id == u.id).one()
        assert await u_fresh.has_permission(uow, "orders:read") is True


async def test_user_lacks_ungranted_permission(harness: _SqlAlchemyHarness) -> None:
    u = await _make_user_with_perm(harness, "orders", "read")
    async with harness.transaction() as uow:
        u_fresh = await uow.query(User).where(User.id == u.id).one()
        assert await u_fresh.has_permission(uow, "orders:write") is False
        assert await u_fresh.has_permission(uow, "users:read") is False


async def test_user_wildcard_short_circuits(harness: _SqlAlchemyHarness) -> None:
    async with harness.transaction() as uow:
        u = User(email=EmailAddress(raw="admin@example.com"),
                 password=HashedSecret.from_plaintext("x"))
        await uow.save(u)
        r = Role(name="admin")
        await uow.save(r)
        wildcard = Permission(name=PermissionName(resource="*", action="*"))
        await uow.save(wildcard)
        await uow.save(UserRole(user_id=u.id, role_id=r.id))
        await uow.save(RolePermission(role_id=r.id, permission_id=wildcard.id))

    async with harness.transaction() as uow:
        u_fresh = await uow.query(User).where(User.id == u.id).one()
        assert await u_fresh.has_permission(uow, "anything:goes") is True
        assert await u_fresh.has_permission(uow, "orders:read") is True


async def test_api_key_has_granted_permission(harness: _SqlAlchemyHarness) -> None:
    async with harness.transaction() as uow:
        k = ApiKey(name="x", key_prefix="hak_abc",
                   key_hash=HashedSecret.from_plaintext("s"))
        await uow.save(k)
        p = Permission(name=PermissionName(resource="webhooks", action="receive"))
        await uow.save(p)
        await uow.save(ApiKeyPermission(api_key_id=k.id, permission_id=p.id))

    async with harness.transaction() as uow:
        k_fresh = await uow.query(ApiKey).where(ApiKey.id == k.id).one()
        assert await k_fresh.has_permission(uow, "webhooks:receive") is True
        assert await k_fresh.has_permission(uow, "orders:read") is False


async def test_api_key_wildcard_short_circuits(harness: _SqlAlchemyHarness) -> None:
    async with harness.transaction() as uow:
        k = ApiKey(name="superkey", key_prefix="hak_sup",
                   key_hash=HashedSecret.from_plaintext("s"))
        await uow.save(k)
        wildcard = Permission(name=PermissionName(resource="*", action="*"))
        await uow.save(wildcard)
        await uow.save(ApiKeyPermission(api_key_id=k.id, permission_id=wildcard.id))

    async with harness.transaction() as uow:
        k_fresh = await uow.query(ApiKey).where(ApiKey.id == k.id).one()
        assert await k_fresh.has_permission(uow, "anything:goes") is True


async def test_api_key_revoked_blocks_all_permissions(harness: _SqlAlchemyHarness) -> None:
    async with harness.transaction() as uow:
        k = ApiKey(name="revoked", key_prefix="hak_rev",
                   key_hash=HashedSecret.from_plaintext("s"),
                   revoked_at=datetime.now(UTC))
        await uow.save(k)
        wildcard = Permission(name=PermissionName(resource="*", action="*"))
        await uow.save(wildcard)
        await uow.save(ApiKeyPermission(api_key_id=k.id, permission_id=wildcard.id))

    async with harness.transaction() as uow:
        k_fresh = await uow.query(ApiKey).where(ApiKey.id == k.id).one()
        assert await k_fresh.has_permission(uow, "anything:goes") is False


async def test_api_key_expired_blocks_all_permissions(harness: _SqlAlchemyHarness) -> None:
    past = datetime.now(UTC) - timedelta(days=1)
    async with harness.transaction() as uow:
        k = ApiKey(name="expired", key_prefix="hak_exp",
                   key_hash=HashedSecret.from_plaintext("s"),
                   expires_at=past)
        await uow.save(k)
        wildcard = Permission(name=PermissionName(resource="*", action="*"))
        await uow.save(wildcard)
        await uow.save(ApiKeyPermission(api_key_id=k.id, permission_id=wildcard.id))

    async with harness.transaction() as uow:
        k_fresh = await uow.query(ApiKey).where(ApiKey.id == k.id).one()
        assert await k_fresh.has_permission(uow, "anything:goes") is False
```

- [ ] **Step 2: Run the test — expect failure**

```bash
uv run pytest plugins/auth/tests/test_has_permission.py -v
```

Expected: FAIL with NotImplementedError.

- [ ] **Step 3: Implement `User.has_permission`**

In `plugins/auth/hearth_auth/entities/user.py`, replace the `has_permission` method:

```python
    async def has_permission(self, uow: "UnitOfWork", permission: str) -> bool:
        """Resolve via UserRole → RolePermission → Permission. Wildcard `*:*`
        short-circuits to True."""
        if ":" not in permission:
            return False
        resource, action = permission.split(":", 1)

        # Local imports to avoid a top-level cycle.
        from hearth_auth.entities.permission import Permission
        from hearth_auth.entities.role_permission import RolePermission
        from hearth_auth.entities.user_role import UserRole

        # Wildcard check
        wildcard_exists = await (
            uow.query(Permission)
            .join(RolePermission, RolePermission.permission_id == Permission.id)
            .join(UserRole, UserRole.role_id == RolePermission.role_id)
            .where(UserRole.user_id == self.id)
            .where(Permission.name.resource == "*")
            .where(Permission.name.action == "*")
            .exists()
        )
        if wildcard_exists:
            return True

        return await (
            uow.query(Permission)
            .join(RolePermission, RolePermission.permission_id == Permission.id)
            .join(UserRole, UserRole.role_id == RolePermission.role_id)
            .where(UserRole.user_id == self.id)
            .where(Permission.name.resource == resource)
            .where(Permission.name.action == action)
            .exists()
        )
```

- [ ] **Step 4: Implement `ApiKey.has_permission`**

In `plugins/auth/hearth_auth/entities/api_key.py`, replace the `has_permission` method:

```python
    async def has_permission(self, uow: "UnitOfWork", permission: str) -> bool:
        """Resolve via ApiKeyPermission → Permission. Revoked/expired keys
        deny everything. Wildcard `*:*` short-circuits to True."""
        from datetime import UTC, datetime

        if self.revoked_at is not None:
            return False
        if self.expires_at is not None and self.expires_at < datetime.now(UTC):
            return False
        if ":" not in permission:
            return False
        resource, action = permission.split(":", 1)

        # Local imports to avoid a top-level cycle.
        from hearth_auth.entities.api_key_permission import ApiKeyPermission
        from hearth_auth.entities.permission import Permission

        # Wildcard check
        wildcard_exists = await (
            uow.query(Permission)
            .join(ApiKeyPermission, ApiKeyPermission.permission_id == Permission.id)
            .where(ApiKeyPermission.api_key_id == self.id)
            .where(Permission.name.resource == "*")
            .where(Permission.name.action == "*")
            .exists()
        )
        if wildcard_exists:
            return True

        return await (
            uow.query(Permission)
            .join(ApiKeyPermission, ApiKeyPermission.permission_id == Permission.id)
            .where(ApiKeyPermission.api_key_id == self.id)
            .where(Permission.name.resource == resource)
            .where(Permission.name.action == action)
            .exists()
        )
```

- [ ] **Step 5: Run the tests — expect pass**

```bash
uv run pytest plugins/auth/tests/test_has_permission.py -v
```

Expected: 7 PASS.

---

## Task 12: Helpers — exceptions + `require_permission` + `@requires` decorator

**Files:**
- Create: `plugins/auth/hearth_auth/helpers.py`
- Create: `plugins/auth/tests/test_helpers.py`
- Modify: `plugins/auth/hearth_auth/__init__.py` — re-export helpers

- [ ] **Step 1: Write the failing test**

Create `plugins/auth/tests/test_helpers.py`:

```python
"""Tests for hearth_auth helpers: exceptions, require_permission, @requires."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from typing import Any

import pytest

from hearth import Action, Anonymous, System
from hearth.testing._base import _SqlAlchemyHarness
from hearth_auth import (
    AuthenticationFailed,
    PermissionDenied,
    require_permission,
    requires,
)
from hearth_auth.entities import (
    ApiKey,
    ApiKeyPermission,
    Permission,
    Role,
    RolePermission,
    User,
    UserRole,
)
from hearth_auth.values import HashedSecret, PermissionName
from hearth_commons import EmailAddress

ENTITY_LIST = [User, Role, Permission, UserRole, RolePermission, ApiKey, ApiKeyPermission]


@pytest.fixture
async def harness(
    make_harness: Callable[[], _SqlAlchemyHarness],
) -> AsyncIterator[_SqlAlchemyHarness]:
    h = make_harness()
    await h.setup(entities=ENTITY_LIST)
    try:
        await h.reset()
        yield h
    finally:
        await h.teardown()


async def test_require_permission_passes_for_system(harness: _SqlAlchemyHarness) -> None:
    async with harness.transaction() as uow:
        await require_permission(uow, System(), "anything:goes")
        # No exception = pass


async def test_require_permission_raises_for_anonymous(harness: _SqlAlchemyHarness) -> None:
    async with harness.transaction() as uow:
        with pytest.raises(PermissionDenied) as exc:
            await require_permission(uow, Anonymous(), "x:y")
    assert exc.value.permission == "x:y"


async def test_requires_decorator_blocks_unauthorized(harness: _SqlAlchemyHarness) -> None:
    @requires("orders:read")
    class _Read(Action):
        async def handle(self, uow, actor):  # type: ignore[no-untyped-def]
            return "secret-data"

    with pytest.raises(PermissionDenied):
        await harness.run(_Read(), actor=Anonymous())


async def test_requires_decorator_allows_authorized(harness: _SqlAlchemyHarness) -> None:
    @requires("orders:read")
    class _Read(Action):
        async def handle(self, uow, actor):  # type: ignore[no-untyped-def]
            return "secret-data"

    result = await harness.run(_Read(), actor=System())
    assert result == "secret-data"


async def test_requires_decorator_sets_classvar(harness: _SqlAlchemyHarness) -> None:
    @requires("orders:read", "orders:write")
    class _Action(Action):
        async def handle(self, uow, actor):  # type: ignore[no-untyped-def]
            return None

    assert _Action._hearth_required_permissions == ("orders:read", "orders:write")  # pyright: ignore[reportAttributeAccessIssue]


def test_authentication_failed_carries_message() -> None:
    err = AuthenticationFailed("bad creds")
    assert "bad creds" in str(err)


def test_permission_denied_carries_actor_and_permission() -> None:
    a = Anonymous()
    err = PermissionDenied(a, "orders:read")
    assert err.actor is a
    assert err.permission == "orders:read"
    assert "orders:read" in str(err)
```

- [ ] **Step 2: Run the test — expect failure**

```bash
uv run pytest plugins/auth/tests/test_helpers.py -v
```

Expected: FAIL — ImportError on `hearth_auth.require_permission`.

- [ ] **Step 3: Create `plugins/auth/hearth_auth/helpers.py`**

```python
"""Auth-plugin helpers: enforcement decorator + imperative check + exceptions."""

from __future__ import annotations

import functools
from collections.abc import Callable
from typing import Any, ClassVar

from hearth import Action, Actor
from hearth.unit_of_work import UnitOfWork


class AuthenticationFailed(Exception):
    """Raised by AuthenticateUser / AuthenticateApiKey on bad/missing/expired credentials.
    The message is intentionally generic ("invalid credentials") to avoid
    leaking which branch (no user vs wrong password vs disabled) failed."""


class PermissionDenied(Exception):
    """Raised by require_permission and the @requires decorator when an actor
    lacks the requested permission."""

    def __init__(self, actor: Actor, permission: str) -> None:
        self.actor = actor
        self.permission = permission
        super().__init__(
            f"actor of kind {actor.actor_kind!r} denied permission {permission!r}"
        )


async def require_permission(
    uow: UnitOfWork, actor: Actor, permission: str
) -> None:
    """Raise PermissionDenied if `actor` lacks `permission`. No-op on success."""
    if not await actor.has_permission(uow, permission):
        raise PermissionDenied(actor, permission)


def requires(*permissions: str) -> Callable[[type[Action]], type[Action]]:
    """Decorator: gate an Action class on static permissions.

    Wraps the class's `handle` so every listed permission is checked before
    the body runs. For dynamic checks (e.g., row-level ownership), call
    `require_permission` inside the body instead.

    LIMITATION: the decorator wraps `cls.handle` at decoration time. A
    subclass that overrides `handle` REPLACES the wrapped version and
    bypasses the gate silently. Plugin authors who subclass a `@requires`-
    decorated Action MUST reapply `@requires(...)` on the subclass.

    Side effect: sets `_hearth_required_permissions: tuple[str, ...]`
    on the class for test helpers and admin tools to introspect.
    """

    def wrap(cls: type[Action]) -> type[Action]:
        cls._hearth_required_permissions = permissions  # type: ignore[attr-defined]
        original_handle = cls.handle

        @functools.wraps(original_handle)
        async def handle(self: Action, uow: UnitOfWork, actor: Actor) -> Any:
            for p in permissions:
                await require_permission(uow, actor, p)
            return await original_handle(self, uow, actor)

        cls.handle = handle  # type: ignore[method-assign]
        return cls

    return wrap
```

- [ ] **Step 4: Update `plugins/auth/hearth_auth/__init__.py`**

```python
"""hearth_auth: users, roles, permissions, and api keys for Hearth."""

from hearth_auth.entities import (
    ApiKey,
    ApiKeyPermission,
    Permission,
    Role,
    RolePermission,
    User,
    UserRole,
)
from hearth_auth.helpers import (
    AuthenticationFailed,
    PermissionDenied,
    require_permission,
    requires,
)
from hearth_auth.values import HashedSecret, PermissionName

__all__ = [
    "ApiKey",
    "ApiKeyPermission",
    "AuthenticationFailed",
    "HashedSecret",
    "Permission",
    "PermissionDenied",
    "PermissionName",
    "Role",
    "RolePermission",
    "User",
    "UserRole",
    "require_permission",
    "requires",
]
```

- [ ] **Step 5: Run the tests — expect pass**

```bash
uv run pytest plugins/auth/tests/test_helpers.py -v
```

Expected: 7 PASS.

---

## Task 13: User Actions + Events — `CreateUser`, `ChangePassword`, `DisableUser`, `EnableUser`

**Files:**
- Create: `plugins/auth/hearth_auth/actions/__init__.py`
- Create: `plugins/auth/hearth_auth/actions/user.py`
- Create: `plugins/auth/hearth_auth/events/__init__.py`
- Create: `plugins/auth/hearth_auth/events/user.py`
- Create: `plugins/auth/tests/test_user_actions.py`

- [ ] **Step 1: Write the failing test**

Create `plugins/auth/tests/test_user_actions.py`:

```python
"""Tests for user lifecycle actions (excluding AuthenticateUser — that's its own file)."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable

import pytest
from pydantic import SecretStr

from hearth import EntityId, System
from hearth.testing._base import _SqlAlchemyHarness
from hearth_auth.actions import ChangePassword, CreateUser, DisableUser, EnableUser
from hearth_auth.entities import (
    ApiKey,
    ApiKeyPermission,
    Permission,
    Role,
    RolePermission,
    User,
    UserRole,
)
from hearth_auth.events import (
    PasswordChanged,
    UserCreated,
    UserDisabled,
    UserEnabled,
)
from hearth_commons import EmailAddress

ENTITY_LIST = [User, Role, Permission, UserRole, RolePermission, ApiKey, ApiKeyPermission]


@pytest.fixture
async def harness(
    make_harness: Callable[[], _SqlAlchemyHarness],
) -> AsyncIterator[_SqlAlchemyHarness]:
    h = make_harness()
    await h.setup(entities=ENTITY_LIST)
    try:
        await h.reset()
        yield h
    finally:
        await h.teardown()


async def test_create_user_persists_and_hashes(harness: _SqlAlchemyHarness) -> None:
    user = await harness.run(
        CreateUser(
            email=EmailAddress(raw="alice@example.com"),
            password=SecretStr("hunter2"),
            display_name="Alice",
        )
    )
    assert isinstance(user, User)
    assert user.password.hash.startswith("$argon2id$")
    assert user.password.verify("hunter2")

    events = await harness.events_of_type(UserCreated)
    assert len(events) == 1
    assert events[0].user_id == user.id
    assert events[0].email == EmailAddress(raw="alice@example.com")


async def test_change_password_succeeds_with_correct_old(harness: _SqlAlchemyHarness) -> None:
    user = await harness.run(
        CreateUser(email=EmailAddress(raw="bob@example.com"), password=SecretStr("old"))
    )
    await harness.run(
        ChangePassword(
            user_id=user.id, old_password=SecretStr("old"), new_password=SecretStr("new")
        )
    )
    async with harness.transaction() as uow:
        fresh = await uow.query(User).where(User.id == user.id).one()
        assert fresh.password.verify("new")
        assert not fresh.password.verify("old")
    events = await harness.events_of_type(PasswordChanged)
    assert len(events) == 1
    assert events[0].user_id == user.id


async def test_change_password_rejects_wrong_old(harness: _SqlAlchemyHarness) -> None:
    from hearth_auth import AuthenticationFailed

    user = await harness.run(
        CreateUser(email=EmailAddress(raw="carol@example.com"), password=SecretStr("right"))
    )
    with pytest.raises(AuthenticationFailed):
        await harness.run(
            ChangePassword(
                user_id=user.id,
                old_password=SecretStr("wrong"),
                new_password=SecretStr("new"),
            )
        )


async def test_disable_and_enable_user(harness: _SqlAlchemyHarness) -> None:
    user = await harness.run(
        CreateUser(email=EmailAddress(raw="dave@example.com"), password=SecretStr("x"))
    )
    await harness.run(DisableUser(user_id=user.id))
    async with harness.transaction() as uow:
        fresh = await uow.query(User).where(User.id == user.id).one()
        assert fresh.is_active is False
    assert len(await harness.events_of_type(UserDisabled)) == 1

    await harness.run(EnableUser(user_id=user.id))
    async with harness.transaction() as uow:
        fresh = await uow.query(User).where(User.id == user.id).one()
        assert fresh.is_active is True
    assert len(await harness.events_of_type(UserEnabled)) == 1
```

- [ ] **Step 2: Run the test — expect failure**

```bash
uv run pytest plugins/auth/tests/test_user_actions.py -v
```

Expected: FAIL — ImportError on actions/events.

- [ ] **Step 3: Create `plugins/auth/hearth_auth/events/user.py`**

```python
"""Events emitted by user-lifecycle actions."""

from __future__ import annotations

from hearth import EntityId
from hearth_auth._base import Event
from hearth_commons import EmailAddress


class UserCreated(Event):
    user_id: EntityId
    email: EmailAddress


class PasswordChanged(Event):
    user_id: EntityId


class UserDisabled(Event):
    user_id: EntityId


class UserEnabled(Event):
    user_id: EntityId


class UserAuthenticated(Event):
    user_id: EntityId
```

- [ ] **Step 4: Create `plugins/auth/hearth_auth/events/__init__.py`**

```python
"""Auth plugin events."""

from hearth_auth.events.user import (
    PasswordChanged,
    UserAuthenticated,
    UserCreated,
    UserDisabled,
    UserEnabled,
)

__all__ = [
    "PasswordChanged",
    "UserAuthenticated",
    "UserCreated",
    "UserDisabled",
    "UserEnabled",
]
```

- [ ] **Step 5: Create `plugins/auth/hearth_auth/actions/user.py`** (without AuthenticateUser — that lands in Task 14)

```python
"""User-lifecycle actions: Create, ChangePassword, Disable, Enable.
AuthenticateUser lives in actions/user_auth.py for clarity of the timing-
constant flow."""

from __future__ import annotations

import asyncio
from typing import Any

from pydantic import SecretStr

from hearth import EntityId, UnitOfWork
from hearth_auth._base import Action
from hearth_auth.entities.user import User
from hearth_auth.events.user import (
    PasswordChanged,
    UserCreated,
    UserDisabled,
    UserEnabled,
)
from hearth_auth.helpers import AuthenticationFailed
from hearth_auth.values import HashedSecret
from hearth_commons import EmailAddress


class CreateUser(Action):
    email: EmailAddress
    password: SecretStr
    display_name: str | None = None

    async def handle(self, uow: UnitOfWork, actor: Any) -> User:
        hashed = await asyncio.to_thread(HashedSecret.from_plaintext, self.password)
        user = User(
            email=self.email,
            password=hashed,
            display_name=self.display_name,
        )
        await uow.save(user)
        assert user.id is not None
        uow.emit(UserCreated(user_id=user.id, email=self.email))
        return user


class ChangePassword(Action):
    user_id: EntityId
    old_password: SecretStr
    new_password: SecretStr

    async def handle(self, uow: UnitOfWork, actor: Any) -> None:
        user = await uow.query(User).where(User.id == self.user_id).one_or_none()
        if user is None:
            raise AuthenticationFailed("invalid credentials")
        ok = await asyncio.to_thread(user.password.verify, self.old_password)
        if not ok:
            raise AuthenticationFailed("invalid credentials")
        new_hashed = await asyncio.to_thread(HashedSecret.from_plaintext, self.new_password)
        user.password = new_hashed
        await uow.save(user)
        uow.emit(PasswordChanged(user_id=user.id))


class DisableUser(Action):
    user_id: EntityId

    async def handle(self, uow: UnitOfWork, actor: Any) -> None:
        user = await uow.query(User).where(User.id == self.user_id).one()
        user.is_active = False
        await uow.save(user)
        uow.emit(UserDisabled(user_id=user.id))


class EnableUser(Action):
    user_id: EntityId

    async def handle(self, uow: UnitOfWork, actor: Any) -> None:
        user = await uow.query(User).where(User.id == self.user_id).one()
        user.is_active = True
        await uow.save(user)
        uow.emit(UserEnabled(user_id=user.id))
```

- [ ] **Step 6: Create `plugins/auth/hearth_auth/actions/__init__.py`** (initial; AuthenticateUser etc. appended in later tasks)

```python
"""Auth plugin actions."""

from hearth_auth.actions.user import (
    ChangePassword,
    CreateUser,
    DisableUser,
    EnableUser,
)

__all__ = [
    "ChangePassword",
    "CreateUser",
    "DisableUser",
    "EnableUser",
]
```

- [ ] **Step 7: Run the tests — expect pass**

```bash
uv run pytest plugins/auth/tests/test_user_actions.py -v
```

Expected: 4 PASS.

---

## Task 14: `AuthenticateUser` action (timing-constant flow)

**Files:**
- Create: `plugins/auth/hearth_auth/actions/user_auth.py`
- Modify: `plugins/auth/hearth_auth/actions/__init__.py` — export AuthenticateUser
- Create: `plugins/auth/tests/test_authentication.py`

- [ ] **Step 1: Write the failing test**

Create `plugins/auth/tests/test_authentication.py`:

```python
"""Tests for AuthenticateUser. Timing-constant behavior is asserted by
counting verify() invocations rather than wall-clock time."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from unittest.mock import patch

import pytest
from pydantic import SecretStr

from hearth.testing._base import _SqlAlchemyHarness
from hearth_auth import AuthenticationFailed
from hearth_auth.actions import AuthenticateUser, CreateUser, DisableUser
from hearth_auth.entities import (
    ApiKey, ApiKeyPermission, Permission, Role, RolePermission, User, UserRole,
)
from hearth_auth.events import UserAuthenticated
from hearth_auth.values import HashedSecret
from hearth_commons import EmailAddress

ENTITY_LIST = [User, Role, Permission, UserRole, RolePermission, ApiKey, ApiKeyPermission]


@pytest.fixture
async def harness(
    make_harness: Callable[[], _SqlAlchemyHarness],
) -> AsyncIterator[_SqlAlchemyHarness]:
    h = make_harness()
    await h.setup(entities=ENTITY_LIST)
    try:
        await h.reset()
        yield h
    finally:
        await h.teardown()


async def test_authenticate_user_success(harness: _SqlAlchemyHarness) -> None:
    created = await harness.run(
        CreateUser(email=EmailAddress(raw="alice@example.com"), password=SecretStr("pw"))
    )
    authed = await harness.run(
        AuthenticateUser(email=EmailAddress(raw="alice@example.com"), password=SecretStr("pw"))
    )
    assert authed.id == created.id
    events = await harness.events_of_type(UserAuthenticated)
    assert any(e.user_id == created.id for e in events)


async def test_authenticate_user_wrong_password(harness: _SqlAlchemyHarness) -> None:
    await harness.run(
        CreateUser(email=EmailAddress(raw="bob@example.com"), password=SecretStr("right"))
    )
    with pytest.raises(AuthenticationFailed):
        await harness.run(
            AuthenticateUser(
                email=EmailAddress(raw="bob@example.com"), password=SecretStr("wrong")
            )
        )


async def test_authenticate_user_unknown_email(harness: _SqlAlchemyHarness) -> None:
    with pytest.raises(AuthenticationFailed):
        await harness.run(
            AuthenticateUser(
                email=EmailAddress(raw="ghost@example.com"),
                password=SecretStr("anything"),
            )
        )


async def test_authenticate_user_disabled(harness: _SqlAlchemyHarness) -> None:
    user = await harness.run(
        CreateUser(email=EmailAddress(raw="carol@example.com"), password=SecretStr("pw"))
    )
    await harness.run(DisableUser(user_id=user.id))
    with pytest.raises(AuthenticationFailed):
        await harness.run(
            AuthenticateUser(
                email=EmailAddress(raw="carol@example.com"), password=SecretStr("pw")
            )
        )


async def test_authenticate_user_timing_runs_verify_for_unknown_email(
    harness: _SqlAlchemyHarness,
) -> None:
    """Timing-constant requirement: even when the email is not found,
    AuthenticateUser MUST still invoke a verify() call (against the
    sentinel) so an attacker cannot enumerate by response time."""
    call_count = {"n": 0}

    real_verify = HashedSecret.verify

    def counted(self: HashedSecret, plaintext) -> bool:  # type: ignore[no-untyped-def]
        call_count["n"] += 1
        return real_verify(self, plaintext)

    with patch.object(HashedSecret, "verify", counted):
        with pytest.raises(AuthenticationFailed):
            await harness.run(
                AuthenticateUser(
                    email=EmailAddress(raw="never-existed@example.com"),
                    password=SecretStr("anything"),
                )
            )
    assert call_count["n"] >= 1


async def test_authenticate_user_updates_last_login_at(harness: _SqlAlchemyHarness) -> None:
    user = await harness.run(
        CreateUser(email=EmailAddress(raw="dave@example.com"), password=SecretStr("pw"))
    )
    assert user.last_login_at is None
    authed = await harness.run(
        AuthenticateUser(email=EmailAddress(raw="dave@example.com"), password=SecretStr("pw"))
    )
    assert authed.last_login_at is not None
```

- [ ] **Step 2: Run the test — expect failure**

```bash
uv run pytest plugins/auth/tests/test_authentication.py -v
```

Expected: FAIL — ImportError on AuthenticateUser.

- [ ] **Step 3: Create `plugins/auth/hearth_auth/actions/user_auth.py`**

```python
"""AuthenticateUser action with timing-constant flow.

The sentinel HashedSecret ensures we always run verify() once regardless of
whether the email lookup succeeded — preventing email enumeration via
response-time analysis."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from pydantic import SecretStr

from hearth import UnitOfWork
from hearth_auth._base import Action
from hearth_auth.entities.user import User
from hearth_auth.events.user import UserAuthenticated
from hearth_auth.helpers import AuthenticationFailed
from hearth_auth.values import HashedSecret
from hearth_commons import EmailAddress

# Module-level sentinel hashed once at import. Used to equalize timing when
# the email is not found — verify() always runs at least once.
_SENTINEL_HASHED = HashedSecret.from_plaintext("sentinel-not-a-real-password")


class AuthenticateUser(Action):
    email: EmailAddress
    password: SecretStr

    async def handle(self, uow: UnitOfWork, actor: Any) -> User:
        user = await uow.query(User).where(User.email == self.email).one_or_none()
        target = user.password if user is not None else _SENTINEL_HASHED
        ok = await asyncio.to_thread(target.verify, self.password)
        # All failure branches produce the same exception with the same message
        # — no information leaks via error type or text.
        if user is None or not ok or not user.is_active:
            raise AuthenticationFailed("invalid credentials")
        user.last_login_at = datetime.now(UTC)
        await uow.save(user)
        uow.emit(UserAuthenticated(user_id=user.id))
        return user
```

- [ ] **Step 4: Update `plugins/auth/hearth_auth/actions/__init__.py`**

```python
"""Auth plugin actions."""

from hearth_auth.actions.user import (
    ChangePassword,
    CreateUser,
    DisableUser,
    EnableUser,
)
from hearth_auth.actions.user_auth import AuthenticateUser

__all__ = [
    "AuthenticateUser",
    "ChangePassword",
    "CreateUser",
    "DisableUser",
    "EnableUser",
]
```

- [ ] **Step 5: Run the tests — expect pass**

```bash
uv run pytest plugins/auth/tests/test_authentication.py -v
```

Expected: 6 PASS.

---

## Task 15: Role + Permission Actions + Events

**Files:**
- Create: `plugins/auth/hearth_auth/actions/role.py`
- Create: `plugins/auth/hearth_auth/actions/permission.py`
- Create: `plugins/auth/hearth_auth/events/role.py`
- Create: `plugins/auth/hearth_auth/events/permission.py`
- Modify: `plugins/auth/hearth_auth/actions/__init__.py`
- Modify: `plugins/auth/hearth_auth/events/__init__.py`
- Create: `plugins/auth/tests/test_role_permission_actions.py`

- [ ] **Step 1: Write the failing test**

Create `plugins/auth/tests/test_role_permission_actions.py`:

```python
"""Tests for Role + Permission lifecycle actions."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable

import pytest

from hearth.testing._base import _SqlAlchemyHarness
from hearth_auth.actions import (
    AssignPermissionToRole,
    AssignRoleToUser,
    CreatePermission,
    CreateRole,
    CreateUser,
    RevokePermissionFromRole,
    RevokeRoleFromUser,
)
from hearth_auth.entities import (
    ApiKey, ApiKeyPermission, Permission, Role, RolePermission, User, UserRole,
)
from hearth_auth.events import (
    PermissionAssignedToRole,
    PermissionCreated,
    PermissionRevokedFromRole,
    RoleAssignedToUser,
    RoleCreated,
    RoleRevokedFromUser,
)
from hearth_auth.values import PermissionName
from hearth_commons import EmailAddress
from pydantic import SecretStr

ENTITY_LIST = [User, Role, Permission, UserRole, RolePermission, ApiKey, ApiKeyPermission]


@pytest.fixture
async def harness(
    make_harness: Callable[[], _SqlAlchemyHarness],
) -> AsyncIterator[_SqlAlchemyHarness]:
    h = make_harness()
    await h.setup(entities=ENTITY_LIST)
    try:
        await h.reset()
        yield h
    finally:
        await h.teardown()


async def test_create_role(harness: _SqlAlchemyHarness) -> None:
    role = await harness.run(CreateRole(name="admin", description="The admin role."))
    assert role.name == "admin"
    events = await harness.events_of_type(RoleCreated)
    assert any(e.role_id == role.id and e.name == "admin" for e in events)


async def test_create_permission(harness: _SqlAlchemyHarness) -> None:
    perm = await harness.run(
        CreatePermission(name=PermissionName(resource="orders", action="read"))
    )
    assert perm.name.resource == "orders"
    events = await harness.events_of_type(PermissionCreated)
    matching = [e for e in events if e.permission_id == perm.id]
    assert len(matching) == 1
    assert matching[0].name == PermissionName(resource="orders", action="read")


async def test_assign_role_to_user(harness: _SqlAlchemyHarness) -> None:
    user = await harness.run(
        CreateUser(email=EmailAddress(raw="x@y"), password=SecretStr("p"))
    )
    role = await harness.run(CreateRole(name="admin"))
    user_role = await harness.run(AssignRoleToUser(user_id=user.id, role_id=role.id))
    assert user_role.user_id == user.id
    assert user_role.role_id == role.id
    assert len(await harness.events_of_type(RoleAssignedToUser)) == 1


async def test_revoke_role_from_user(harness: _SqlAlchemyHarness) -> None:
    user = await harness.run(
        CreateUser(email=EmailAddress(raw="x@y"), password=SecretStr("p"))
    )
    role = await harness.run(CreateRole(name="admin"))
    await harness.run(AssignRoleToUser(user_id=user.id, role_id=role.id))
    await harness.run(RevokeRoleFromUser(user_id=user.id, role_id=role.id))
    async with harness.transaction() as uow:
        count = await uow.query(UserRole).where(UserRole.user_id == user.id).count()
    assert count == 0
    assert len(await harness.events_of_type(RoleRevokedFromUser)) == 1


async def test_assign_permission_to_role(harness: _SqlAlchemyHarness) -> None:
    role = await harness.run(CreateRole(name="r"))
    perm = await harness.run(
        CreatePermission(name=PermissionName(resource="x", action="y"))
    )
    rp = await harness.run(AssignPermissionToRole(role_id=role.id, permission_id=perm.id))
    assert rp.role_id == role.id
    assert rp.permission_id == perm.id
    assert len(await harness.events_of_type(PermissionAssignedToRole)) == 1


async def test_revoke_permission_from_role(harness: _SqlAlchemyHarness) -> None:
    role = await harness.run(CreateRole(name="r"))
    perm = await harness.run(
        CreatePermission(name=PermissionName(resource="x", action="y"))
    )
    await harness.run(AssignPermissionToRole(role_id=role.id, permission_id=perm.id))
    await harness.run(RevokePermissionFromRole(role_id=role.id, permission_id=perm.id))
    async with harness.transaction() as uow:
        count = await uow.query(RolePermission).where(RolePermission.role_id == role.id).count()
    assert count == 0
    assert len(await harness.events_of_type(PermissionRevokedFromRole)) == 1
```

- [ ] **Step 2: Run the test — expect failure**

```bash
uv run pytest plugins/auth/tests/test_role_permission_actions.py -v
```

Expected: FAIL — ImportError.

- [ ] **Step 3: Create `plugins/auth/hearth_auth/events/role.py`**

```python
"""Events emitted by role-lifecycle actions."""

from __future__ import annotations

from hearth import EntityId
from hearth_auth._base import Event


class RoleCreated(Event):
    role_id: EntityId
    name: str


class RoleAssignedToUser(Event):
    user_id: EntityId
    role_id: EntityId
    granted_by_id: EntityId | None


class RoleRevokedFromUser(Event):
    user_id: EntityId
    role_id: EntityId
```

- [ ] **Step 4: Create `plugins/auth/hearth_auth/events/permission.py`**

```python
"""Events emitted by permission-lifecycle actions."""

from __future__ import annotations

from hearth import EntityId
from hearth_auth._base import Event
from hearth_auth.values import PermissionName


class PermissionCreated(Event):
    permission_id: EntityId
    name: PermissionName


class PermissionAssignedToRole(Event):
    role_id: EntityId
    permission_id: EntityId


class PermissionRevokedFromRole(Event):
    role_id: EntityId
    permission_id: EntityId
```

- [ ] **Step 5: Update `plugins/auth/hearth_auth/events/__init__.py`**

```python
"""Auth plugin events."""

from hearth_auth.events.permission import (
    PermissionAssignedToRole,
    PermissionCreated,
    PermissionRevokedFromRole,
)
from hearth_auth.events.role import (
    RoleAssignedToUser,
    RoleCreated,
    RoleRevokedFromUser,
)
from hearth_auth.events.user import (
    PasswordChanged,
    UserAuthenticated,
    UserCreated,
    UserDisabled,
    UserEnabled,
)

__all__ = [
    "PasswordChanged",
    "PermissionAssignedToRole",
    "PermissionCreated",
    "PermissionRevokedFromRole",
    "RoleAssignedToUser",
    "RoleCreated",
    "RoleRevokedFromUser",
    "UserAuthenticated",
    "UserCreated",
    "UserDisabled",
    "UserEnabled",
]
```

- [ ] **Step 6: Create `plugins/auth/hearth_auth/actions/role.py`**

```python
"""Role-lifecycle actions: Create, AssignToUser, RevokeFromUser."""

from __future__ import annotations

from typing import Any

from hearth import EntityId, UnitOfWork
from hearth_auth._base import Action
from hearth_auth.entities.role import Role
from hearth_auth.entities.user_role import UserRole
from hearth_auth.events.role import (
    RoleAssignedToUser,
    RoleCreated,
    RoleRevokedFromUser,
)


class CreateRole(Action):
    name: str
    description: str | None = None

    async def handle(self, uow: UnitOfWork, actor: Any) -> Role:
        role = Role(name=self.name, description=self.description)
        await uow.save(role)
        assert role.id is not None
        uow.emit(RoleCreated(role_id=role.id, name=role.name))
        return role


class AssignRoleToUser(Action):
    user_id: EntityId
    role_id: EntityId

    async def handle(self, uow: UnitOfWork, actor: Any) -> UserRole:
        granted_by_id = getattr(actor, "id", None)
        ur = UserRole(
            user_id=self.user_id,
            role_id=self.role_id,
            granted_by_id=granted_by_id,
        )
        await uow.save(ur)
        uow.emit(
            RoleAssignedToUser(
                user_id=self.user_id,
                role_id=self.role_id,
                granted_by_id=granted_by_id,
            )
        )
        return ur


class RevokeRoleFromUser(Action):
    user_id: EntityId
    role_id: EntityId

    async def handle(self, uow: UnitOfWork, actor: Any) -> None:
        existing = await (
            uow.query(UserRole)
            .where(UserRole.user_id == self.user_id)
            .where(UserRole.role_id == self.role_id)
            .one_or_none()
        )
        if existing is not None:
            await uow.delete(existing)
        uow.emit(RoleRevokedFromUser(user_id=self.user_id, role_id=self.role_id))
```

- [ ] **Step 7: Create `plugins/auth/hearth_auth/actions/permission.py`**

```python
"""Permission-lifecycle actions: Create, AssignToRole, RevokeFromRole."""

from __future__ import annotations

from typing import Any

from hearth import EntityId, UnitOfWork
from hearth_auth._base import Action
from hearth_auth.entities.permission import Permission
from hearth_auth.entities.role_permission import RolePermission
from hearth_auth.events.permission import (
    PermissionAssignedToRole,
    PermissionCreated,
    PermissionRevokedFromRole,
)
from hearth_auth.values import PermissionName


class CreatePermission(Action):
    name: PermissionName
    description: str | None = None

    async def handle(self, uow: UnitOfWork, actor: Any) -> Permission:
        perm = Permission(name=self.name, description=self.description)
        await uow.save(perm)
        assert perm.id is not None
        uow.emit(PermissionCreated(permission_id=perm.id, name=self.name))
        return perm


class AssignPermissionToRole(Action):
    role_id: EntityId
    permission_id: EntityId

    async def handle(self, uow: UnitOfWork, actor: Any) -> RolePermission:
        rp = RolePermission(role_id=self.role_id, permission_id=self.permission_id)
        await uow.save(rp)
        uow.emit(
            PermissionAssignedToRole(
                role_id=self.role_id, permission_id=self.permission_id
            )
        )
        return rp


class RevokePermissionFromRole(Action):
    role_id: EntityId
    permission_id: EntityId

    async def handle(self, uow: UnitOfWork, actor: Any) -> None:
        existing = await (
            uow.query(RolePermission)
            .where(RolePermission.role_id == self.role_id)
            .where(RolePermission.permission_id == self.permission_id)
            .one_or_none()
        )
        if existing is not None:
            await uow.delete(existing)
        uow.emit(
            PermissionRevokedFromRole(
                role_id=self.role_id, permission_id=self.permission_id
            )
        )
```

- [ ] **Step 8: Update `plugins/auth/hearth_auth/actions/__init__.py`**

```python
"""Auth plugin actions."""

from hearth_auth.actions.permission import (
    AssignPermissionToRole,
    CreatePermission,
    RevokePermissionFromRole,
)
from hearth_auth.actions.role import (
    AssignRoleToUser,
    CreateRole,
    RevokeRoleFromUser,
)
from hearth_auth.actions.user import (
    ChangePassword,
    CreateUser,
    DisableUser,
    EnableUser,
)
from hearth_auth.actions.user_auth import AuthenticateUser

__all__ = [
    "AssignPermissionToRole",
    "AssignRoleToUser",
    "AuthenticateUser",
    "ChangePassword",
    "CreatePermission",
    "CreateRole",
    "CreateUser",
    "DisableUser",
    "EnableUser",
    "RevokePermissionFromRole",
    "RevokeRoleFromUser",
]
```

- [ ] **Step 9: Run the tests — expect pass**

```bash
uv run pytest plugins/auth/tests/test_role_permission_actions.py -v
```

Expected: 6 PASS.

---

## Task 16: ApiKey Actions + Events (`CreateApiKey`, `RevokeApiKey`, `AuthenticateApiKey`)

**Files:**
- Create: `plugins/auth/hearth_auth/actions/api_key.py`
- Create: `plugins/auth/hearth_auth/events/api_key.py`
- Modify: `plugins/auth/hearth_auth/actions/__init__.py`
- Modify: `plugins/auth/hearth_auth/events/__init__.py`
- Modify: `plugins/auth/hearth_auth/helpers.py` — add `PermissionNotFound` exception
- Create: `plugins/auth/tests/test_api_key_actions.py`

- [ ] **Step 1: Add `PermissionNotFound` to helpers**

Append to `plugins/auth/hearth_auth/helpers.py`, after `PermissionDenied`:

```python
class PermissionNotFound(Exception):
    """Raised by CreateApiKey when a referenced PermissionName doesn't match
    any existing Permission row. Forces operators to create permissions
    explicitly (via CreatePermission) rather than auto-creating from typos."""

    def __init__(self, names: list[Any]) -> None:
        self.names = names
        super().__init__(
            f"the following PermissionNames are not registered: {names!r}. "
            f"Create them via CreatePermission first."
        )
```

Also export it: in the same file, no `__all__` is defined; rely on `__init__.py` re-export. Update `plugins/auth/hearth_auth/__init__.py` to include `"PermissionNotFound"` in `__all__` and import line. Add `from hearth_auth.helpers import ..., PermissionNotFound` and `"PermissionNotFound"` in the `__all__` list (alphabetical: between `PermissionName` and `Role`).

- [ ] **Step 2: Write the failing test**

Create `plugins/auth/tests/test_api_key_actions.py`:

```python
"""Tests for ApiKey lifecycle and authentication actions."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from pydantic import SecretStr

from hearth.testing._base import _SqlAlchemyHarness
from hearth_auth import AuthenticationFailed, PermissionNotFound
from hearth_auth.actions import (
    AuthenticateApiKey,
    CreateApiKey,
    CreatePermission,
    RevokeApiKey,
)
from hearth_auth.entities import (
    ApiKey, ApiKeyPermission, Permission, Role, RolePermission, User, UserRole,
)
from hearth_auth.events import (
    ApiKeyAuthenticated,
    ApiKeyCreated,
    ApiKeyRevoked,
)
from hearth_auth.values import HashedSecret, PermissionName

ENTITY_LIST = [User, Role, Permission, UserRole, RolePermission, ApiKey, ApiKeyPermission]


@pytest.fixture
async def harness(
    make_harness: Callable[[], _SqlAlchemyHarness],
) -> AsyncIterator[_SqlAlchemyHarness]:
    h = make_harness()
    await h.setup(entities=ENTITY_LIST)
    try:
        await h.reset()
        yield h
    finally:
        await h.teardown()


async def test_create_api_key_returns_plaintext_once(harness: _SqlAlchemyHarness) -> None:
    perm = await harness.run(
        CreatePermission(name=PermissionName(resource="webhooks", action="receive"))
    )
    api_key, plaintext = await harness.run(
        CreateApiKey(
            name="tigo-webhook",
            permissions=[PermissionName(resource="webhooks", action="receive")],
        )
    )
    assert plaintext.startswith("hak_")
    assert api_key.key_prefix == plaintext[:8]
    assert api_key.key_hash.verify(plaintext)
    assert len(await harness.events_of_type(ApiKeyCreated)) == 1


async def test_create_api_key_rejects_unknown_permission(harness: _SqlAlchemyHarness) -> None:
    with pytest.raises(PermissionNotFound):
        await harness.run(
            CreateApiKey(
                name="bad",
                permissions=[PermissionName(resource="ghost", action="haunt")],
            )
        )


async def test_authenticate_api_key_success(harness: _SqlAlchemyHarness) -> None:
    await harness.run(
        CreatePermission(name=PermissionName(resource="webhooks", action="receive"))
    )
    api_key, plaintext = await harness.run(
        CreateApiKey(
            name="k",
            permissions=[PermissionName(resource="webhooks", action="receive")],
        )
    )
    authed = await harness.run(AuthenticateApiKey(key_string=SecretStr(plaintext)))
    assert authed.id == api_key.id
    assert len(await harness.events_of_type(ApiKeyAuthenticated)) == 1


async def test_authenticate_api_key_unknown_key(harness: _SqlAlchemyHarness) -> None:
    with pytest.raises(AuthenticationFailed):
        await harness.run(AuthenticateApiKey(key_string=SecretStr("hak_nopealla_long")))


async def test_authenticate_api_key_revoked(harness: _SqlAlchemyHarness) -> None:
    await harness.run(
        CreatePermission(name=PermissionName(resource="x", action="y"))
    )
    api_key, plaintext = await harness.run(
        CreateApiKey(name="k", permissions=[PermissionName(resource="x", action="y")])
    )
    await harness.run(RevokeApiKey(api_key_id=api_key.id))
    with pytest.raises(AuthenticationFailed):
        await harness.run(AuthenticateApiKey(key_string=SecretStr(plaintext)))
    assert len(await harness.events_of_type(ApiKeyRevoked)) == 1


async def test_authenticate_api_key_expired(harness: _SqlAlchemyHarness) -> None:
    past = datetime.now(UTC) - timedelta(days=1)
    await harness.run(
        CreatePermission(name=PermissionName(resource="x", action="y"))
    )
    _, plaintext = await harness.run(
        CreateApiKey(
            name="k",
            permissions=[PermissionName(resource="x", action="y")],
            expires_at=past,
        )
    )
    with pytest.raises(AuthenticationFailed):
        await harness.run(AuthenticateApiKey(key_string=SecretStr(plaintext)))


async def test_authenticate_api_key_timing_runs_verify_on_unknown(
    harness: _SqlAlchemyHarness,
) -> None:
    call_count = {"n": 0}
    real_verify = HashedSecret.verify

    def counted(self, plaintext):  # type: ignore[no-untyped-def]
        call_count["n"] += 1
        return real_verify(self, plaintext)

    with patch.object(HashedSecret, "verify", counted):
        with pytest.raises(AuthenticationFailed):
            await harness.run(
                AuthenticateApiKey(key_string=SecretStr("hak_unknown_long_secret"))
            )
    assert call_count["n"] >= 1
```

- [ ] **Step 3: Run the test — expect failure**

```bash
uv run pytest plugins/auth/tests/test_api_key_actions.py -v
```

Expected: FAIL — ImportError on the new actions/events.

- [ ] **Step 4: Create `plugins/auth/hearth_auth/events/api_key.py`**

```python
"""Events emitted by ApiKey-lifecycle and authentication actions."""

from __future__ import annotations

from hearth import EntityId
from hearth_auth._base import Event


class ApiKeyCreated(Event):
    api_key_id: EntityId
    key_prefix: str
    owner_id: EntityId | None


class ApiKeyAuthenticated(Event):
    api_key_id: EntityId


class ApiKeyRevoked(Event):
    api_key_id: EntityId
```

- [ ] **Step 5: Update `plugins/auth/hearth_auth/events/__init__.py`**

Add the ApiKey events. Replace the file:

```python
"""Auth plugin events."""

from hearth_auth.events.api_key import (
    ApiKeyAuthenticated,
    ApiKeyCreated,
    ApiKeyRevoked,
)
from hearth_auth.events.permission import (
    PermissionAssignedToRole,
    PermissionCreated,
    PermissionRevokedFromRole,
)
from hearth_auth.events.role import (
    RoleAssignedToUser,
    RoleCreated,
    RoleRevokedFromUser,
)
from hearth_auth.events.user import (
    PasswordChanged,
    UserAuthenticated,
    UserCreated,
    UserDisabled,
    UserEnabled,
)

__all__ = [
    "ApiKeyAuthenticated",
    "ApiKeyCreated",
    "ApiKeyRevoked",
    "PasswordChanged",
    "PermissionAssignedToRole",
    "PermissionCreated",
    "PermissionRevokedFromRole",
    "RoleAssignedToUser",
    "RoleCreated",
    "RoleRevokedFromUser",
    "UserAuthenticated",
    "UserCreated",
    "UserDisabled",
    "UserEnabled",
]
```

- [ ] **Step 6: Create `plugins/auth/hearth_auth/actions/api_key.py`**

```python
"""ApiKey-lifecycle and authentication actions."""

from __future__ import annotations

import asyncio
import secrets
from datetime import UTC, datetime
from typing import Any

from pydantic import SecretStr

from hearth import EntityId, UnitOfWork
from hearth_auth._base import Action
from hearth_auth.entities.api_key import ApiKey
from hearth_auth.entities.api_key_permission import ApiKeyPermission
from hearth_auth.entities.permission import Permission
from hearth_auth.events.api_key import (
    ApiKeyAuthenticated,
    ApiKeyCreated,
    ApiKeyRevoked,
)
from hearth_auth.helpers import AuthenticationFailed, PermissionNotFound
from hearth_auth.values import HashedSecret, PermissionName

# Module-level sentinel — used for timing-constant flow when no candidate
# ApiKey row matches the prefix.
_SENTINEL_HASHED = HashedSecret.from_plaintext("sentinel-not-a-real-key")

_KEY_PREFIX_LENGTH = 8


def _generate_key_string() -> str:
    """Generate a plaintext API key: 'hak_' + 32 random url-safe chars."""
    return "hak_" + secrets.token_urlsafe(24)


class CreateApiKey(Action):
    name: str
    permissions: list[PermissionName]
    owner_id: EntityId | None = None
    expires_at: datetime | None = None

    async def handle(self, uow: UnitOfWork, actor: Any) -> tuple[ApiKey, str]:
        # Look up Permission rows for the requested names. Fail loudly on
        # any unknown name — auto-creating would mask typos.
        # Strategy: pre-filter by resource (the Query.in_() form is supported
        # natively), then filter by action in Python. Plugins must not import
        # from SQLAlchemy directly (ADR-0006); the kernel's Query API is the
        # only persistence interface plugins may touch. For v1 the Permission
        # table is small (~tens of rows in typical deployments) so the
        # in-memory filter is cheap.
        if self.permissions:
            requested = {(pn.resource, pn.action) for pn in self.permissions}
            resources = list({pn.resource for pn in self.permissions})
            candidates = await (
                uow.query(Permission)
                .where(Permission.name.resource.in_(resources))
                .all()
            )
            found = [
                p for p in candidates
                if (p.name.resource, p.name.action) in requested
            ]
            found_tuples = {(p.name.resource, p.name.action) for p in found}
            missing = [pn for pn in self.permissions
                       if (pn.resource, pn.action) not in found_tuples]
            if missing:
                raise PermissionNotFound(missing)
        else:
            found = []

        plaintext = _generate_key_string()
        prefix = plaintext[:_KEY_PREFIX_LENGTH]
        hashed = await asyncio.to_thread(HashedSecret.from_plaintext, plaintext)

        api_key = ApiKey(
            name=self.name,
            key_prefix=prefix,
            key_hash=hashed,
            owner_id=self.owner_id,
            expires_at=self.expires_at,
        )
        await uow.save(api_key)
        assert api_key.id is not None

        for perm in found:
            await uow.save(
                ApiKeyPermission(api_key_id=api_key.id, permission_id=perm.id)
            )

        uow.emit(
            ApiKeyCreated(
                api_key_id=api_key.id,
                key_prefix=prefix,
                owner_id=self.owner_id,
            )
        )
        return api_key, plaintext


class RevokeApiKey(Action):
    api_key_id: EntityId

    async def handle(self, uow: UnitOfWork, actor: Any) -> None:
        api_key = await uow.query(ApiKey).where(ApiKey.id == self.api_key_id).one()
        api_key.revoked_at = datetime.now(UTC)
        await uow.save(api_key)
        uow.emit(ApiKeyRevoked(api_key_id=api_key.id))


class AuthenticateApiKey(Action):
    key_string: SecretStr

    async def handle(self, uow: UnitOfWork, actor: Any) -> ApiKey:
        plaintext = self.key_string.get_secret_value()
        prefix = plaintext[:_KEY_PREFIX_LENGTH]
        candidates = await (
            uow.query(ApiKey).where(ApiKey.key_prefix == prefix).all()
        )

        # Always run verify at least once to equalize timing — against the
        # sentinel if no candidates exist.
        matched: ApiKey | None = None
        if not candidates:
            await asyncio.to_thread(_SENTINEL_HASHED.verify, plaintext)
        else:
            for cand in candidates:
                ok = await asyncio.to_thread(cand.key_hash.verify, plaintext)
                if ok and matched is None:
                    matched = cand
                    # Don't break — we want roughly equal time regardless of
                    # which candidate matched. (Number of candidates per prefix
                    # is bounded by how many keys share an 8-char prefix —
                    # effectively 0 or 1 in normal use.)

        if matched is None:
            raise AuthenticationFailed("invalid api key")
        if matched.revoked_at is not None:
            raise AuthenticationFailed("invalid api key")
        if matched.expires_at is not None and matched.expires_at < datetime.now(UTC):
            raise AuthenticationFailed("invalid api key")

        uow.emit(ApiKeyAuthenticated(api_key_id=matched.id))
        return matched
```

- [ ] **Step 7: Update `plugins/auth/hearth_auth/actions/__init__.py`**

```python
"""Auth plugin actions."""

from hearth_auth.actions.api_key import (
    AuthenticateApiKey,
    CreateApiKey,
    RevokeApiKey,
)
from hearth_auth.actions.permission import (
    AssignPermissionToRole,
    CreatePermission,
    RevokePermissionFromRole,
)
from hearth_auth.actions.role import (
    AssignRoleToUser,
    CreateRole,
    RevokeRoleFromUser,
)
from hearth_auth.actions.user import (
    ChangePassword,
    CreateUser,
    DisableUser,
    EnableUser,
)
from hearth_auth.actions.user_auth import AuthenticateUser

__all__ = [
    "AssignPermissionToRole",
    "AssignRoleToUser",
    "AuthenticateApiKey",
    "AuthenticateUser",
    "ChangePassword",
    "CreateApiKey",
    "CreatePermission",
    "CreateRole",
    "CreateUser",
    "DisableUser",
    "EnableUser",
    "RevokeApiKey",
    "RevokePermissionFromRole",
    "RevokeRoleFromUser",
]
```

- [ ] **Step 8: Run the tests — expect pass**

```bash
uv run pytest plugins/auth/tests/test_api_key_actions.py -v
```

Expected: 7 PASS.

---

## Task 17: `hearth auth bootstrap` CLI

**Files:**
- Create: `plugins/auth/hearth_auth/cli.py`
- Create: `plugins/auth/tests/test_bootstrap_cli.py`

- [ ] **Step 1: Write the failing test**

Create `plugins/auth/tests/test_bootstrap_cli.py`:

```python
"""Tests for `hearth auth bootstrap`."""

from __future__ import annotations

import sqlite3
from typing import Any

import pytest
from typer.testing import CliRunner

from hearth.cli import app


def _init_db(tmp_path) -> str:
    """Run `hearth db init` to materialize all auth tables. Returns the URL."""
    db_path = tmp_path / "auth_bootstrap.db"
    url = f"sqlite:///{db_path}"
    return url


def test_bootstrap_creates_admin(monkeypatch, tmp_path) -> None:
    url = _init_db(tmp_path)
    monkeypatch.setenv("DATABASE_URL", url)
    runner = CliRunner()
    result = runner.invoke(app, ["db", "init"])
    assert result.exit_code == 0

    result = runner.invoke(
        app,
        ["auth", "bootstrap", "--email", "admin@example.com"],
        input="hunter2\nhunter2\n",
    )
    assert result.exit_code == 0
    combined = result.stdout + (result.stderr or "")
    assert "admin@example.com" in combined

    # Inspect DB directly to confirm a wildcard permission + user exist.
    # Composite column names use single underscore: `email_raw`, `name_resource`,
    # `name_action` — see `_install_composite` in hearth/primitives/entity.py.
    db_file = url.replace("sqlite:///", "")
    con = sqlite3.connect(db_file)
    try:
        users = con.execute("SELECT email_raw FROM auth__user").fetchall()
        perms = con.execute("SELECT name_resource, name_action FROM auth__permission").fetchall()
    finally:
        con.close()

    assert ("admin@example.com",) in users
    assert ("*", "*") in perms


def test_bootstrap_refuses_when_wildcard_exists(monkeypatch, tmp_path) -> None:
    url = _init_db(tmp_path)
    monkeypatch.setenv("DATABASE_URL", url)
    runner = CliRunner()
    runner.invoke(app, ["db", "init"])
    runner.invoke(
        app,
        ["auth", "bootstrap", "--email", "first@example.com"],
        input="x\nx\n",
    )

    result = runner.invoke(
        app,
        ["auth", "bootstrap", "--email", "second@example.com"],
        input="y\ny\n",
    )
    assert result.exit_code != 0
    combined = result.stdout + (result.stderr or "")
    assert "already bootstrapped" in combined.lower()
```

- [ ] **Step 2: Run the test — expect failure**

```bash
uv run pytest plugins/auth/tests/test_bootstrap_cli.py -v
```

Expected: FAIL — `auth` subcommand doesn't exist yet.

- [ ] **Step 3: Create `plugins/auth/hearth_auth/cli.py`**

```python
"""hearth auth ... CLI subcommands. Registered via the `hearth.cli`
entry-point group declared in pyproject.toml."""

from __future__ import annotations

import asyncio
import os
from typing import Annotated

import typer
from sqlalchemy.ext.asyncio import async_sessionmaker

from hearth.kernel._engine import make_async_engine  # pyright: ignore[reportPrivateUsage]
from hearth.kernel.transaction import _UnitOfWork  # pyright: ignore[reportPrivateUsage]
from hearth.primitives.actor import System
from hearth_auth.actions import (
    AssignPermissionToRole,
    AssignRoleToUser,
    CreatePermission,
    CreateRole,
    CreateUser,
)
from hearth_auth.entities.permission import Permission
from hearth_auth.values import PermissionName

auth_app = typer.Typer(help="User accounts, roles, and authentication.", no_args_is_help=True)


@auth_app.callback()
def _root() -> None:
    """hearth-auth: user accounts and authentication."""


@auth_app.command("bootstrap")
def bootstrap_cmd(
    email: Annotated[str, typer.Option(help="Admin user's email.")],
    password: Annotated[
        str,
        typer.Option(
            prompt=True,
            hide_input=True,
            confirmation_prompt=True,
            help="Admin password (prompted; will be hidden and confirmed).",
        ),
    ],
) -> None:
    """Create the first admin user with the wildcard '*:*' permission.

    Refuses to run if the wildcard Permission row already exists."""
    url = os.environ.get("DATABASE_URL")
    if not url:
        typer.echo(
            "ERROR: DATABASE_URL environment variable is required for bootstrap.",
            err=True,
        )
        raise typer.Exit(1)
    asyncio.run(_bootstrap_impl(url, email, password))


async def _bootstrap_impl(url: str, email: str, password: str) -> None:
    """Run the bootstrap inside a single transaction. Uses `_UnitOfWork`
    directly (a kernel-internal); the auth plugin is built-in and ships
    in the monorepo, so this shortcut is an accepted exception until a
    public CLI-side UoW factory exists in v1.1+."""
    from hearth_commons import EmailAddress
    from pydantic import SecretStr

    engine = make_async_engine(url)
    try:
        sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
        # First short read-only transaction to check the wildcard guard.
        async with sessionmaker() as session, session.begin():
            uow_guard = _UnitOfWork(session, System())
            wildcard_exists = await (
                uow_guard.query(Permission)
                .where(Permission.name.resource == "*")
                .where(Permission.name.action == "*")
                .exists()
            )
        if wildcard_exists:
            typer.echo(
                "ERROR: hearth-auth is already bootstrapped "
                "(wildcard permission exists). Manage users via the Action surface.",
                err=True,
            )
            raise typer.Exit(1)

        # Second transaction does the create. `session.begin()` handles
        # commit on success and rollback on any exception.
        async with sessionmaker() as session, session.begin():
            uow = _UnitOfWork(session, System())
            actor = System()
            user = await CreateUser(
                email=EmailAddress(raw=email),
                password=SecretStr(password),
                display_name="Admin",
            ).handle(uow, actor)
            role = await CreateRole(
                name="admin", description="Bootstrapped administrator role."
            ).handle(uow, actor)
            wildcard = await CreatePermission(
                name=PermissionName(resource="*", action="*"),
                description="Wildcard — grants all permissions.",
            ).handle(uow, actor)
            await AssignPermissionToRole(
                role_id=role.id, permission_id=wildcard.id
            ).handle(uow, actor)
            await AssignRoleToUser(user_id=user.id, role_id=role.id).handle(uow, actor)
            await uow._flush_events()  # pyright: ignore[reportPrivateUsage]

        typer.echo("Bootstrapped admin user:")
        typer.echo(f"  email: {email}")
        typer.echo("  role:  admin (wildcard *:*)")
        typer.echo(f"  id:    {user.id}")
    finally:
        await engine.dispose()
```

- [ ] **Step 4: Run the tests — expect pass**

```bash
uv run pytest plugins/auth/tests/test_bootstrap_cli.py -v
```

Expected: 2 PASS.

```bash
uv run hearth auth --help
```

Expected: prints help for the `auth` subcommand group with `bootstrap` listed.

---

## Task 18: Test helpers (`hearth_auth.testing.seed_admin`) + cross-plugin enforcement test

**Files:**
- Create: `plugins/auth/hearth_auth/testing/__init__.py`
- Create: `plugins/auth/tests/test_cross_plugin_enforcement.py`

Note: the root `conftest.py` already provides `make_harness`. Plugin tests inherit it automatically via pytest's conftest-walking; no plugin-level conftest is needed.

- [ ] **Step 1: Create `plugins/auth/hearth_auth/testing/__init__.py`**

```python
"""Test helpers for plugins that depend on hearth-auth in their own test suites.

Plugin authors import these to seed admin users and grant permissions inside
their test fixtures, without going through the full CLI flow."""

from __future__ import annotations

from collections.abc import Iterable

from pydantic import SecretStr

from hearth.testing._base import _SqlAlchemyHarness  # pyright: ignore[reportPrivateUsage]
from hearth_auth.actions import (
    AssignPermissionToRole,
    AssignRoleToUser,
    CreatePermission,
    CreateRole,
    CreateUser,
)
from hearth_auth.entities import User
from hearth_auth.values import PermissionName
from hearth_commons import EmailAddress


async def seed_admin(
    harness: _SqlAlchemyHarness,
    email: str = "admin@test",
    password: str = "adminpass",
) -> User:
    """Programmatic equivalent of `hearth auth bootstrap`. Creates a User
    with the wildcard '*:*' permission via an 'admin' Role."""
    user = await harness.run(
        CreateUser(
            email=EmailAddress(raw=email),
            password=SecretStr(password),
            display_name="Admin",
        )
    )
    role = await harness.run(CreateRole(name="admin", description="Bootstrapped admin."))
    wildcard = await harness.run(
        CreatePermission(
            name=PermissionName(resource="*", action="*"),
            description="Wildcard — grants all permissions.",
        )
    )
    await harness.run(AssignPermissionToRole(role_id=role.id, permission_id=wildcard.id))
    await harness.run(AssignRoleToUser(user_id=user.id, role_id=role.id))
    return user


async def grant_permissions(
    harness: _SqlAlchemyHarness,
    role_id,
    permissions: Iterable[PermissionName],
) -> None:
    """Bulk-create Permission rows and attach to a Role. Test convenience."""
    for pn in permissions:
        perm = await harness.run(CreatePermission(name=pn))
        await harness.run(AssignPermissionToRole(role_id=role_id, permission_id=perm.id))
```

- [ ] **Step 2: Write the cross-plugin enforcement test**

The fake plugin is defined directly at module scope in the test file — no separate conftest, no cross-module imports.

Create `plugins/auth/tests/test_cross_plugin_enforcement.py`:

```python
"""Proves that @requires from hearth_auth correctly gates actions registered
by another plugin — i.e., the cross-plugin authorization story works.

The "other plugin" is constructed inline as a fake module with a single
@requires-decorated action; nothing is registered via entry points."""

from __future__ import annotations

import sys
import types
from collections.abc import AsyncIterator, Callable

import pytest
from pydantic import SecretStr

from hearth import bases_for
from hearth.testing._base import _SqlAlchemyHarness
from hearth_auth import PermissionDenied, requires
from hearth_auth.actions import (
    AssignPermissionToRole,
    AssignRoleToUser,
    CreatePermission,
    CreateRole,
    CreateUser,
)
from hearth_auth.entities import (
    ApiKey, ApiKeyPermission, Permission, Role, RolePermission, User, UserRole,
)
from hearth_auth.testing import seed_admin
from hearth_auth.values import PermissionName
from hearth_commons import EmailAddress

# Build a fake 'fake_orders' plugin once, at module import time. The module
# name uses the standard `hearth_test_<scenario>` convention; the action is
# attached to it so the entity-metaclass can resolve string annotations if
# any are added later.
_FAKE_MOD_NAME = "hearth_test_fake_orders_cross"
_fake_mod = types.ModuleType(_FAKE_MOD_NAME)
sys.modules[_FAKE_MOD_NAME] = _fake_mod
_, _FakeAction, _ = bases_for("fake_orders_cross")  # noqa: N806


@requires("orders:read")
class ReadOrders(_FakeAction):  # type: ignore[misc]
    async def handle(self, uow, actor):  # type: ignore[no-untyped-def]
        return "orders-data"


ReadOrders.__module__ = _FAKE_MOD_NAME
_fake_mod.ReadOrders = ReadOrders


ENTITY_LIST = [User, Role, Permission, UserRole, RolePermission, ApiKey, ApiKeyPermission]


@pytest.fixture
async def harness(
    make_harness: Callable[[], _SqlAlchemyHarness],
) -> AsyncIterator[_SqlAlchemyHarness]:
    h = make_harness()
    await h.setup(entities=ENTITY_LIST)
    try:
        await h.reset()
        yield h
    finally:
        await h.teardown()


async def test_admin_via_wildcard_can_run_cross_plugin_action(
    harness: _SqlAlchemyHarness,
) -> None:
    admin = await seed_admin(harness)
    result = await harness.run(ReadOrders(), actor=admin)
    assert result == "orders-data"


async def test_user_without_grant_cannot_run_cross_plugin_action(
    harness: _SqlAlchemyHarness,
) -> None:
    bare = await harness.run(
        CreateUser(email=EmailAddress(raw="bare@x"), password=SecretStr("p"))
    )
    with pytest.raises(PermissionDenied):
        await harness.run(ReadOrders(), actor=bare)


async def test_user_with_specific_grant_can_run_cross_plugin_action(
    harness: _SqlAlchemyHarness,
) -> None:
    user = await harness.run(
        CreateUser(email=EmailAddress(raw="reader@x"), password=SecretStr("p"))
    )
    role = await harness.run(CreateRole(name="reader"))
    perm = await harness.run(
        CreatePermission(name=PermissionName(resource="orders", action="read"))
    )
    await harness.run(AssignPermissionToRole(role_id=role.id, permission_id=perm.id))
    await harness.run(AssignRoleToUser(user_id=user.id, role_id=role.id))

    result = await harness.run(ReadOrders(), actor=user)
    assert result == "orders-data"
```

- [ ] **Step 3: Run the tests — expect pass**

```bash
uv run pytest plugins/auth/tests/test_cross_plugin_enforcement.py -v
```

Expected: 3 PASS.

---

## Task 19: Documentation — ADR-0005 amendment, `actor.md`, `auth.md`

**Files:**
- Modify: `docs/architecture/0005-primitives.md` — append an amendment section
- Create: `docs/core/primitives/actor.md`
- Create: `docs/core/plugins/auth.md`

- [ ] **Step 1: Append the amendment to `docs/architecture/0005-primitives.md`**

At the end of the file, append:

```markdown

## Amendment 2026-05-15 — `Identity` renamed to `Actor`; concrete actors live in plugins

The "Identity" primitive in the original ADR is renamed to **Actor**. The
prior `Identity(BaseModel)` hierarchy with thin `User(Identity)` /
`System(Identity)` / `ApiKey(Identity)` subclasses is replaced with:

1. **`Actor`** as a runtime-checkable `Protocol` exposing:
   - `actor_kind: ClassVar[str]` — snake_case discriminator (e.g. `"user"`,
     `"system"`, `"plugin"`, `"api_key"`).
   - `async def has_permission(uow, permission: str) -> bool` — resolution
     semantics live on the actor itself; the kernel knows about strings, not
     about how permissions are stored.

2. **Kernel-shipped dataless actors** for cases with no backing row:
   - `System` — kernel-internal callers (outbox dispatch, bootstrap).
   - `Anonymous` — public, unauthenticated endpoints.
   - `PluginActor(alias=str)` — a plugin running its own scheduled work.

3. **Plugin-contributed entity-actors** for cases with a backing row:
   - `hearth-auth` ships `User(Entity)` and `ApiKey(Entity)`, both declaring
     `actor_kind` and implementing `has_permission` against their own joins.
   - Future plugins can mint additional actor-capable entities the same way
     (e.g., a hypothetical `WhatsAppNumber(Entity)`).

**Action signature update.** `Action.handle(self, uow, identity)` becomes
`Action.handle(self, uow, actor)`. The harness API exposes the kwarg as
`actor=` instead of `identity=`.

**Outbox serialization.** The outbox `actor` JSONB column stores
`{"kind": actor.actor_kind, "id": <entity id or null>, "meta": <type-specific dict>}`.
Plugin-actor metadata (alias, future delegation chains) lives under `meta`
so the kernel doesn't need to update the schema when new actor types are
added.

**Companion specs.** See [docs/core/primitives/actor.md](../core/primitives/actor.md)
for the primitive contract and [docs/core/plugins/auth.md](../core/plugins/auth.md)
for the auth plugin's SDK surface.
```

- [ ] **Step 2: Create `docs/core/primitives/actor.md`**

```markdown
# Spec: Actor primitive

> **Source module**: `hearth/primitives/actor.py`
> **Parent ADR**: [ADR-0005 (primitives)](../../architecture/0005-primitives.md) with 2026-05-15 amendment.

## What an Actor is

The "who" of an Action — the entity or kernel-shipped identity threading
through every Action call for audit and authorization. Two kinds:

1. **Entity actors**: any `Entity` subclass that declares
   `actor_kind: ClassVar[str]` and implements
   `async def has_permission(uow, permission: str) -> bool`. Plugin-contributed
   (e.g., hearth-auth's `User` and `ApiKey`).

2. **Dataless actors**: kernel-shipped types for callers with no backing row.
   `System`, `Anonymous`, `PluginActor`. Frozen dataclasses; satisfy the
   Protocol directly.

Both shapes satisfy the `Actor` Protocol. The kernel uses the Protocol type
as the parameter to `Action.handle(uow, actor)` and as the input to outbox
audit serialization.

## Protocol surface

```python
@runtime_checkable
class Actor(Protocol):
    actor_kind: ClassVar[str]

    async def has_permission(self, uow: UnitOfWork, permission: str) -> bool:
        ...
```

**`actor_kind` convention**: snake_case, lowercase. The string is the wire
discriminator for outbox audit JSON; choose carefully — once shipped it
shouldn't be renamed.

**`has_permission`**: permission strings follow `<resource>:<action>`.
Resolution semantics are the actor's responsibility. The kernel does not
know about Role, Permission, or any plugin's authz schema.

## Kernel-shipped actors

| Type | `actor_kind` | `has_permission` | Notes |
|---|---|---|---|
| `System()` | `"system"` | always `True` | Kernel-internal callers; trusted. |
| `Anonymous()` | `"anonymous"` | always `False` | Public, unauthenticated. |
| `PluginActor(alias)` | `"plugin"` | always `True` | Plugin cron/bootstrap; trusted. |

## Outbox serialization

The outbox `actor` JSONB column stores:

```json
{"kind": "<actor_kind>", "id": "<entity id or null>", "meta": {...}}
```

`meta` is type-specific. For `PluginActor`, `meta` contains `{"alias": "..."}`.
For entity actors and kernel dataless actors, `meta` is `{}` by default.
The kernel writes this JSON; it does not reconstruct typed actors from
outbox rows. Audit consumers read the JSON directly.

## What the kernel does NOT know

- What permission strings mean.
- What concrete actor types exist beyond its three dataless ones.
- How any entity-actor resolves its permissions.

The boundary is intentional: plugins own the actor-capable entities and the
authz schema. The kernel only knows about *actorship as a capability*.
```

- [ ] **Step 3: Create `docs/core/plugins/auth.md`**

```markdown
# Plugin: hearth-auth

> **Source package**: `plugins/auth/hearth_auth/`
> **Install**: `pip install hearth[auth]`
> **Status**: v0.0.1 (first release; tracks the 2026-05-15 spec).

## What hearth-auth provides

Users with email + password authentication. Roles and Permissions for
role-based access control. ApiKeys for programmatic actors. Two enforcement
helpers (`require_permission`, `@requires`) that plugin authors use to gate
their actions. A `hearth auth bootstrap` CLI for first-admin setup.

## SDK surface

```python
from hearth_auth import (
    # Entities (when you need to query/extend)
    User, Role, Permission, UserRole, RolePermission, ApiKey, ApiKeyPermission,
    # Values
    PermissionName, HashedSecret,
    # Enforcement (the 99% case)
    require_permission, requires, PermissionDenied,
    # Authentication (transport layers, not plugin authors)
    AuthenticationFailed,
)
```

## Gating an action

```python
from hearth import Action
from hearth_auth import requires

@requires("orders:read")
class GetOrders(Action):
    async def handle(self, uow, actor):
        return await uow.query(Order).all()
```

For dynamic checks (row-level ownership, etc.), call `require_permission`
inside the body:

```python
from hearth_auth import require_permission

class GetOrder(Action):
    order_id: EntityId

    async def handle(self, uow, actor):
        order = await uow.query(Order).where(Order.id == self.order_id).one()
        if order.customer_id != getattr(actor, "id", None):
            await require_permission(uow, actor, "orders:read_any")
        return order
```

## Permission strings

Free-form `<resource>:<action>` strings. The `*:*` wildcard short-circuits
to `True` for any check on actors that hold it (used by the bootstrap
admin). Partial wildcards (`users:*`, `*:read`) are NOT supported in v0.0.1.

## Bootstrap

After `hearth db init` materializes the auth tables:

```bash
hearth auth bootstrap --email admin@example.com
# (password prompted interactively, confirmed twice)
```

Refuses to run if the wildcard Permission row already exists.

## What hearth-auth does NOT do (and why)

- **No sessions / tokens / JWT.** `AuthenticateUser` and `AuthenticateApiKey`
  return entities; the transport layer (HTTP, WhatsApp, CLI) owns session state.
- **No OAuth / OTP / magic-link / WebAuthn / MFA.** Email + password only.
  New mechanisms can be added later as parallel `AuthenticateUserViaX` actions.
- **No password reset flow.** No email sender; reset is admin-mediated via
  `ChangePassword`.
- **No login throttling / brute-force protection.** Transport layer's concern.
- **No plugin-declared permission seeding.** Permission rows are created
  via `CreatePermission`. Auto-seeding waits for the migration system.

## Testing helpers

```python
from hearth_auth.testing import seed_admin, grant_permissions
```

`seed_admin(harness)` is the programmatic equivalent of `hearth auth bootstrap`.
Use it in test fixtures. `grant_permissions(harness, role_id, [PermissionName(...)])`
is a bulk-create convenience for setting up test scenarios.
```

- [ ] **Step 4: Verify documentation files render**

```bash
ls docs/architecture/0005-primitives.md docs/core/primitives/actor.md docs/core/plugins/auth.md
```

Expected: all three exist.

```bash
grep -c "Amendment 2026-05-15" docs/architecture/0005-primitives.md
```

Expected: at least `1`.

---

## End-to-end verification (after all tasks)

```bash
# Full test suite
uv run pytest -q

# Static checks
uv run basedpyright
uv run ruff check hearth/ tests/ plugins/
uv run ruff format --check hearth/ tests/ plugins/

# CLI smoke
uv run hearth --version
uv run hearth plugins list
uv run hearth plugins show auth
uv run hearth db graph

# Auth bootstrap roundtrip (use a temp DB)
rm -f /tmp/hearth_auth_smoke.db
DATABASE_URL=sqlite:////tmp/hearth_auth_smoke.db uv run hearth db init
DATABASE_URL=sqlite:////tmp/hearth_auth_smoke.db uv run hearth auth bootstrap --email admin@example.com --password adminpass
rm -f /tmp/hearth_auth_smoke.db
```

Expected: all tests pass; pyright + ruff clean; CLI smokes exit 0; bootstrap creates the admin user.

---

## Notes for the executor

- **Test fixtures use the `make_harness` factory** (existing pattern from `conftest.py`). It returns either an in-memory SQLite or a Postgres harness based on `DATABASE_URL`.
- **`hearth_auth.testing.seed_admin` is for cross-plugin tests** that want a working admin without going through the CLI. The plugin's own tests don't need it — they construct entities directly.
- **Module-level sentinel `HashedSecret`** in `actions/user_auth.py` and `actions/api_key.py` is intentional: it hashes once at import (the only acceptable place to hash without `asyncio.to_thread`, since import is synchronous). Don't refactor it into a lazy property — it changes the timing guarantees.
- **`Permission.name.resource` and `Permission.name.action`** are composite columns mapped from the `PermissionName` Value. SQLAlchemy `Composite` semantics: the comparison `Permission.name.resource == "*"` translates to `permission.name__resource = '*'` in SQL.
- **No commits.** This plan describes code and tests; commits are out of scope.
- **`hearth.cli` entry-point discovery runs at module import time.** Tests that need to manipulate the discovered plugins use `importlib.reload(hearth.cli)` after monkeypatching `importlib.metadata.entry_points`.
