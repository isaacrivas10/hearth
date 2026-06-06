# Plugin Registry and `hearth` CLI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Do not commit anything.** This plan describes code, tests, and verification — commits are out of scope. The user authors commits separately, against the working tree, on their own cadence. Don't run `git add` or `git commit`.

**Goal:** Land the plugin Registry (entry-point discovery + validation + dependency graph) and the `hearth` CLI with seven v1 commands. Source spec: `docs/superpowers/specs/2026-05-14-registry-cli-design.md`.

**Architecture:** Kernel-internal `Registry` built once at process start from `[project.entry-points."hearth.plugins"]`. Plugin authors declare their alias once via a `_base.py` using the `bases_for(alias)` helper; entities/actions/events inherit `_hearth_plugin` from the local bases. The CLI is a Typer app with `plugins` and `db` noun groups, lazy registry build, dialect-portable engine via a shared helper.

**Tech Stack:** Python 3.13, Pydantic v2, SQLAlchemy 2.x async, Typer (new dep), `importlib.metadata` for entry-point discovery, pytest + pytest-asyncio, basedpyright strict, ruff.

---

## Pre-implementation reading

- `CLAUDE.md` — architectural commitments + commit rules.
- `docs/superpowers/specs/2026-05-14-registry-cli-design.md` — the spec this plan implements.
- `hearth/primitives/entity.py` — existing `_EntityMeta`, `Entity.__init_subclass__`, `__abstract__` guard.
- `hearth/primitives/action.py` — already has `__init_subclass__` with `plugin=` kwarg.
- `hearth/primitives/event.py` — current state (needs the `plugin=` kwarg added).
- `hearth/testing/_base.py`, `harness.py`, `postgres_harness.py` — engine setup currently split here; will be consolidated.
- `pyproject.toml` (root) — current `[project]` table.

---

## File structure

**New files:**
- `hearth/kernel/_engine.py` — `make_async_engine(url)` and `_to_async_url`.
- `hearth/kernel/registry.py` — `PluginInfo`, `Registry`, `RegistryBuildError`.
- `hearth/cli/__init__.py` — Typer app, `--version`, unhandled-exception handler, subcommand registration.
- `hearth/cli/_plugins.py` — `hearth plugins list/deps/show`.
- `hearth/cli/_db.py` — `hearth db init/status/graph`.
- `plugins/commerce/hearth_commerce/_base.py` — uses `bases_for("commerce")`.
- `tests/test_engine.py`, `tests/test_event_plugin_kwarg.py`, `tests/test_bases_for.py`, `tests/test_registry.py`, `tests/test_cli.py`.

**Modified files:**
- `pyproject.toml` (root) — add `typer>=0.12` to `dependencies`; add `[project.scripts]`.
- `plugins/commerce/pyproject.toml` — add `[project.entry-points."hearth.plugins"]`.
- `plugins/commerce/hearth_commerce/{__init__.py, entities/customer.py, actions/identify_customer.py, events/customer_identified.py}` — re-route through `_base`.
- `hearth/__init__.py` — re-export `bases_for`.
- `hearth/primitives/event.py` — add `_hearth_plugin` ClassVar + `__init_subclass__`.
- `hearth/testing/{_base.py, harness.py, postgres_harness.py}` — call `make_async_engine`.
- `docs/core/kernel/registry.md` — replace stub.

---

### Task 1: Event `plugin=` kwarg extension

**Files:**
- Modify: `hearth/primitives/event.py`
- Test: `tests/test_event_plugin_kwarg.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_event_plugin_kwarg.py`:

```python
"""Verify Event accepts `plugin=` kwarg and propagates `_hearth_plugin` via inheritance."""

from hearth import Event


def test_event_subclass_records_plugin_alias() -> None:
    class MyEvent(Event, plugin="my_plugin"):
        x: int

    assert MyEvent._hearth_plugin == "my_plugin"


def test_event_subclass_inherits_plugin_alias_through_base() -> None:
    class PluginEventBase(Event, plugin="other"):
        pass

    class ConcreteEvent(PluginEventBase):
        y: str

    assert ConcreteEvent._hearth_plugin == "other"


def test_event_without_plugin_kwarg_has_none() -> None:
    class Bare(Event):
        z: bool

    assert Bare._hearth_plugin is None
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_event_plugin_kwarg.py -v
```

Expected: the first two tests FAIL with a `TypeError` mentioning the `plugin` kwarg (exact wording varies by Python/Pydantic version). The third test fails with `AttributeError` on `_hearth_plugin`.

- [ ] **Step 3: Implement the kwarg**

Replace `hearth/primitives/event.py` entirely with:

```python
"""Event primitive — see docs/core/primitives/event.md."""

from __future__ import annotations

from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict


class Event(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    _hearth_plugin: ClassVar[str | None] = None

    def __init_subclass__(cls, *, plugin: str | None = None, **kwargs: Any) -> None:
        if plugin is not None:
            cls._hearth_plugin = plugin
        super().__init_subclass__(**kwargs)
```

- [ ] **Step 4: Run tests + static checks**

```bash
uv run pytest tests/test_event_plugin_kwarg.py -v
DATABASE_URL=postgresql://postgres:isaacrivas10@127.0.0.1:5432/hearth uv run pytest -q
uv run basedpyright
uv run ruff check hearth/ tests/ scripts/ plugins/
```

Expected: 3 PASS in the new file; full suite green; pyright + ruff clean.

---

### Task 2: `bases_for(alias)` helper

**Files:**
- Modify: `hearth/__init__.py`
- Test: `tests/test_bases_for.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_bases_for.py`:

```python
"""Verify bases_for(alias) returns Entity/Action/Event bases pre-bound to the alias,
and the equivalent longhand form behaves identically."""

from hearth import Action, Entity, Event, bases_for


def test_bases_for_returns_three_subclasses() -> None:
    E, A, Ev = bases_for("test_plugin_alpha")
    assert issubclass(E, Entity)
    assert issubclass(A, Action)
    assert issubclass(Ev, Event)


def test_bases_for_propagates_plugin_alias_via_inheritance() -> None:
    E, A, Ev = bases_for("test_plugin_beta")

    class MyCust(E):
        name: str

    class MyCreate(A):
        name: str

    class MyCreated(Ev):
        name: str

    assert MyCust._hearth_plugin == "test_plugin_beta"
    assert MyCreate._hearth_plugin == "test_plugin_beta"
    assert MyCreated._hearth_plugin == "test_plugin_beta"


def test_bases_for_entity_base_is_abstract() -> None:
    E, _, _ = bases_for("test_plugin_gamma")
    assert E.__dict__.get("__abstract__") is True


def test_bases_for_is_cached_by_alias() -> None:
    first = bases_for("test_plugin_delta")
    second = bases_for("test_plugin_delta")
    assert first[0] is second[0]
    assert first[1] is second[1]
    assert first[2] is second[2]


def test_bases_for_distinct_aliases_yield_distinct_bases() -> None:
    e1, _, _ = bases_for("test_plugin_epsilon_1")
    e2, _, _ = bases_for("test_plugin_epsilon_2")
    assert e1 is not e2
    assert e1._hearth_plugin == "test_plugin_epsilon_1"
    assert e2._hearth_plugin == "test_plugin_epsilon_2"


def test_longhand_form_propagates_plugin_alias_identically() -> None:
    """The spec promises both `bases_for(alias)` and the longhand form work."""

    class LonghandEntity(Entity, plugin="test_longhand"):
        __abstract__ = True

    class LonghandAction(Action, plugin="test_longhand"):
        pass

    class LonghandEvent(Event, plugin="test_longhand"):
        pass

    class MyEnt(LonghandEntity):
        n: int

    class MyAct(LonghandAction):
        pass

    class MyEv(LonghandEvent):
        pass

    assert MyEnt._hearth_plugin == "test_longhand"
    assert MyAct._hearth_plugin == "test_longhand"
    assert MyEv._hearth_plugin == "test_longhand"
    assert LonghandEntity.__dict__.get("__abstract__") is True
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_bases_for.py -v
```

Expected: `ImportError: cannot import name 'bases_for' from 'hearth'`.

- [ ] **Step 3: Add `bases_for` to `hearth/__init__.py`**

Add the import and function definition (placement: after existing primitive imports, before `__all__`):

```python
from functools import lru_cache


@lru_cache(maxsize=None)
def bases_for(alias: str) -> tuple[type[Entity], type[Action], type[Event]]:
    """Return Entity/Action/Event base classes pre-bound to the plugin alias."""

    class _PluginEntity(Entity, plugin=alias):
        __abstract__ = True

    class _PluginAction(Action, plugin=alias):
        pass

    class _PluginEvent(Event, plugin=alias):
        pass

    return _PluginEntity, _PluginAction, _PluginEvent
```

Add `"bases_for"` to `__all__` (alphabetical: after `"agg"`).

- [ ] **Step 4: Run tests + static checks**

```bash
uv run pytest tests/test_bases_for.py -v
uv run basedpyright
uv run ruff check hearth/ tests/
```

Expected: 6 PASS, pyright + ruff clean.

---

### Task 3: Engine helper extraction

**Files:**
- Create: `hearth/kernel/_engine.py`
- Modify: `hearth/testing/{_base.py, harness.py, postgres_harness.py}`
- Test: `tests/test_engine.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_engine.py`:

```python
"""Verify make_async_engine produces a usable async engine on both dialects."""

import os
from pathlib import Path

import pytest
from sqlalchemy import text

from hearth.kernel._engine import _to_async_url, make_async_engine  # pyright: ignore[reportPrivateUsage]


def test_to_async_url_postgresql_plain() -> None:
    assert _to_async_url("postgresql://u:p@h/db") == "postgresql+asyncpg://u:p@h/db"


def test_to_async_url_postgresql_already_async() -> None:
    assert _to_async_url("postgresql+asyncpg://u:p@h/db") == "postgresql+asyncpg://u:p@h/db"


def test_to_async_url_postgres_alias() -> None:
    assert _to_async_url("postgres://u:p@h/db") == "postgresql+asyncpg://u:p@h/db"


def test_to_async_url_sqlite_passes_through() -> None:
    assert _to_async_url("sqlite:///foo.db") == "sqlite+aiosqlite:///foo.db"


def test_to_async_url_sqlite_already_async() -> None:
    assert _to_async_url("sqlite+aiosqlite:///foo.db") == "sqlite+aiosqlite:///foo.db"


async def test_sqlite_engine_executes_simple_query(tmp_path: Path) -> None:
    db_file = tmp_path / "hearth_test.db"
    engine = make_async_engine(f"sqlite:///{db_file}")
    try:
        async with engine.connect() as conn:
            result = await conn.execute(text("SELECT 1 AS n"))
            assert result.scalar_one() == 1
    finally:
        await engine.dispose()


async def test_sqlite_engine_enables_foreign_keys(tmp_path: Path) -> None:
    db_file = tmp_path / "fk_test.db"
    engine = make_async_engine(f"sqlite:///{db_file}")
    try:
        async with engine.connect() as conn:
            result = await conn.execute(text("PRAGMA foreign_keys"))
            assert result.scalar_one() == 1
    finally:
        await engine.dispose()


@pytest.mark.skipif("DATABASE_URL" not in os.environ, reason="DATABASE_URL not set")
async def test_postgres_engine_executes_simple_query() -> None:
    engine = make_async_engine(os.environ["DATABASE_URL"])
    try:
        async with engine.connect() as conn:
            result = await conn.execute(text("SELECT 1 AS n"))
            assert result.scalar_one() == 1
    finally:
        await engine.dispose()
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_engine.py -v
```

Expected: `ImportError: cannot import name '_to_async_url' from 'hearth.kernel._engine'`.

- [ ] **Step 3: Implement `hearth/kernel/_engine.py`**

```python
"""Dialect-aware async engine factory — shared by the CLI, the in-memory Harness,
and PostgresHarness. Centralises the SQLite-specific quirks (StaticPool, FK pragma)
so they don't drift across consumers."""

from __future__ import annotations

from typing import Any

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import StaticPool


def _to_async_url(url: str) -> str:
    if url.startswith("postgresql+"):
        return url
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+asyncpg://", 1)
    if url.startswith("sqlite+"):
        return url
    if url.startswith("sqlite://"):
        return url.replace("sqlite://", "sqlite+aiosqlite://", 1)
    return url


def make_async_engine(url: str) -> AsyncEngine:
    """Build an AsyncEngine. For SQLite: StaticPool + check_same_thread=False so
    multiple async sessions share one underlying connection (required for :memory:);
    plus a connect listener that enables `PRAGMA foreign_keys=ON` (off by default
    in SQLite)."""
    async_url = _to_async_url(url)
    kwargs: dict[str, Any] = {}
    if async_url.startswith("sqlite+"):
        kwargs["poolclass"] = StaticPool
        kwargs["connect_args"] = {"check_same_thread": False}
    engine = create_async_engine(async_url, **kwargs)
    if async_url.startswith("sqlite+"):

        @event.listens_for(engine.sync_engine, "connect")
        def _enable_fk(dbapi_conn: Any, _: Any) -> None:  # pyright: ignore[reportUnusedFunction]
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine
```

- [ ] **Step 4: Refactor harnesses**

Replace `hearth/testing/postgres_harness.py`:

```python
"""Postgres-backed test harness — see docs/core/testing/harness.md."""

from __future__ import annotations

from hearth.kernel._engine import make_async_engine  # pyright: ignore[reportPrivateUsage]
from hearth.testing._base import _SqlAlchemyHarness  # pyright: ignore[reportPrivateUsage]


class PostgresHarness(_SqlAlchemyHarness):
    """Test harness backed by a real Postgres instance via DATABASE_URL."""

    def __init__(self, database_url: str) -> None:
        super().__init__(make_async_engine(database_url))
```

In `hearth/testing/harness.py`, find the SQLite engine-construction block in `Harness.__init__` (StaticPool / connect_args / `_enable_fk` listener) and replace with:

```python
        engine = make_async_engine("sqlite:///:memory:")
        super().__init__(engine)
```

Add `from hearth.kernel._engine import make_async_engine  # pyright: ignore[reportPrivateUsage]` to the imports of `harness.py`. Remove unused `event`, `create_async_engine`, `StaticPool`. `_base.py` should need no changes — verify.

- [ ] **Step 5: Run tests + static checks**

```bash
uv run pytest tests/test_engine.py -v
DATABASE_URL=postgresql://postgres:isaacrivas10@127.0.0.1:5432/hearth uv run pytest -q
uv run basedpyright
uv run ruff check hearth/ tests/
```

Expected: 8 PASS in test_engine.py (7 without Postgres); full suite same count as before refactor; pyright + ruff clean.

---

### Task 4: `PluginInfo`, `RegistryBuildError`, `Registry` skeleton + Phase 1 import

**Files:**
- Create: `hearth/kernel/registry.py`
- Test: `tests/test_registry.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_registry.py`:

```python
"""Tests for the plugin Registry."""

from __future__ import annotations

import sys
import types
from typing import Any

import pytest

from hearth import Entity, EntityId, ForeignKey, References, bases_for
from hearth.kernel.registry import (
    PluginInfo,
    Registry,
    RegistryBuildError,
)
from hearth.kernel.registry import _ModuleSpec  # noqa: E402  pyright: ignore[reportPrivateUsage]


def _fake_module(name: str) -> types.ModuleType:
    mod = types.ModuleType(name)
    sys.modules[name] = mod
    return mod


def _attach_to_module(mod: types.ModuleType, cls: type) -> None:
    cls.__module__ = mod.__name__
    setattr(mod, cls.__name__, cls)


def test_plugin_info_construction() -> None:
    info = PluginInfo(
        alias="commerce",
        package="hearth-commerce",
        version="0.0.1",
        module="hearth_commerce",
        install_path="/tmp/fake",
        depends_on=[],
        extends=[],
        entities=[],
        actions=[],
        events=[],
    )
    assert info.alias == "commerce"


def test_registry_from_modules_with_no_plugins_returns_empty() -> None:
    registry = Registry.from_modules({})
    assert registry.plugins == {}
    assert registry.topological_order() == []


def test_registry_build_error_carries_issue_list() -> None:
    err = RegistryBuildError(["import failed: foo", "alias mismatch: bar"])
    assert "import failed: foo" in str(err)
    assert "alias mismatch: bar" in str(err)


def test_registry_from_modules_indexes_entities() -> None:
    mod = _fake_module("hearth_test_indexing")
    EntityBase, _, _ = bases_for("test_indexing")

    class TestCust(EntityBase):
        name: str

    _attach_to_module(mod, TestCust)

    registry = Registry.from_modules(
        {"test_indexing": _ModuleSpec(module=mod, package="hearth-test", version="0.0.1", requires=())},
    )
    assert TestCust in registry.get("test_indexing").entities
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_registry.py -v
```

Expected: `ImportError: cannot import name 'PluginInfo' from 'hearth.kernel.registry'`.

- [ ] **Step 3: Implement skeleton**

Create `hearth/kernel/registry.py`:

```python
"""Plugin registry — startup-built, in-memory record of installed plugins.

Built once per process from `[project.entry-points."hearth.plugins"]`. Read-only
after construction. Plugins never import this module."""

from __future__ import annotations

import importlib
import importlib.metadata
from dataclasses import dataclass, field
from types import ModuleType
from typing import Any

from hearth.primitives.action import Action
from hearth.primitives.entity import Entity
from hearth.primitives.event import Event


@dataclass(frozen=True)
class PluginInfo:
    """All registry knowledge of one installed plugin."""

    alias: str
    package: str
    version: str
    module: str
    install_path: str
    depends_on: list[str] = field(default_factory=list)
    extends: list[str] = field(default_factory=list)
    entities: list[type[Entity]] = field(default_factory=list)
    actions: list[type[Action]] = field(default_factory=list)
    events: list[type[Event]] = field(default_factory=list)


class RegistryBuildError(Exception):
    """Raised when Registry.build() finds problems with the installed plugin set."""

    def __init__(self, issues: list[str]) -> None:
        self.issues = issues
        super().__init__("\n".join(["plugin registry build failed:", *(f"  - {i}" for i in issues)]))


@dataclass
class _ModuleSpec:
    """Per-plugin info bundled by Registry.build() and passed to from_modules.
    Tests construct this directly to bypass entry-point discovery."""

    module: ModuleType
    package: str
    version: str
    requires: tuple[str, ...]


@dataclass
class Registry:
    """In-memory plugin registry. Built once at process start; read-only."""

    plugins: dict[str, PluginInfo] = field(default_factory=dict)
    _topological_order: list[str] = field(default_factory=list)

    @classmethod
    def from_modules(cls, specs: dict[str, _ModuleSpec]) -> "Registry":
        """Build a Registry from pre-imported plugin modules.

        Phase 1 (indexing) only at this point; Phases 2 and 3 are added in
        later tasks."""
        plugins: dict[str, PluginInfo] = {}
        for alias, spec in specs.items():
            top_package = spec.module.__name__
            entities = _classes_from_module_tree(Entity, top_package)
            actions = _classes_from_module_tree(Action, top_package)
            events = _classes_from_module_tree(Event, top_package)
            plugins[alias] = PluginInfo(
                alias=alias,
                package=spec.package,
                version=spec.version,
                module=top_package,
                install_path=getattr(spec.module, "__file__", "<unknown>") or "<unknown>",
                entities=entities,
                actions=actions,
                events=events,
            )
        return cls(plugins=plugins, _topological_order=sorted(plugins))

    def get(self, alias: str) -> PluginInfo:
        if alias not in self.plugins:
            raise KeyError(f"no plugin named {alias!r}; installed plugins: {sorted(self.plugins)}")
        return self.plugins[alias]

    def topological_order(self) -> list[str]:
        return list(self._topological_order)

    def entities_for(self, alias: str) -> list[type[Entity]]:
        return self.get(alias).entities


def _classes_from_module_tree(base: type, top_package: str) -> list[type]:
    """Walk base.__subclasses__() recursively. Return classes whose __module__
    is the given top-level package or a submodule of it, skipping abstract bases."""
    found: list[type] = []
    seen: set[type] = set()
    stack: list[type] = list(base.__subclasses__())
    while stack:
        cls = stack.pop()
        if cls in seen:
            continue
        seen.add(cls)
        stack.extend(cls.__subclasses__())
        mod = getattr(cls, "__module__", "") or ""
        if mod == top_package or mod.startswith(top_package + "."):
            if cls.__dict__.get("__abstract__", False):
                continue
            found.append(cls)
    return found
```

- [ ] **Step 4: Run tests + static checks**

```bash
uv run pytest tests/test_registry.py -v
uv run basedpyright
uv run ruff check hearth/ tests/
```

Expected: 4 PASS, pyright + ruff clean.

---

### Task 5: Registry Phase 2 — alias/kwarg consistency validation

**Files:**
- Modify: `hearth/kernel/registry.py`
- Modify: `tests/test_registry.py`

- [ ] **Step 1: Append the failing tests**

```python
def test_phase2_rejects_class_with_mismatched_plugin_alias() -> None:
    mod = _fake_module("hearth_test_mismatch")
    EBase, _, _ = bases_for("WRONG_ALIAS")

    class Cust(EBase):
        name: str

    _attach_to_module(mod, Cust)

    with pytest.raises(RegistryBuildError) as exc:
        Registry.from_modules(
            {"correct_alias": _ModuleSpec(module=mod, package="x", version="0.0.1", requires=())},
        )
    assert "Cust" in str(exc.value)
    assert "WRONG_ALIAS" in str(exc.value)
    assert "correct_alias" in str(exc.value)


def test_phase2_rejects_cross_package_namespace_violation() -> None:
    mod_a = _fake_module("hearth_test_pkg_a")
    mod_b = _fake_module("hearth_test_pkg_b")
    EBaseB, _, _ = bases_for("pkg_b_alias")

    class Bad(EBaseB):
        name: str

    _attach_to_module(mod_a, Bad)

    with pytest.raises(RegistryBuildError) as exc:
        Registry.from_modules(
            {
                "pkg_a_alias": _ModuleSpec(module=mod_a, package="a", version="0", requires=()),
                "pkg_b_alias": _ModuleSpec(module=mod_b, package="b", version="0", requires=()),
            },
        )
    assert "Bad" in str(exc.value)


def test_phase2_collects_multiple_errors_before_raising() -> None:
    mod = _fake_module("hearth_test_multi_err")
    EBase, ABase, _ = bases_for("not_the_real_alias")

    class C1(EBase):
        n: int

    class A1(ABase):
        pass

    for c in (C1, A1):
        _attach_to_module(mod, c)

    with pytest.raises(RegistryBuildError) as exc:
        Registry.from_modules(
            {"real_alias": _ModuleSpec(module=mod, package="x", version="0", requires=())},
        )
    msg = str(exc.value)
    assert "C1" in msg
    assert "A1" in msg


def test_phase2_accepts_correctly_tagged_classes() -> None:
    mod = _fake_module("hearth_test_happy")
    EBase, _, _ = bases_for("happy_alias")

    class Customer(EBase):
        name: str

    _attach_to_module(mod, Customer)

    registry = Registry.from_modules(
        {"happy_alias": _ModuleSpec(module=mod, package="x", version="0", requires=())},
    )
    assert Customer in registry.get("happy_alias").entities
```

- [ ] **Step 2: Run tests to verify they fail**

Expected: the four new tests FAIL — the first three would currently SUCCEED building without raising.

- [ ] **Step 3: Implement Phase 2**

Replace `Registry.from_modules` body in `hearth/kernel/registry.py`:

```python
    @classmethod
    def from_modules(cls, specs: dict[str, _ModuleSpec]) -> "Registry":
        # ----- Phase 2: alias/kwarg consistency -----
        errors: list[str] = []
        for alias, spec in specs.items():
            top_package = spec.module.__name__
            for base in (Entity, Action, Event):
                for cls_obj in _classes_from_module_tree(base, top_package):
                    actual = getattr(cls_obj, "_hearth_plugin", None)
                    if actual != alias:
                        errors.append(
                            f"{cls_obj.__module__}.{cls_obj.__name__}: declares plugin="
                            f"{actual!r} but ships in package owned by plugin alias {alias!r}",
                        )
        if errors:
            raise RegistryBuildError(errors)

        # ----- Phase 1: indexing -----
        plugins: dict[str, PluginInfo] = {}
        for alias, spec in specs.items():
            top_package = spec.module.__name__
            entities = _classes_from_module_tree(Entity, top_package)
            actions = _classes_from_module_tree(Action, top_package)
            events = _classes_from_module_tree(Event, top_package)
            plugins[alias] = PluginInfo(
                alias=alias,
                package=spec.package,
                version=spec.version,
                module=top_package,
                install_path=getattr(spec.module, "__file__", "<unknown>") or "<unknown>",
                entities=entities,
                actions=actions,
                events=events,
            )
        return cls(plugins=plugins, _topological_order=sorted(plugins))
```

- [ ] **Step 4: Run tests + static checks**

Expected: 8 registry tests PASS.

---

### Task 6: Registry Phase 3 — `depends_on`, `extends`, cycle detection, invariant

**Files:**
- Modify: `hearth/kernel/registry.py`
- Modify: `tests/test_registry.py`

- [ ] **Step 1: Append the failing tests**

```python
def test_phase3_depends_on_from_requires_dist() -> None:
    mod_a = _fake_module("hearth_test_dep_a")
    mod_b = _fake_module("hearth_test_dep_b")
    _ = bases_for("dep_alias_a")
    _ = bases_for("dep_alias_b")

    registry = Registry.from_modules(
        {
            "dep_alias_a": _ModuleSpec(module=mod_a, package="pkg-a", version="0", requires=()),
            "dep_alias_b": _ModuleSpec(
                module=mod_b, package="pkg-b", version="0", requires=("pkg-a",),
            ),
        },
    )
    assert registry.get("dep_alias_b").depends_on == ["dep_alias_a"]
    assert registry.get("dep_alias_a").depends_on == []


def test_phase3_topological_order_respects_deps() -> None:
    mod_a = _fake_module("hearth_test_topo_a")
    mod_b = _fake_module("hearth_test_topo_b")
    mod_c = _fake_module("hearth_test_topo_c")

    registry = Registry.from_modules(
        {
            "topo_c": _ModuleSpec(module=mod_c, package="pkg-c", version="0", requires=("pkg-b",)),
            "topo_b": _ModuleSpec(module=mod_b, package="pkg-b", version="0", requires=("pkg-a",)),
            "topo_a": _ModuleSpec(module=mod_a, package="pkg-a", version="0", requires=()),
        },
    )
    order = registry.topological_order()
    assert order.index("topo_a") < order.index("topo_b") < order.index("topo_c")


def test_phase3_detects_cycles() -> None:
    mod_a = _fake_module("hearth_test_cycle_a")
    mod_b = _fake_module("hearth_test_cycle_b")

    with pytest.raises(RegistryBuildError) as exc:
        Registry.from_modules(
            {
                "cy_a": _ModuleSpec(module=mod_a, package="pkg-a", version="0", requires=("pkg-b",)),
                "cy_b": _ModuleSpec(module=mod_b, package="pkg-b", version="0", requires=("pkg-a",)),
            },
        )
    assert "cycle" in str(exc.value).lower()


def test_phase3_extends_derived_from_references() -> None:
    mod_target = _fake_module("hearth_test_ext_target")
    EBaseT, _, _ = bases_for("ext_target")

    class Customer(EBaseT):
        name: str

    _attach_to_module(mod_target, Customer)

    mod_extender = _fake_module("hearth_test_ext_extender")
    EBaseE, _, _ = bases_for("ext_extender")

    class Order(EBaseE):
        customer_id: EntityId = ForeignKey()
        customer: Customer = References(Customer)

    _attach_to_module(mod_extender, Order)

    registry = Registry.from_modules(
        {
            "ext_target": _ModuleSpec(
                module=mod_target, package="pkg-target", version="0", requires=(),
            ),
            "ext_extender": _ModuleSpec(
                module=mod_extender, package="pkg-extender", version="0",
                requires=("pkg-target",),
            ),
        },
    )
    assert registry.get("ext_extender").extends == ["ext_target"]
    assert registry.get("ext_target").extends == []


def test_phase3_invariant_extends_subset_of_depends_on() -> None:
    mod_target = _fake_module("hearth_test_inv_target")
    EBaseT, _, _ = bases_for("inv_target")

    class Cust(EBaseT):
        name: str

    _attach_to_module(mod_target, Cust)

    mod_extender = _fake_module("hearth_test_inv_extender")
    EBaseE, _, _ = bases_for("inv_extender")

    class Ord(EBaseE):
        customer_id: EntityId = ForeignKey()
        customer: Cust = References(Cust)

    _attach_to_module(mod_extender, Ord)

    with pytest.raises(RegistryBuildError) as exc:
        Registry.from_modules(
            {
                "inv_target": _ModuleSpec(
                    module=mod_target, package="pkg-target", version="0", requires=(),
                ),
                "inv_extender": _ModuleSpec(
                    module=mod_extender, package="pkg-extender", version="0",
                    requires=(),  # missing pkg-target as Python dep
                ),
            },
        )
    msg = str(exc.value).lower()
    assert "inv_extender" in msg
    assert "inv_target" in msg
```

- [ ] **Step 2: Run tests to verify they fail**

Expected: 5 new tests FAIL.

- [ ] **Step 3: Implement Phase 3**

Replace `Registry.from_modules` body with the full three-phase version:

```python
    @classmethod
    def from_modules(cls, specs: dict[str, _ModuleSpec]) -> "Registry":
        # ----- Phase 2: alias/kwarg consistency -----
        errors: list[str] = []
        for alias, spec in specs.items():
            top_package = spec.module.__name__
            for base in (Entity, Action, Event):
                for cls_obj in _classes_from_module_tree(base, top_package):
                    actual = getattr(cls_obj, "_hearth_plugin", None)
                    if actual != alias:
                        errors.append(
                            f"{cls_obj.__module__}.{cls_obj.__name__}: declares plugin="
                            f"{actual!r} but ships in package owned by plugin alias {alias!r}",
                        )
        if errors:
            raise RegistryBuildError(errors)

        # ----- Phase 1: indexing -----
        package_to_alias = {spec.package: alias for alias, spec in specs.items()}
        plugins: dict[str, PluginInfo] = {}
        for alias, spec in specs.items():
            top_package = spec.module.__name__
            entities = _classes_from_module_tree(Entity, top_package)
            actions = _classes_from_module_tree(Action, top_package)
            events = _classes_from_module_tree(Event, top_package)
            plugins[alias] = PluginInfo(
                alias=alias,
                package=spec.package,
                version=spec.version,
                module=top_package,
                install_path=getattr(spec.module, "__file__", "<unknown>") or "<unknown>",
                entities=entities,
                actions=actions,
                events=events,
            )

        # ----- Phase 3a: depends_on from Requires-Dist -----
        for alias, spec in specs.items():
            deps: list[str] = []
            for raw in spec.requires:
                pkg = _extract_package_name(raw)
                if pkg in package_to_alias and package_to_alias[pkg] != alias:
                    deps.append(package_to_alias[pkg])
            plugins[alias] = _replace(plugins[alias], depends_on=sorted(set(deps)))

        # ----- Phase 3b: extends from FK introspection -----
        tablename_to_alias: dict[str, str] = {}
        for alias, info in plugins.items():
            for ent in info.entities:
                tbl = getattr(ent, "__tablename__", None)
                if tbl:
                    tablename_to_alias[tbl] = alias
        for alias, info in plugins.items():
            ext_set: set[str] = set()
            for ent in info.entities:
                table = getattr(ent, "__table__", None)
                if table is None:
                    continue
                for col in table.columns:
                    for fk in col.foreign_keys:
                        target_alias = tablename_to_alias.get(fk.column.table.name)
                        if target_alias and target_alias != alias:
                            ext_set.add(target_alias)
            plugins[alias] = _replace(info, extends=sorted(ext_set))

        # ----- Phase 3c: invariant — extends ⊆ depends_on -----
        invariant_errors: list[str] = []
        for alias, info in plugins.items():
            missing = sorted(set(info.extends) - set(info.depends_on))
            for tgt in missing:
                target_package = plugins[tgt].package
                invariant_errors.append(
                    f"{alias}: has foreign keys into plugin {tgt!r} but does not declare "
                    f"{target_package!r} as a Python dependency in pyproject.toml",
                )
        if invariant_errors:
            raise RegistryBuildError(invariant_errors)

        # ----- Phase 3d: cycle detection via topological sort -----
        order = _topological_sort({a: list(p.depends_on) for a, p in plugins.items()})
        if order is None:
            raise RegistryBuildError(
                [f"cycle detected in plugin dependency graph among: {sorted(plugins)}"],
            )

        return cls(plugins=plugins, _topological_order=order)
```

Append helpers at the bottom of `hearth/kernel/registry.py`:

```python
def _extract_package_name(requires_entry: str) -> str:
    """Given a Requires-Dist string like 'hearth-commons>=0.0.1; extra == "x"',
    return the bare package name."""
    name = requires_entry
    for sep in (" ", "[", ";", "<", ">", "=", "!", "~"):
        idx = name.find(sep)
        if idx != -1:
            name = name[:idx]
    return name.strip()


def _topological_sort(graph: dict[str, list[str]]) -> list[str] | None:
    """Kahn's algorithm. `graph[a]` is the list of deps that `a` depends on
    (incoming edges into `a`). Returns nodes in dependency order — deps
    before dependents — or None if a cycle is detected.

    Indegree of `a` = how many things `a` waits on = `len(graph[a])`.
    Reverse-edge map: for each `(a, dep)`, dep gains a dependent `a`.
    """
    indegree: dict[str, int] = {n: len(graph[n]) for n in graph}
    dependents: dict[str, list[str]] = {n: [] for n in graph}
    for node, deps in graph.items():
        for dep in deps:
            dependents.setdefault(dep, []).append(node)

    queue = [n for n, d in indegree.items() if d == 0]
    out: list[str] = []
    while queue:
        n = queue.pop(0)
        out.append(n)
        for dependent in dependents.get(n, []):
            indegree[dependent] -= 1
            if indegree[dependent] == 0:
                queue.append(dependent)

    if len(out) != len(graph):
        return None
    return out


def _replace(info: PluginInfo, **changes: Any) -> PluginInfo:
    """Return a new PluginInfo with the given fields replaced (PluginInfo is frozen)."""
    import dataclasses
    return dataclasses.replace(info, **changes)
```

- [ ] **Step 4: Run tests + static checks**

Expected: 13 registry tests PASS.

---

### Task 7: `Registry.build()` — entry-point discovery wrapper

**Files:**
- Modify: `hearth/kernel/registry.py`
- Modify: `tests/test_registry.py`

- [ ] **Step 1: Append the failing tests**

```python
def test_registry_build_with_no_entry_points(monkeypatch: pytest.MonkeyPatch) -> None:
    import hearth.kernel.registry as reg_mod

    monkeypatch.setattr(reg_mod.importlib.metadata, "entry_points", lambda **kw: [])
    registry = Registry.build()
    assert registry.plugins == {}


def test_registry_build_imports_entry_point_modules(monkeypatch: pytest.MonkeyPatch) -> None:
    import hearth.kernel.registry as reg_mod

    mod = _fake_module("hearth_test_buildep")
    EBase, _, _ = bases_for("buildep")

    class Cust(EBase):
        name: str

    _attach_to_module(mod, Cust)

    class FakeEP:
        name = "buildep"
        value = "hearth_test_buildep"
        dist = type("FakeDist", (), {
            "name": "pkg-buildep",
            "version": "0.0.1",
            "requires": (),
        })()

    monkeypatch.setattr(
        reg_mod.importlib.metadata,
        "entry_points",
        lambda **kw: [FakeEP()] if kw.get("group") == "hearth.plugins" else [],
    )

    registry = Registry.build()
    assert "buildep" in registry.plugins
    assert Cust in registry.get("buildep").entities


def test_registry_build_surfaces_import_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    import hearth.kernel.registry as reg_mod

    class FakeEP:
        name = "broken"
        value = "nonexistent_module_xyz"
        dist = type("FakeDist", (), {"name": "pkg-broken", "version": "0", "requires": ()})()

    monkeypatch.setattr(
        reg_mod.importlib.metadata,
        "entry_points",
        lambda **kw: [FakeEP()] if kw.get("group") == "hearth.plugins" else [],
    )

    with pytest.raises(RegistryBuildError) as exc:
        Registry.build()
    assert "broken" in str(exc.value)
    assert "import failed" in str(exc.value).lower() or "modulenotfound" in str(exc.value).lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Expected: `Registry.build` doesn't exist or returns the wrong shape.

- [ ] **Step 3: Implement `Registry.build`**

Append to `Registry` class:

```python
    @classmethod
    def build(cls) -> "Registry":
        """Discover `hearth.plugins` entry points, import each, and validate."""
        eps = list(importlib.metadata.entry_points(group="hearth.plugins"))
        import_errors: list[str] = []
        specs: dict[str, _ModuleSpec] = {}
        for ep in eps:
            try:
                mod = importlib.import_module(ep.value)
            except Exception as exc:
                import_errors.append(f"{ep.name}: import failed — {type(exc).__name__}: {exc}")
                continue
            dist = ep.dist
            requires = tuple(dist.requires or ())
            specs[ep.name] = _ModuleSpec(
                module=mod,
                package=dist.name,
                version=dist.version,
                requires=requires,
            )
        if import_errors:
            raise RegistryBuildError(import_errors)
        return cls.from_modules(specs)
```

- [ ] **Step 4: Run tests + static checks**

Expected: 16 registry tests PASS.

---

### Task 8: `pyproject.toml` — add `typer` dep and `hearth` script entry

**Files:**
- Modify: `pyproject.toml` (root)

- [ ] **Step 1: Survey current state**

```bash
grep -nE "dependencies|scripts" pyproject.toml | head
```

- [ ] **Step 2: Edit `pyproject.toml`**

Add `"typer>=0.12"` to the `dependencies` array under `[project]`. Add `[project.scripts]`:

```toml
[project.scripts]
hearth = "hearth.cli:app"
```

- [ ] **Step 3: Sync workspace**

```bash
uv sync --dev
```

- [ ] **Step 4: Verify**

```bash
uv run python -c "import typer; print(typer.__version__)"
```

Expected: typer version printed.

---

### Task 9: Typer app scaffold + `--version` + unhandled-exception handler

**Files:**
- Create: `hearth/cli/__init__.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_cli.py`:

```python
"""Tests for the hearth CLI."""

from __future__ import annotations

import typer
from typer.testing import CliRunner

from hearth.cli import app


def test_version_flag_prints_kernel_version() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "hearth" in result.stdout.lower()


def test_no_args_prints_help() -> None:
    runner = CliRunner()
    result = runner.invoke(app, [])
    assert result.exit_code == 0
    assert "Usage" in result.stdout


def test_unexpected_exception_renders_clean_error_without_debug(monkeypatch) -> None:
    from hearth.cli import _wrap_command  # pyright: ignore[reportPrivateUsage]

    test_app = typer.Typer()

    @test_app.command()
    @_wrap_command
    def boom() -> None:
        raise RuntimeError("ouchies")

    monkeypatch.delenv("HEARTH_DEBUG", raising=False)
    runner = CliRunner()
    result = runner.invoke(test_app, ["boom"], catch_exceptions=True)
    assert result.exit_code == 1
    combined = result.stdout + (result.stderr or "")
    assert "ERROR: unexpected" in combined
    assert "RuntimeError" in combined
    assert "ouchies" in combined


def test_unexpected_exception_reraises_with_debug_env(monkeypatch) -> None:
    from hearth.cli import _wrap_command  # pyright: ignore[reportPrivateUsage]

    test_app = typer.Typer()

    @test_app.command()
    @_wrap_command
    def boom_debug() -> None:
        raise RuntimeError("ouchies-debug")

    monkeypatch.setenv("HEARTH_DEBUG", "1")
    runner = CliRunner()
    result = runner.invoke(test_app, ["boom-debug"], catch_exceptions=True)
    assert result.exit_code != 0
    assert result.exception is not None
    assert isinstance(result.exception, RuntimeError)
```

- [ ] **Step 2: Run test to verify it fails**

Expected: `ImportError: cannot import name 'app' from 'hearth.cli'`.

- [ ] **Step 3: Create `hearth/cli/__init__.py`**

```python
"""The `hearth` command-line interface."""

from __future__ import annotations

import functools
import os
import sys
from importlib.metadata import version as _pkg_version
from typing import Annotated, Any, Callable

import typer

from hearth.kernel.registry import RegistryBuildError

app = typer.Typer(
    no_args_is_help=True,
    help="Hearth kernel command-line interface.",
    add_completion=False,
)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"hearth {_pkg_version('hearth')}")
        raise typer.Exit()


@app.callback()
def main(
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            callback=_version_callback,
            is_eager=True,
            help="Print the kernel version and exit.",
        ),
    ] = False,
) -> None:
    """Hearth kernel CLI."""


def _wrap_command(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Wrap a Typer command body so unhandled exceptions render cleanly."""

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return fn(*args, **kwargs)
        except (RegistryBuildError, typer.BadParameter, typer.Exit):
            raise
        except Exception as exc:
            if os.environ.get("HEARTH_DEBUG"):
                raise
            typer.echo(
                f"ERROR: unexpected {type(exc).__name__}: {exc}\n"
                f"Re-run with HEARTH_DEBUG=1 for a full traceback.",
                err=True,
            )
            sys.exit(1)

    return wrapper
```

- [ ] **Step 4: Run tests + static checks**

```bash
uv run pytest tests/test_cli.py -v
uv run basedpyright
uv run ruff check hearth/ tests/
uv run hearth --version
```

Expected: 4 PASS; pyright + ruff clean; `hearth --version` prints version.

---

### Task 10: `hearth plugins list`

**Files:**
- Create: `hearth/cli/_plugins.py`
- Modify: `hearth/cli/__init__.py` (register subcommand group)
- Modify: `tests/test_cli.py`

- [ ] **Step 1: Append the failing tests**

```python
def test_plugins_list_zero_plugins(monkeypatch) -> None:
    import hearth.kernel.registry as reg_mod

    monkeypatch.setattr(reg_mod.importlib.metadata, "entry_points", lambda **kw: [])
    runner = CliRunner()
    result = runner.invoke(app, ["plugins", "list"])
    assert result.exit_code == 0
    assert "Installed plugins (0)" in result.stdout
    assert "(none)" in result.stdout


def test_plugins_list_renders_columns(monkeypatch) -> None:
    import sys
    import types

    import hearth.kernel.registry as reg_mod
    from hearth import bases_for

    mod = types.ModuleType("hearth_test_cli_list")
    sys.modules["hearth_test_cli_list"] = mod
    EBase, _, _ = bases_for("clitestlist")

    class Cust(EBase):
        name: str

    Cust.__module__ = "hearth_test_cli_list"
    mod.Cust = Cust

    class FakeEP:
        name = "clitestlist"
        value = "hearth_test_cli_list"
        dist = type("FakeDist", (), {
            "name": "pkg-clitestlist",
            "version": "1.2.3",
            "requires": (),
        })()

    monkeypatch.setattr(
        reg_mod.importlib.metadata,
        "entry_points",
        lambda **kw: [FakeEP()] if kw.get("group") == "hearth.plugins" else [],
    )

    runner = CliRunner()
    result = runner.invoke(app, ["plugins", "list"])
    assert result.exit_code == 0
    assert "clitestlist" in result.stdout
    assert "1.2.3" in result.stdout
    assert "hearth_test_cli_list" in result.stdout


def test_plugins_list_surfaces_registry_build_errors(monkeypatch) -> None:
    import hearth.kernel.registry as reg_mod

    class BrokenEP:
        name = "broken"
        value = "this_module_does_not_exist_xyz"
        dist = type("FakeDist", (), {"name": "pkg-broken", "version": "0", "requires": ()})()

    monkeypatch.setattr(
        reg_mod.importlib.metadata,
        "entry_points",
        lambda **kw: [BrokenEP()] if kw.get("group") == "hearth.plugins" else [],
    )

    runner = CliRunner()
    result = runner.invoke(app, ["plugins", "list"])
    assert result.exit_code == 1
    combined = result.stdout + (result.stderr or "")
    assert "ERROR" in combined
    assert "broken" in combined
```

- [ ] **Step 2: Run tests to verify they fail**

Expected: `No such command 'plugins'`.

- [ ] **Step 3: Create `hearth/cli/_plugins.py`**

```python
"""`hearth plugins ...` subcommand group."""

from __future__ import annotations

import typer

from hearth.cli import _wrap_command  # pyright: ignore[reportPrivateUsage]
from hearth.kernel.registry import Registry, RegistryBuildError

plugins_app = typer.Typer(help="Inspect installed plugins.", no_args_is_help=True)


def _format_deps(deps: list[str]) -> str:
    return ", ".join(deps) if deps else "(none)"


def _print_registry_build_error(err: RegistryBuildError) -> None:
    typer.echo("ERROR: cannot build plugin registry.\n", err=True)
    for issue in err.issues:
        typer.echo(f"  - {issue}", err=True)
    typer.echo("\nFix the above and rerun.", err=True)


@plugins_app.command("list")
@_wrap_command
def list_cmd() -> None:
    """List installed plugins."""
    try:
        registry = Registry.build()
    except RegistryBuildError as err:
        _print_registry_build_error(err)
        raise typer.Exit(1) from err

    aliases = sorted(registry.plugins)
    typer.echo(f"Installed plugins ({len(aliases)}):")
    if not aliases:
        typer.echo("  (none)")
        return
    for alias in aliases:
        info = registry.get(alias)
        typer.echo(
            f"  {alias:<14}{info.version:<8}{info.module:<24}"
            f"deps: {_format_deps(info.depends_on):<18}"
            f"extends: {_format_deps(info.extends)}",
        )
```

- [ ] **Step 4: Register the subcommand group**

Append to `hearth/cli/__init__.py`:

```python
from hearth.cli._plugins import plugins_app  # noqa: E402  pyright: ignore[reportPrivateUsage]

app.add_typer(plugins_app, name="plugins")
```

- [ ] **Step 5: Run tests + static checks**

Expected: 7 CLI tests PASS.

---

### Task 11: `hearth plugins deps`

**Files:**
- Modify: `hearth/cli/_plugins.py`
- Modify: `tests/test_cli.py`

- [ ] **Step 1: Append the failing tests**

```python
def test_plugins_deps_renders_tree(monkeypatch) -> None:
    import sys
    import types

    import hearth.kernel.registry as reg_mod
    from hearth import EntityId, ForeignKey, References, bases_for

    mod_a = types.ModuleType("hearth_test_cli_deps_a")
    sys.modules["hearth_test_cli_deps_a"] = mod_a
    EA, _, _ = bases_for("citdeps_a")

    class Cust(EA):
        name: str

    Cust.__module__ = "hearth_test_cli_deps_a"
    mod_a.Cust = Cust

    mod_b = types.ModuleType("hearth_test_cli_deps_b")
    sys.modules["hearth_test_cli_deps_b"] = mod_b
    EB, _, _ = bases_for("citdeps_b")

    class Order(EB):
        customer_id: EntityId = ForeignKey()
        customer: Cust = References(Cust)

    Order.__module__ = "hearth_test_cli_deps_b"
    mod_b.Order = Order

    def make_dist(name, version, requires):
        return type("FakeDist", (), {"name": name, "version": version, "requires": requires})()

    eps = [
        type("FakeEP", (), {
            "name": "citdeps_a",
            "value": "hearth_test_cli_deps_a",
            "dist": make_dist("pkg-citdeps-a", "0", ()),
        })(),
        type("FakeEP", (), {
            "name": "citdeps_b",
            "value": "hearth_test_cli_deps_b",
            "dist": make_dist("pkg-citdeps-b", "0", ("pkg-citdeps-a",)),
        })(),
    ]
    monkeypatch.setattr(
        reg_mod.importlib.metadata,
        "entry_points",
        lambda **kw: eps if kw.get("group") == "hearth.plugins" else [],
    )

    runner = CliRunner()
    result = runner.invoke(app, ["plugins", "deps"])
    assert result.exit_code == 0
    out = result.stdout
    assert "citdeps_a" in out
    assert "citdeps_b" in out
    assert "extends" in out


def test_plugins_deps_no_plugins(monkeypatch) -> None:
    import hearth.kernel.registry as reg_mod

    monkeypatch.setattr(reg_mod.importlib.metadata, "entry_points", lambda **kw: [])
    runner = CliRunner()
    result = runner.invoke(app, ["plugins", "deps"])
    assert result.exit_code == 0
    assert "(no plugins installed)" in result.stdout or "(none)" in result.stdout
```

- [ ] **Step 2: Run tests to verify they fail**

Expected: `No such command 'deps'`.

- [ ] **Step 3: Implement**

Append to `hearth/cli/_plugins.py`:

```python
@plugins_app.command("deps")
@_wrap_command
def deps_cmd() -> None:
    """Show plugin dependency graph.

    v1 limitation: renders only direct dependents of each root plugin
    (two levels deep). Deeper chains (A → B → C) show all three plugins
    but C-under-B nesting is not visualised."""
    try:
        registry = Registry.build()
    except RegistryBuildError as err:
        _print_registry_build_error(err)
        raise typer.Exit(1) from err

    typer.echo("Dependency graph:")
    aliases = registry.topological_order()
    if not aliases:
        typer.echo("  (no plugins installed)")
        return

    dependents: dict[str, list[str]] = {a: [] for a in aliases}
    for a in aliases:
        for dep in registry.get(a).depends_on:
            dependents.setdefault(dep, []).append(a)

    roots = [a for a in aliases if not registry.get(a).depends_on]
    for root in roots:
        typer.echo(f"  {root}")
        for kid in dependents.get(root, []):
            relation = "extends" if root in registry.get(kid).extends else "uses"
            typer.echo(f"  └── {kid}       ({relation})")
```

- [ ] **Step 4: Run tests + static checks**

Expected: 9 CLI tests PASS.

---

### Task 12: `hearth plugins show <alias>`

**Files:**
- Modify: `hearth/cli/_plugins.py`
- Modify: `tests/test_cli.py`

- [ ] **Step 1: Append the failing tests**

```python
def test_plugins_show_renders_detail(monkeypatch) -> None:
    import sys
    import types

    import hearth.kernel.registry as reg_mod
    from hearth import bases_for

    mod = types.ModuleType("hearth_test_cli_show")
    sys.modules["hearth_test_cli_show"] = mod
    E, A, Ev = bases_for("citshow")

    class Cust(E):
        name: str

    class DoSomething(A):
        x: int

    class Happened(Ev):
        y: str

    for cls in (Cust, DoSomething, Happened):
        cls.__module__ = "hearth_test_cli_show"
        setattr(mod, cls.__name__, cls)

    class FakeEP:
        name = "citshow"
        value = "hearth_test_cli_show"
        dist = type("FakeDist", (), {"name": "pkg-citshow", "version": "9.9.9", "requires": ()})()

    monkeypatch.setattr(
        reg_mod.importlib.metadata,
        "entry_points",
        lambda **kw: [FakeEP()] if kw.get("group") == "hearth.plugins" else [],
    )

    runner = CliRunner()
    result = runner.invoke(app, ["plugins", "show", "citshow"])
    assert result.exit_code == 0
    assert "citshow" in result.stdout
    assert "9.9.9" in result.stdout
    assert "Cust" in result.stdout
    assert "DoSomething" in result.stdout
    assert "Happened" in result.stdout


def test_plugins_show_unknown_alias_exits_1(monkeypatch) -> None:
    import hearth.kernel.registry as reg_mod

    monkeypatch.setattr(reg_mod.importlib.metadata, "entry_points", lambda **kw: [])
    runner = CliRunner()
    result = runner.invoke(app, ["plugins", "show", "ghost"])
    assert result.exit_code == 1
    combined = result.stdout + (result.stderr or "")
    assert "ghost" in combined
```

- [ ] **Step 2: Run tests to verify they fail**

Expected: `No such command 'show'`.

- [ ] **Step 3: Implement**

Append to `hearth/cli/_plugins.py`:

```python
@plugins_app.command("show")
@_wrap_command
def show_cmd(alias: str) -> None:
    """Show detailed info for one plugin."""
    try:
        registry = Registry.build()
    except RegistryBuildError as err:
        _print_registry_build_error(err)
        raise typer.Exit(1) from err

    if alias not in registry.plugins:
        installed = ", ".join(sorted(registry.plugins)) or "(none)"
        typer.echo(f"ERROR: no plugin named {alias!r}. Installed: {installed}", err=True)
        raise typer.Exit(1)

    info = registry.get(alias)
    extended_by = sorted(a for a, p in registry.plugins.items() if alias in p.extends)
    used_by = sorted(a for a, p in registry.plugins.items() if alias in p.depends_on)

    typer.echo(f"Plugin: {info.alias}")
    typer.echo(f"  Package:       {info.package} {info.version}")
    typer.echo(f"  Module:        {info.module}")
    typer.echo(f"  Install path:  {info.install_path}")
    typer.echo(f"  Depends on:    {_format_deps(info.depends_on)}")
    typer.echo(f"  Extends:       {_format_deps(info.extends)}")
    typer.echo(f"  Extended by:   {_format_deps(extended_by)}")
    typer.echo(f"  Used by:       {_format_deps(used_by)}")
    typer.echo("")
    typer.echo(f"  Entities ({len(info.entities)}):  {', '.join(e.__name__ for e in info.entities) or '(none)'}")
    typer.echo(f"  Actions ({len(info.actions)}):   {', '.join(a.__name__ for a in info.actions) or '(none)'}")
    typer.echo(f"  Events ({len(info.events)}):    {', '.join(e.__name__ for e in info.events) or '(none)'}")
```

- [ ] **Step 4: Run tests + static checks**

Expected: 11 CLI tests PASS.

---

### Task 13: `hearth db init`

**Files:**
- Create: `hearth/cli/_db.py`
- Modify: `hearth/cli/__init__.py`
- Modify: `tests/test_cli.py`

- [ ] **Step 1: Append the failing tests**

```python
def test_db_init_missing_database_url_exits_1(monkeypatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    runner = CliRunner()
    result = runner.invoke(app, ["db", "init"])
    assert result.exit_code != 0
    combined = result.stdout + (result.stderr or "")
    assert "DATABASE_URL" in combined


def test_db_init_rejects_in_memory_sqlite(monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    runner = CliRunner()
    result = runner.invoke(app, ["db", "init"])
    assert result.exit_code != 0
    combined = result.stdout + (result.stderr or "")
    assert ":memory:" in combined


def test_db_init_creates_outbox(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "hearth.db"
    url = f"sqlite:///{db_path}"
    monkeypatch.setenv("DATABASE_URL", url)
    runner = CliRunner()
    result = runner.invoke(app, ["db", "init"])
    assert result.exit_code == 0
    combined = result.stdout + (result.stderr or "")
    assert "_hearth_outbox" in combined

    result2 = runner.invoke(app, ["db", "init"])
    assert result2.exit_code == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Expected: `No such command 'db'`.

- [ ] **Step 3: Create `hearth/cli/_db.py`**

```python
"""`hearth db ...` subcommand group."""

from __future__ import annotations

import asyncio
import os

import typer
from sqlalchemy import inspect

from hearth.cli import _wrap_command  # pyright: ignore[reportPrivateUsage]
from hearth.kernel._engine import make_async_engine  # pyright: ignore[reportPrivateUsage]
from hearth.kernel.persistence import METADATA
from hearth.kernel.registry import Registry, RegistryBuildError

db_app = typer.Typer(help="Database operations.", no_args_is_help=True)


def _require_database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        typer.echo(
            "ERROR: DATABASE_URL environment variable is required for `db` commands.",
            err=True,
        )
        raise typer.Exit(1)
    if url.endswith(":memory:") or url.endswith(":memory:?cache=shared"):
        typer.echo(
            "ERROR: in-memory SQLite (:memory:) is not supported for `db` commands.\n"
            "Use a file URL like `sqlite:///hearth.db` instead.",
            err=True,
        )
        raise typer.Exit(1)
    return url


@db_app.command("init")
@_wrap_command
def init_cmd() -> None:
    """Create the kernel's internal tables (`_hearth_outbox`)."""
    url = _require_database_url()
    asyncio.run(_init_impl(url))


async def _init_impl(url: str) -> None:
    typer.echo(f"Connecting to: {url}")
    engine = make_async_engine(url)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(METADATA.create_all)
        typer.echo("Created kernel tables:")
        typer.echo("  [OK] _hearth_outbox")
        typer.echo("Done.")
    finally:
        await engine.dispose()
```

- [ ] **Step 4: Register subcommand group**

Append to `hearth/cli/__init__.py`:

```python
from hearth.cli._db import db_app  # noqa: E402  pyright: ignore[reportPrivateUsage]

app.add_typer(db_app, name="db")
```

- [ ] **Step 5: Run tests + static checks**

Expected: 14 CLI tests PASS.

---

### Task 14: `hearth db status`

**Files:**
- Modify: `hearth/cli/_db.py`
- Modify: `tests/test_cli.py`

- [ ] **Step 1: Append the failing test**

```python
def test_db_status_reports_connection_ok(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "hearth.db"
    url = f"sqlite:///{db_path}"
    monkeypatch.setenv("DATABASE_URL", url)
    runner = CliRunner()
    runner.invoke(app, ["db", "init"])
    result = runner.invoke(app, ["db", "status"])
    assert result.exit_code == 0
    assert "Connection" in result.stdout
    assert "OK" in result.stdout
    assert "_hearth_outbox" in result.stdout
```

- [ ] **Step 2: Run test to verify it fails**

Expected: `No such command 'status'`.

- [ ] **Step 3: Implement**

Append to `hearth/cli/_db.py`:

```python
@db_app.command("status")
@_wrap_command
def status_cmd() -> None:
    """Show database connection status and kernel-table presence."""
    url = _require_database_url()
    asyncio.run(_status_impl(url))


async def _status_impl(url: str) -> None:
    typer.echo(f"Database: {url}")
    engine = make_async_engine(url)
    try:
        async with engine.connect() as conn:
            existing = await conn.run_sync(
                lambda sync_conn: set(inspect(sync_conn).get_table_names()),
            )
        typer.echo("  Connection:     OK")
        outbox_status = "[present]" if "_hearth_outbox" in existing else "[missing — run `hearth db init`]"
        typer.echo(f"  Kernel tables:  _hearth_outbox {outbox_status}")
        typer.echo("  Plugin tables:  0 tracked  (schema log not yet initialized)")
        typer.echo("  Orphan tables:  (none detected)")
    finally:
        await engine.dispose()
```

- [ ] **Step 4: Run tests + static checks**

Expected: 15 CLI tests PASS.

---

### Task 15: `hearth db graph`

**Files:**
- Modify: `hearth/cli/_db.py`
- Modify: `tests/test_cli.py`

- [ ] **Step 1: Append the failing test**

```python
def test_db_graph_text_output(monkeypatch) -> None:
    import sys
    import types

    import hearth.kernel.registry as reg_mod
    from hearth import EntityId, ForeignKey, References, bases_for

    mod = types.ModuleType("hearth_test_cli_graph")
    sys.modules["hearth_test_cli_graph"] = mod
    EBase, _, _ = bases_for("citgraph")

    class Customer(EBase):
        name: str

    class Order(EBase):
        customer_id: EntityId = ForeignKey()
        customer: Customer = References(Customer)

    for cls in (Customer, Order):
        cls.__module__ = "hearth_test_cli_graph"
        setattr(mod, cls.__name__, cls)

    class FakeEP:
        name = "citgraph"
        value = "hearth_test_cli_graph"
        dist = type("FakeDist", (), {"name": "pkg-citgraph", "version": "0", "requires": ()})()

    monkeypatch.setattr(
        reg_mod.importlib.metadata,
        "entry_points",
        lambda **kw: [FakeEP()] if kw.get("group") == "hearth.plugins" else [],
    )

    runner = CliRunner()
    result = runner.invoke(app, ["db", "graph"])
    assert result.exit_code == 0
    assert "citgraph__customer" in result.stdout
    assert "citgraph__order" in result.stdout
    assert "customer_id" in result.stdout
```

- [ ] **Step 2: Run test to verify it fails**

Expected: `No such command 'graph'`.

- [ ] **Step 3: Implement**

Append to `hearth/cli/_db.py`:

```python
@db_app.command("graph")
@_wrap_command
def graph_cmd() -> None:
    """Print a text-based schema graph (tables and FKs) from declared entities."""
    try:
        registry = Registry.build()
    except RegistryBuildError as err:
        typer.echo("ERROR: cannot build plugin registry.", err=True)
        for issue in err.issues:
            typer.echo(f"  - {issue}", err=True)
        raise typer.Exit(1) from err

    all_tables: set[str] = set()
    children_of: dict[str, list[tuple[str, str, str, str]]] = {}
    for info in registry.plugins.values():
        for ent in info.entities:
            table = getattr(ent, "__table__", None)
            if table is None:
                continue
            all_tables.add(table.name)
            for col in table.columns:
                for fk in col.foreign_keys:
                    parent_table = fk.column.table.name
                    all_tables.add(parent_table)
                    children_of.setdefault(parent_table, []).append(
                        (col.name, table.name, fk.column.name, fk.ondelete or "NO ACTION"),
                    )

    typer.echo(f"Schema graph ({len(registry.plugins)} plugins, {len(all_tables)} tables):")
    typer.echo("")
    for table in sorted(all_tables):
        typer.echo(f"  {table}")
        for child_col, child_table, parent_col, ondelete in children_of.get(table, []):
            typer.echo(
                f"  └── {child_table}    ({child_col} → {table}.{parent_col}, {ondelete})",
            )
        typer.echo("")
```

- [ ] **Step 4: Run tests + static checks**

Expected: 16 CLI tests PASS.

---

### Task 16: Wire the commerce plugin — entry point + `bases_for` refactor

**Files:**
- Modify: `plugins/commerce/pyproject.toml`
- Create: `plugins/commerce/hearth_commerce/_base.py`
- Modify: `plugins/commerce/hearth_commerce/entities/customer.py`
- Modify: `plugins/commerce/hearth_commerce/actions/identify_customer.py`
- Modify: `plugins/commerce/hearth_commerce/events/customer_identified.py`

After this, `uv run hearth plugins list` shows the commerce plugin row.

- [ ] **Step 1: Add entry point to `plugins/commerce/pyproject.toml`**

Append:

```toml
[project.entry-points."hearth.plugins"]
commerce = "hearth_commerce"
```

- [ ] **Step 2: Create `plugins/commerce/hearth_commerce/_base.py`**

```python
"""Commerce plugin's local Entity / Action / Event bases. The plugin alias
'commerce' is declared exactly once, here."""

from hearth import bases_for

Entity, Action, Event = bases_for("commerce")
```

- [ ] **Step 3: Refactor `entities/customer.py`**

```bash
grep -n "class Customer\|from hearth" plugins/commerce/hearth_commerce/entities/customer.py
```

Switch `from hearth import Entity` → `from hearth_commerce._base import Entity`. Drop `plugin="commerce"` from `class Customer(Entity, plugin="commerce")` — it's inherited.

- [ ] **Step 4: Refactor the action and the event identically**

In `actions/identify_customer.py`: switch the Action import to `from hearth_commerce._base import Action`; drop `plugin="commerce"`.

In `events/customer_identified.py`: switch the Event import to `from hearth_commerce._base import Event`; drop `plugin="commerce"`.

- [ ] **Step 5: Sync workspace and verify end-to-end**

```bash
uv sync --dev
DATABASE_URL=postgresql://postgres:isaacrivas10@127.0.0.1:5432/hearth uv run pytest -q
uv run basedpyright
uv run ruff check hearth/ tests/ scripts/ plugins/
uv run hearth plugins list
uv run hearth plugins show commerce
```

Expected:
- Full test suite passes.
- `hearth plugins list` shows `commerce 0.0.1 hearth_commerce deps: (none) extends: (none)`.
- `hearth plugins show commerce` lists `Customer`, `IdentifyCustomer`, `CustomerIdentified`.

---

### Task 17: Replace `docs/core/kernel/registry.md` stub with reference doc

**Files:**
- Modify: `docs/core/kernel/registry.md`

- [ ] **Step 1: Replace the file**

```markdown
# Spec: plugin and primitive registry

> **Source module**: `hearth/kernel/registry.py`
> **Parent ADRs**: [ADR-0003 (plugin model)](../../architecture/0003-plugin-model.md), [ADR-0008 (built-in plugins)](../../architecture/0008-built-in-plugins.md).

## What the registry is

The startup-built, in-memory record of which plugins are installed in this
Python environment. The kernel discovers plugins via the
`[project.entry-points."hearth.plugins"]` group; for each, it imports the
declared module (which triggers metaclass / `__init_subclass__` registration
of the plugin's Entity / Action / Event classes) and indexes them.

Built once per process. Read-only after construction. No hot-reload (ADR-0003).

## Plugin-author contract

Each plugin's `pyproject.toml` declares:

```toml
[project.entry-points."hearth.plugins"]
commerce = "hearth_commerce"
```

Vocabulary libraries (which contribute only `Value` types, no
entities/actions/events) do *not* register here — they're regular Python
libraries, not plugins.

Plugin authors typically use `bases_for(alias)` to bind their plugin alias
in one place:

```python
# hearth_commerce/_base.py
from hearth import bases_for

Entity, Action, Event = bases_for("commerce")
```

Every concrete class in the plugin then imports from `_base` and
automatically carries `_hearth_plugin = "commerce"` via inheritance.

## What it exposes

```python
class PluginInfo:
    alias: str
    package: str
    version: str
    module: str
    install_path: str
    depends_on: list[str]    # other plugin aliases this imports
    extends: list[str]       # plugin aliases this has foreign keys into
    entities: list[type[Entity]]
    actions: list[type[Action]]
    events: list[type[Event]]


class Registry:
    plugins: dict[str, PluginInfo]
    def get(self, alias: str) -> PluginInfo: ...
    def topological_order(self) -> list[str]: ...
    def entities_for(self, alias: str) -> list[type[Entity]]: ...

    @classmethod
    def build(cls) -> "Registry": ...
```

## Validation phases

`Registry.build()` runs three phases, each collecting all errors before raising
a `RegistryBuildError`:

1. **Import** every entry-point module.
2. **Alias/kwarg consistency** — every Entity / Action / Event subclass shipped
   in a plugin's module tree must declare `_hearth_plugin` matching that
   plugin's entry-point alias. Cross-package namespace contributions are
   rejected.
3. **Dependency graph** — `depends_on` derived from each plugin's
   `Requires-Dist` intersected with known plugin aliases; `extends` derived
   from each entity's `__table__.foreign_keys`. The invariant
   `extends ⊆ depends_on` is enforced. Cycles produce an error.

## What plugins cannot do

- Import `hearth.kernel.registry` (kernel-internal).
- Modify the registry after process start.
- Contribute entities to another plugin's namespace.

## CLI surface

The registry is queried by `hearth plugins list`, `hearth plugins deps`, and
`hearth plugins show <alias>`. The `db` subcommand group does not require the
registry; the kernel's own `_hearth_outbox` table is plugin-independent.
```

- [ ] **Step 2: Verify the file is in place**

```bash
ls docs/core/kernel/registry.md docs/architecture/0003-plugin-model.md docs/architecture/0008-built-in-plugins.md
```

Expected: all three present.

---

## End-to-end verification (after all tasks)

```bash
# Full test suite, both dialects
DATABASE_URL=postgresql://postgres:isaacrivas10@127.0.0.1:5432/hearth uv run pytest -q
uv run pytest -q  # no Postgres path

# Static checks
uv run basedpyright
uv run ruff check hearth/ tests/ scripts/ plugins/
uv run ruff format --check hearth/ tests/ scripts/ plugins/

# End-to-end CLI smoke
uv run hearth --version
uv run hearth plugins list
uv run hearth plugins deps
uv run hearth plugins show commerce
DATABASE_URL=sqlite:///e2e_smoke.db uv run hearth db init
DATABASE_URL=sqlite:///e2e_smoke.db uv run hearth db status
uv run hearth db graph
rm -f e2e_smoke.db
```

Every command should exit 0 with sensible output.

---

## Notes for the executor

- **Test isolation**: many registry tests inject fake plugin modules into `sys.modules` via `types.ModuleType`. These linger across tests in the same session. Each test uses a unique module name (`hearth_test_<scenario>`) so they don't collide; the registry filters classes by module top-level package, so test-A's classes don't pollute test-B's registry.
- **`bases_for` caching**: `@lru_cache` on `bases_for` means tests using the same alias get the same base classes. Tests should use unique aliases.
- **`hearth_commons` is NOT a plugin** — verify after Task 16 that `hearth plugins list` does *not* show commons.
- **Do not run `git add` or `git commit`** — commits are out of scope for this plan. The user authors them separately.
