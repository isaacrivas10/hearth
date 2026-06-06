# hearth-web Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `hearth-web` — a FastAPI + Jinja2(async) + HTMX presentation plugin that serves a read-only admin console, owns session/API-key auth transport, and lets other plugins add *and extend* pages, nav, and in-page slots — all served via `hearth web serve`.

**Architecture:** A single installable plugin (`plugins/web/`) hard-depending on `hearth-auth`. An app factory `create_app()` builds a FastAPI app: it discovers plugin contributions from the `hearth.web` entry-point group (mirroring the existing `hearth.cli` loader), mounts their routers/templates/static/nav/slots, installs session + auth + authz machinery, and mounts the built-in `/admin` console that introspects the kernel `Registry`. Authorization is abstract: declared permission strings validated against the actor's `has_permission` (auth's contract), memoized per request. One per-request DB transaction (a FastAPI yield-dependency) stays open through template rendering so async slot providers and the `can` filter can hit the DB.

**Tech Stack:** Python 3.13, FastAPI, Starlette `SessionMiddleware`, Jinja2 (async mode), uvicorn, itsdangerous, HTMX + Alpine.js (vendored), pytest + Starlette `TestClient`, `hearth` kernel + `hearth-auth`.

**Spec:** `docs/superpowers/specs/2026-06-05-hearth-web-design.md` — read it first.

---

## Key kernel/auth APIs this plan calls (verified against source)

- `from hearth import scope, transaction_factory, Actor, Anonymous, System, EntityId, UnitOfWork`
- `transaction_factory(engine)` → `make_uow(*, actor)` async context manager (one transaction; commits on clean exit; outbox events flushed on commit). Built once from an `AsyncEngine`.
- `from hearth.kernel._engine import make_async_engine` — dialect-aware engine factory (SQLite gets `StaticPool`).
- `from hearth.kernel.registry import Registry, PluginInfo` — `Registry.build()` (entry-point discovery) or `Registry(plugins={...})` for tests. `PluginInfo` has `.alias .package .version .module .depends_on .extends .entities .actions .events`.
- `from hearth.kernel.persistence import OUTBOX_TABLE` — kernel outbox table (for db page).
- `from hearth.testing import BaseHarness` — `BaseHarness(engine)`, `.setup()`, `.run(action, actor=None)`.
- `from hearth_auth.actions.user_auth import AuthenticateUser` — `AuthenticateUser(email=EmailAddress, password=SecretStr).handle(uow, actor) -> User`; raises `AuthenticationFailed`.
- `from hearth_auth.actions.api_key import AuthenticateApiKey` — `AuthenticateApiKey(key_string=SecretStr).handle(uow, actor) -> ApiKey`; raises `AuthenticationFailed`.
- `from hearth_auth import User, AuthenticationFailed` ; `from hearth_auth.testing import seed_admin, grant_permissions`.
- `Actor.has_permission(uow, "resource:action") -> bool` — returns `False` for unmatched/malformed strings; **never raises** for unknown permissions. `Anonymous().has_permission` is always `False`; `User.has_permission` queries via the passed uow using only `self.id`/`self.is_active`.
- `from hearth_commons import EmailAddress` — `EmailAddress(raw="a@b.com")`.

## Design decisions locked for implementation

1. **`create_app(*, engine=None, registry=None, modules=None) -> FastAPI`.** Tests inject `engine` (shared with a `BaseHarness` so the in-memory `StaticPool` DB is the same), `registry` (a hand-built `Registry`), and `modules` (fake `WebModule`s) — no entry-point monkeypatching, no temp files. Production passes nothing: engine from `DATABASE_URL`, `registry=Registry.build()`, `modules=discover_web_modules()`.
2. **One request transaction.** `request_uow` is a yield-dependency opening `make_uow(actor=Anonymous())`; it stays open until the response body is sent (so async slot providers + `can` filter run against a live session). `current_actor` resolves the *authorization* actor inside that same uow. **Caveat (documented):** the uow's *audit* actor is fixed `Anonymous()` in v1; the only event emitted on the read path is `ApiKeyAuthenticated`, audited as anonymous. When the write surface lands, refactor `request_uow` to two-phase so the audit actor equals the resolved actor.
3. **Authz is one memoized predicate.** `make_check(uow, actor)` returns an async `check(perm)` memoizing `actor.has_permission`. `requires_permission`, nav gating, slot gating, and the `can` Jinja filter all call it.
4. **Jinja async mode.** `Environment(enable_async=True, ...)`. `render_slot` is an async function passed into the render context; `can` is an `@pass_context` async filter reading `check` from context. Both are auto-awaited by Jinja.
5. **`/admin` namespace.** Admin owns `/` (redirect→`/admin`) and `/admin/*`. Reserved module names: `admin login logout static health`.

---

## File Structure

```text
plugins/web/
  pyproject.toml                         # deps, hearth.cli + (no hearth.plugins) entry points, workspace
  hearth_web/
    __init__.py                          # public SDK: WebModule, NavItem, SlotContribution, current_actor, requires_permission
    extensions.py                        # WebModule, NavItem, SlotContribution, discover_web_modules()
    slots.py                             # SlotRegistry, make_render_slot()
    security.py                          # Forbidden, make_check, current_actor, request_uow, get_check, requires_permission, exception handlers, can filter
    sessions.py                          # login/logout routes, validate_next()
    rendering.py                         # build_jinja_env(), render(), page_context dep, nav build/group
    app.py                               # create_app()
    cli.py                               # web_app Typer group: serve
    admin/
      __init__.py
      router.py                          # /admin pages (dashboard, plugins, entities, actions, events, schema, db), /health
      introspection.py                   # registry→view-model helpers, mermaid builder
    templates/
      base.html  login.html
      admin/ dashboard.html plugins.html plugin_detail.html entities.html entity_detail.html actions.html events.html schema.html db.html
      _partials/ nav.html
    static/
      base.css  theme-toggle.js  vendor/htmx.min.js  vendor/alpine.min.js
docs/core/plugins/web.md                 # plugin reference (mirrors auth.md)
plugins/web/tests/
  conftest.py
  test_web_extensions.py test_web_slots.py test_web_auth.py
  test_web_authz.py test_web_admin.py test_web_cli.py
```

Root `pyproject.toml`: add `hearth-web` to the uv workspace members and a `web = ["hearth-web"]` extra.

---

## Milestone 0 — Package scaffold & minimal app

### Task 0.1: Package skeleton & dependencies

**Files:**
- Create: `plugins/web/pyproject.toml`
- Create: `plugins/web/hearth_web/__init__.py` (empty for now)
- Create: `plugins/web/hearth_web/py.typed` (empty)
- Modify: root `pyproject.toml`

- [ ] **Step 1: Write `plugins/web/pyproject.toml`**

```toml
[project]
name = "hearth-web"
version = "0.0.1"
description = "Hearth plugin: web framework + read-only admin console, served via the hearth CLI."
requires-python = ">=3.13"
dependencies = [
    "hearth",
    "hearth-auth",
    "hearth-commons",
    "fastapi>=0.115",
    "uvicorn>=0.30",
    "jinja2>=3.1",
    "itsdangerous>=2.2",
    "python-multipart>=0.0.9",
]

[project.entry-points."hearth.cli"]
web = "hearth_web.cli:web_app"

[tool.uv.sources]
hearth = { workspace = true }
hearth-auth = { workspace = true }
hearth-commons = { workspace = true }

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["hearth_web"]
```

Note: **no** `[project.entry-points."hearth.plugins"]` block — hearth-web contributes no entities/actions/events and stays out of the schema registry (ADR-0008, like `commons`).

- [ ] **Step 2: Create empty `plugins/web/hearth_web/__init__.py` and `py.typed`**

```python
"""hearth_web: web framework + admin console for Hearth."""
```

- [ ] **Step 3: Add to root workspace & extra**

In root `pyproject.toml`, add `"plugins/web"` to `[tool.uv.workspace] members` (match the existing list format used for `plugins/auth`), and add under `[project.optional-dependencies]`:

```toml
web = ["hearth-web"]
```

- [ ] **Step 4: Sync and verify the package resolves**

Run: `rtk proxy uv sync --extra web`
Expected: resolves and installs `hearth-web` in the workspace (exit 0).

---

### Task 0.2: Test conftest (shared web-app fixture)

**Files:**
- Create: `plugins/web/tests/conftest.py`

- [ ] **Step 1: Write the conftest**

This fixture shares one in-memory `StaticPool` engine between a `BaseHarness` (to create tables + seed) and `create_app` (via injected `engine`), sets the required secret, and returns a ready `TestClient` plus the harness.

```python
"""Shared fixtures for hearth-web tests."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass

import pytest
import pytest_asyncio
from starlette.testclient import TestClient

from hearth.kernel._engine import make_async_engine  # pyright: ignore[reportPrivateUsage]
from hearth.kernel.registry import PluginInfo, Registry
from hearth.testing import BaseHarness
from hearth_auth import User
from hearth_auth.testing import seed_admin

@dataclass
class WebFixture:
    client: TestClient
    harness: BaseHarness
    admin: User

def make_registry() -> Registry:
    """A small real registry: the auth plugin, introspected from its classes."""
    import hearth_auth

    # Force the auth subpackages to import so their Entity/Action/Event
    # subclasses are registered before _classes_from_module_tree walks
    # __subclasses__(). Without this the lists can come back empty.
    import hearth_auth.actions  # noqa: F401
    import hearth_auth.entities  # noqa: F401
    import hearth_auth.events  # noqa: F401

    info = PluginInfo(
        alias="auth",
        package="hearth-auth",
        version="0.0.1",
        module="hearth_auth",
        install_path=hearth_auth.__file__ or "<unknown>",
    )
    # Reuse the registry's own class discovery so entities/actions/events match prod.
    from hearth.kernel.registry import _classes_from_module_tree  # pyright: ignore[reportPrivateUsage]
    from hearth.primitives.action import Action
    from hearth.primitives.entity import Entity
    from hearth.primitives.event import Event

    object.__setattr__(info, "entities", _classes_from_module_tree(Entity, "hearth_auth"))
    object.__setattr__(info, "actions", _classes_from_module_tree(Action, "hearth_auth"))
    object.__setattr__(info, "events", _classes_from_module_tree(Event, "hearth_auth"))
    return Registry(plugins={"auth": info}, _topological_order=["auth"])

@pytest_asyncio.fixture
async def web(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[WebFixture]:
    monkeypatch.setenv("HEARTH_WEB_SECRET_KEY", "test-secret-key")
    engine = make_async_engine("sqlite:///:memory:")
    harness = BaseHarness(engine)
    await harness.setup()  # creates all entity tables + outbox on this engine
    admin = await seed_admin(harness)

    from hearth_web.app import create_app

    app = create_app(engine=engine, registry=make_registry(), modules=[])
    with TestClient(app) as client:
        yield WebFixture(client=client, harness=harness, admin=admin)
    await engine.dispose()

@pytest_asyncio.fixture
async def web_factory(monkeypatch: pytest.MonkeyPatch):
    """Build ISOLATED web apps (each with its own engine) for tests that need
    custom modules. Never share one StaticPool SQLite engine across two apps —
    both call session.begin() on the same underlying connection and deadlock.
    Returns an async builder; all engines are disposed at teardown."""
    monkeypatch.setenv("HEARTH_WEB_SECRET_KEY", "test-secret-key")
    engines: list[object] = []

    async def build(modules: list[object]) -> WebFixture:
        from hearth_web.app import create_app

        engine = make_async_engine("sqlite:///:memory:")
        engines.append(engine)
        harness = BaseHarness(engine)
        await harness.setup()
        admin = await seed_admin(harness)
        app = create_app(engine=engine, registry=make_registry(), modules=modules)
        client = TestClient(app)
        client.__enter__()
        return WebFixture(client=client, harness=harness, admin=admin)

    yield build
    for eng in engines:
        await eng.dispose()  # type: ignore[union-attr]
```

- [ ] **Step 2: Ensure `pytest-asyncio` is configured as `auto`**

Run: `rtk grep "asyncio_mode|pytest-asyncio|pytest-asyncio" pyproject.toml`
Expected: shows the existing config. **Require `asyncio_mode = "auto"`** under `[tool.pytest.ini_options]` in the root `pyproject.toml` (the kernel already runs async tests; confirm this is set). With `auto` mode: async fixtures (`web`, `web_factory`) resolve for *sync* tests, and `async def test_*` functions run without per-test `@pytest.mark.asyncio` markers. The plan's tests still mark async tests explicitly so they pass under either mode, but `auto` is required for the sync tests that consume the async `web` fixture (e.g. `test_health_no_auth(web)`). Also add `hearth-web` (and `pytest-asyncio` if not already a workspace dev dep) to the test dependency group.

---

### Task 0.3: Minimal `create_app` with `/health`

**Files:**
- Create: `plugins/web/hearth_web/app.py`
- Create: `plugins/web/hearth_web/admin/__init__.py` (empty)
- Test: `plugins/web/tests/test_web_admin.py`

- [ ] **Step 1: Write the failing test**

```python
def test_health_no_auth(web):
    resp = web.client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
```

- [ ] **Step 2: Run it — expect failure**

Run: `rtk proxy uv run pytest plugins/web/tests/test_web_admin.py::test_health_no_auth -v`
Expected: FAIL (`ModuleNotFoundError: hearth_web.app`).

- [ ] **Step 3: Implement minimal `create_app`**

```python
"""The hearth-web application factory."""

from __future__ import annotations

import os

from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncEngine

from hearth.kernel._engine import make_async_engine  # pyright: ignore[reportPrivateUsage]
from hearth.kernel.registry import Registry
from hearth.kernel.transaction import transaction_factory

def create_app(
    *,
    engine: AsyncEngine | None = None,
    registry: Registry | None = None,
    modules: list[object] | None = None,
) -> FastAPI:
    secret = os.environ.get("HEARTH_WEB_SECRET_KEY")
    if not secret:
        raise RuntimeError("HEARTH_WEB_SECRET_KEY is required to start hearth-web")

    if engine is None:
        url = os.environ.get("DATABASE_URL")
        if not url:
            raise RuntimeError("DATABASE_URL is required to start hearth-web")
        engine = make_async_engine(url)

    if registry is None:
        registry = Registry.build()

    app = FastAPI(title="Hearth")
    app.state.engine = engine
    app.state.uow_factory = transaction_factory(engine)
    app.state.registry = registry
    app.state.secret = secret

    @app.get("/health")
    async def health() -> dict[str, str]:  # pyright: ignore[reportUnusedFunction]
        return {"status": "ok"}

    return app
```

- [ ] **Step 4: Run the test — expect pass**

Run: `rtk proxy uv run pytest plugins/web/tests/test_web_admin.py::test_health_no_auth -v`
Expected: PASS.

---

## Milestone 1 — Extension contract (`hearth.web` discovery)

### Task 1.1: `WebModule`, `NavItem`, `SlotContribution` dataclasses

**Files:**
- Create: `plugins/web/hearth_web/extensions.py`
- Test: `plugins/web/tests/test_web_extensions.py`

- [ ] **Step 1: Write the failing test**

```python
from fastapi import APIRouter

from hearth_web.extensions import NavItem, SlotContribution, WebModule

def test_webmodule_defaults():
    m = WebModule(name="inventory", router=APIRouter())
    assert m.name == "inventory"
    assert m.nav == []
    assert m.contributions == []
    assert m.templates_dir is None

def test_navitem_fields():
    n = NavItem(label="Stock", path="/inventory/stock", permission="inventory:read",
                section="operations", order=10)
    assert n.section == "operations"
    assert n.icon is None

def test_slotcontribution_fields():
    s = SlotContribution(slot="admin.dashboard.widgets", template="inventory/w.html",
                         permission="inventory:read", order=5)
    assert s.provider is None
```

- [ ] **Step 2: Run — expect failure**

Run: `rtk proxy uv run pytest plugins/web/tests/test_web_extensions.py -v`
Expected: FAIL (`ModuleNotFoundError: hearth_web.extensions`).

- [ ] **Step 3: Implement the dataclasses**

```python
"""Plugin web-contribution objects and discovery of the `hearth.web` group."""

from __future__ import annotations

import importlib.metadata
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from fastapi import APIRouter

logger = logging.getLogger("hearth_web")

RESERVED_NAMES = frozenset({"admin", "login", "logout", "static", "health", ""})

Provider = Callable[..., Awaitable[dict[str, Any]]]

@dataclass(frozen=True)
class NavItem:
    label: str
    path: str
    permission: str | None = None
    section: str | None = None
    order: int = 0
    icon: str | None = None

@dataclass(frozen=True)
class SlotContribution:
    slot: str
    template: str
    provider: Provider | None = None
    permission: str | None = None
    order: int = 0

@dataclass(frozen=True)
class WebModule:
    name: str
    router: APIRouter | None = None
    templates_dir: str | None = None
    static_dir: str | None = None
    nav: list[NavItem] = field(default_factory=list)
    contributions: list[SlotContribution] = field(default_factory=list)
```

- [ ] **Step 4: Run — expect pass**

Run: `rtk proxy uv run pytest plugins/web/tests/test_web_extensions.py -v`
Expected: PASS.

---

### Task 1.2: `discover_web_modules()` loader (mirrors the CLI loader)

**Files:**
- Modify: `plugins/web/hearth_web/extensions.py`
- Test: `plugins/web/tests/test_web_extensions.py`

- [ ] **Step 1: Write the failing tests**

```python
from hearth_web.extensions import load_web_modules

class _FakeEP:
    def __init__(self, name, obj):
        self.name = name
        self._obj = obj

    def load(self):
        if isinstance(self._obj, Exception):
            raise self._obj
        return self._obj

def test_load_skips_reserved(caplog):
    good = WebModule(name="inventory", router=APIRouter())
    mods = load_web_modules([_FakeEP("login", good), _FakeEP("inventory", good)])
    assert [m.name for m in mods] == ["inventory"]
    assert "reserved" in caplog.text

def test_load_skips_duplicates(caplog):
    a = WebModule(name="inventory", router=APIRouter())
    b = WebModule(name="inventory", router=APIRouter())
    mods = load_web_modules([_FakeEP("inventory", a), _FakeEP("inventory", b)])
    assert mods == [a]
    assert "already registered" in caplog.text

def test_load_isolates_broken(caplog):
    good = WebModule(name="inventory", router=APIRouter())
    mods = load_web_modules([_FakeEP("boom", RuntimeError("nope")),
                             _FakeEP("inventory", good)])
    assert [m.name for m in mods] == ["inventory"]
    assert "failed to load" in caplog.text
```

- [ ] **Step 2: Run — expect failure**

Run: `rtk proxy uv run pytest plugins/web/tests/test_web_extensions.py -k load -v`
Expected: FAIL (`ImportError: load_web_modules`).

- [ ] **Step 3: Implement the loader**

Append to `extensions.py`:

```python
def load_web_modules(entry_points: object) -> list[WebModule]:
    """Resolve an iterable of entry points into WebModules, applying the same
    discipline as the CLI loader: reserved-name refusal, dedup, failure isolation."""
    registered: set[str] = set()
    modules: list[WebModule] = []
    for ep in entry_points:  # type: ignore[union-attr]
        name = ep.name
        if name in RESERVED_NAMES:
            logger.warning("web module name %r is reserved; skipping", name)
            continue
        if name in registered:
            logger.warning("web module name %r already registered; skipping", name)
            continue
        try:
            module = ep.load()
        except Exception as exc:  # noqa: BLE001 — isolation is the point
            logger.warning("failed to load web module %r: %s: %s",
                           name, type(exc).__name__, exc)
            continue
        if not isinstance(module, WebModule):
            logger.warning("web module %r did not resolve to a WebModule; skipping", name)
            continue
        modules.append(module)
        registered.add(name)
    return modules

def discover_web_modules() -> list[WebModule]:
    """Production entry point: discover the `hearth.web` group."""
    return load_web_modules(importlib.metadata.entry_points(group="hearth.web"))
```

- [ ] **Step 4: Run — expect pass**

Run: `rtk proxy uv run pytest plugins/web/tests/test_web_extensions.py -v`
Expected: PASS (all extension tests).

---

### Task 1.3: Mount module routers + static in `create_app`

**Files:**
- Modify: `plugins/web/hearth_web/app.py`
- Test: `plugins/web/tests/test_web_extensions.py`

- [ ] **Step 1: Write the failing tests**

```python
import pytest
import pytest_asyncio
from starlette.testclient import TestClient

from hearth.kernel._engine import make_async_engine  # pyright: ignore[reportPrivateUsage]
from hearth.testing import BaseHarness
from hearth_auth.testing import seed_admin
from conftest import make_registry  # pytest puts the tests dir on sys.path

def _app_with(modules, monkeypatch, engine):
    monkeypatch.setenv("HEARTH_WEB_SECRET_KEY", "k")
    from hearth_web.app import create_app
    return create_app(engine=engine, registry=make_registry(), modules=modules)

@pytest_asyncio.fixture
async def engine():
    eng = make_async_engine("sqlite:///:memory:")
    h = BaseHarness(eng)
    await h.setup()
    await seed_admin(h)
    yield eng
    await eng.dispose()

def test_web_module_mounts_router(monkeypatch, engine):
    from fastapi import APIRouter
    from hearth_web.extensions import WebModule

    router = APIRouter()

    @router.get("/ping")
    async def ping():
        return {"pong": True}

    app = _app_with([WebModule(name="inventory", router=router)], monkeypatch, engine)
    with TestClient(app) as c:
        assert c.get("/inventory/ping").json() == {"pong": True}
```

- [ ] **Step 2: Run — expect failure**

Run: `rtk proxy uv run pytest plugins/web/tests/test_web_extensions.py::test_web_module_mounts_router -v`
Expected: FAIL (404 — routers not mounted yet).

- [ ] **Step 3: Implement mounting in `create_app`**

In `app.py`, replace the `modules` default handling and add mounting before `return app`:

```python
    from fastapi.staticfiles import StaticFiles  # add near top-level imports
    from pathlib import Path
    from hearth_web.extensions import WebModule, discover_web_modules

    resolved_modules = modules if modules is not None else discover_web_modules()
    app.state.modules = resolved_modules

    for module in resolved_modules:
        assert isinstance(module, WebModule)
        if module.router is not None:
            app.include_router(module.router, prefix=f"/{module.name}")
        if module.static_dir is not None:
            pkg_root = Path(_module_package_dir(module))
            app.mount(f"/static/{module.name}",
                      StaticFiles(directory=pkg_root / module.static_dir),
                      name=f"static-{module.name}")
```

Add an **explicit** `package_dir` field to the `WebModule` dataclass (in `extensions.py`) — the absolute directory the module's `templates_dir`/`static_dir` are relative to. The plugin sets it; **no auto-derivation** (that path is fragile and was deliberately dropped):

```python
# in extensions.py, add to the WebModule dataclass:
    package_dir: str | None = None  # set by the plugin: os.path.dirname(__file__)
```

The contract a plugin follows (document this in `docs/core/plugins/web.md`): a templated/static module passes `package_dir=os.path.dirname(__file__)` from its own module. A module with no templates/static may omit it. Then the mount helper is a plain attribute read:

```python
def _module_package_dir(module: WebModule) -> str:
    if module.package_dir is None:
        raise RuntimeError(
            f"web module {module.name!r} sets templates_dir/static_dir but no "
            f"package_dir; pass package_dir=os.path.dirname(__file__)"
        )
    return module.package_dir
```

Type the mount loop on `WebModule` (not `object`) so `_module_package_dir(module)` type-checks: `resolved_modules: list[WebModule]`.

- [ ] **Step 4: Run — expect pass**

Run: `rtk proxy uv run pytest plugins/web/tests/test_web_extensions.py::test_web_module_mounts_router -v`
Expected: PASS.

- [ ] **Step 5: Add the static-mount test and run**

```python
def test_web_module_static_mounted(tmp_path, monkeypatch, engine):
    from hearth_web.extensions import WebModule
    static = tmp_path / "static"
    static.mkdir()
    (static / "app.css").write_text("body{}")
    mod = WebModule(name="inventory", static_dir="static", package_dir=str(tmp_path))
    app = _app_with([mod], monkeypatch, engine)
    with TestClient(app) as c:
        assert c.get("/static/inventory/app.css").status_code == 200
```

Run: `rtk proxy uv run pytest plugins/web/tests/test_web_extensions.py -v`
Expected: PASS.

---

## Milestone 2 — Jinja env + rendering + nav

### Task 2.1: `build_jinja_env` with async mode, ChoiceLoader+PrefixLoader

**Files:**
- Create: `plugins/web/hearth_web/rendering.py`
- Modify: `plugins/web/hearth_web/app.py`
- Test: `plugins/web/tests/test_web_extensions.py`

- [ ] **Step 1: Write the failing test (namespaced template resolution)**

```python
def test_web_module_templates_namespaced(tmp_path, monkeypatch, engine):
    from hearth_web.extensions import WebModule
    tdir = tmp_path / "templates"
    tdir.mkdir()
    (tdir / "hello.html").write_text("INVENTORY HELLO")
    mod = WebModule(name="inventory", templates_dir="templates", package_dir=str(tmp_path))
    app = _app_with([mod], monkeypatch, engine)
    # render via the env directly
    import anyio
    env = app.state.jinja_env
    out = anyio.from_thread.run if False else None  # placeholder; use async test below
    assert env.get_template("inventory/hello.html") is not None
```

> Replace with an async test (the suite is async):

```python
import pytest

@pytest.mark.asyncio
async def test_web_module_templates_namespaced(tmp_path, monkeypatch, engine):
    from hearth_web.extensions import WebModule
    tdir = tmp_path / "templates"
    tdir.mkdir()
    (tdir / "hello.html").write_text("INVENTORY HELLO")
    mod = WebModule(name="inventory", templates_dir="templates", package_dir=str(tmp_path))
    app = _app_with([mod], monkeypatch, engine)
    html = await app.state.jinja_env.get_template("inventory/hello.html").render_async()
    assert html == "INVENTORY HELLO"
```

- [ ] **Step 2: Run — expect failure**

Run: `rtk proxy uv run pytest plugins/web/tests/test_web_extensions.py::test_web_module_templates_namespaced -v`
Expected: FAIL (`app.state.jinja_env` missing).

- [ ] **Step 3: Implement `build_jinja_env`**

```python
"""Jinja environment, page rendering, nav assembly, page_context dependency."""

from __future__ import annotations

import os
from pathlib import Path

from jinja2 import ChoiceLoader, Environment, FileSystemLoader, PrefixLoader, select_autoescape

from hearth_web.extensions import WebModule

_ADMIN_TEMPLATES = Path(__file__).parent / "templates"

def build_jinja_env(modules: list[WebModule], override_dir: str | None) -> Environment:
    prefix_map = {}
    for m in modules:
        if m.templates_dir and m.package_dir:
            prefix_map[m.name] = FileSystemLoader(os.path.join(m.package_dir, m.templates_dir))
    loaders = []
    if override_dir:
        loaders.append(FileSystemLoader(override_dir))
    loaders.append(FileSystemLoader(str(_ADMIN_TEMPLATES)))  # admin + base templates
    if prefix_map:
        loaders.append(PrefixLoader(prefix_map))
    env = Environment(
        loader=ChoiceLoader(loaders),
        autoescape=select_autoescape(["html"]),
        enable_async=True,
        undefined=__import__("jinja2").StrictUndefined,
    )
    return env
```

In `app.py`, after resolving modules and before `return app`:

```python
    from hearth_web.rendering import build_jinja_env
    override_dir = os.environ.get("HEARTH_WEB_TEMPLATE_DIR")
    app.state.jinja_env = build_jinja_env(resolved_modules, override_dir)
    app.state.brand = {
        "name": os.environ.get("HEARTH_WEB_BRAND_NAME", "Hearth"),
        "primary_color": os.environ.get("HEARTH_WEB_PRIMARY_COLOR"),
        "logo_url": os.environ.get("HEARTH_WEB_LOGO_URL"),
    }
```

- [ ] **Step 4: Run — expect pass**

Run: `rtk proxy uv run pytest plugins/web/tests/test_web_extensions.py::test_web_module_templates_namespaced -v`
Expected: PASS.

---

### Task 2.2: Nav assembly (merge, sort, section normalization)

**Files:**
- Modify: `plugins/web/hearth_web/rendering.py`
- Modify: `plugins/web/hearth_web/app.py`
- Test: `plugins/web/tests/test_web_slots.py`

- [ ] **Step 1: Write the failing tests**

```python
from hearth_web.extensions import NavItem, WebModule
from hearth_web.rendering import build_nav, KNOWN_SECTIONS

def test_nav_items_merge_and_order():
    a = WebModule(name="a", nav=[NavItem(label="Z", path="/a", section="operations", order=20)])
    b = WebModule(name="b", nav=[NavItem(label="A", path="/b", section="operations", order=10)])
    nav = build_nav([a, b], admin_nav=[])
    ops = [i.label for i in nav if i.section == "operations"]
    assert ops == ["A", "Z"]

def test_nav_unknown_section_falls_back(caplog):
    m = WebModule(name="a", nav=[NavItem(label="X", path="/a", section="nope")])
    nav = build_nav([m], admin_nav=[])
    assert nav[0].section == "extensions"
    assert "unknown nav section" in caplog.text
    assert "extensions" in KNOWN_SECTIONS
```

- [ ] **Step 2: Run — expect failure**

Run: `rtk proxy uv run pytest plugins/web/tests/test_web_slots.py -k nav -v`
Expected: FAIL (`ImportError: build_nav`).

- [ ] **Step 3: Implement nav assembly**

Append to `rendering.py`:

```python
import logging

from hearth_web.extensions import NavItem

logger = logging.getLogger("hearth_web")

KNOWN_SECTIONS = ("overview", "operations", "system", "extensions")

def build_nav(modules: list[WebModule], admin_nav: list[NavItem]) -> list[NavItem]:
    """Merge admin + plugin nav, normalize unknown sections to 'extensions',
    sort by (section order, item order, label)."""
    items: list[NavItem] = list(admin_nav)
    for m in modules:
        items.extend(m.nav)
    normalized: list[NavItem] = []
    for it in items:
        section = it.section or "overview"
        if section not in KNOWN_SECTIONS:
            logger.warning("unknown nav section %r on %r; placing under 'extensions'",
                           section, it.label)
            section = "extensions"
        normalized.append(NavItem(label=it.label, path=it.path, permission=it.permission,
                                  section=section, order=it.order, icon=it.icon))
    section_rank = {s: i for i, s in enumerate(KNOWN_SECTIONS)}
    normalized.sort(key=lambda i: (section_rank[i.section or "overview"], i.order, i.label))
    return normalized
```

In `app.py`, set `app.state.nav = build_nav(resolved_modules, admin_nav=ADMIN_NAV)` where `ADMIN_NAV` comes from the admin module (added in Milestone 5; for now pass `admin_nav=[]`). Import lazily to avoid a cycle:

```python
    from hearth_web.rendering import build_nav
    app.state.nav = build_nav(resolved_modules, admin_nav=[])
```

- [ ] **Step 4: Run — expect pass**

Run: `rtk proxy uv run pytest plugins/web/tests/test_web_slots.py -k nav -v`
Expected: PASS.

---

## Milestone 3 — Sessions, auth, authorization

### Task 3.1: Per-request uow + actor resolution + memoized check

**Files:**
- Create: `plugins/web/hearth_web/security.py`
- Modify: `plugins/web/hearth_web/app.py` (install `SessionMiddleware`)
- Test: `plugins/web/tests/test_web_auth.py`

- [ ] **Step 1: Write the failing tests**

These need a route that reports the resolved actor. Add a tiny debug router in the test via a module.

```python
import pytest
from fastapi import APIRouter, Depends

from hearth_web.extensions import WebModule
from hearth_web.security import current_actor

def _whoami_module() -> WebModule:
    """A diagnostic module (test-only) whose route reports the resolved actor."""
    r = APIRouter()

    @r.get("/whoami")
    async def whoami(actor=Depends(current_actor)):
        return {"kind": actor.actor_kind}

    return WebModule(name="diag", router=r)

@pytest.mark.asyncio
async def test_anonymous_when_no_session(web_factory):
    fx = await web_factory([_whoami_module()])
    assert fx.client.get("/diag/whoami").json()["kind"] == "anonymous"
```

- [ ] **Step 2: Run — expect failure**

Run: `rtk proxy uv run pytest plugins/web/tests/test_web_auth.py::test_anonymous_when_no_session -v`
Expected: FAIL (`ModuleNotFoundError: hearth_web.security`).

- [ ] **Step 3: Implement `security.py` (deps + check)**

```python
"""Request-scoped auth/authz: uow, actor resolution, memoized permission check,
route gate, exception handling."""

from __future__ import annotations

from collections.abc import AsyncGenerator, Awaitable, Callable

from fastapi import Depends, Request
from pydantic import SecretStr

from hearth import Actor, Anonymous, EntityId
from hearth.unit_of_work import UnitOfWork
from hearth_auth import AuthenticationFailed, User
from hearth_auth.actions.api_key import AuthenticateApiKey

Check = Callable[[str], Awaitable[bool]]

async def request_uow(request: Request) -> AsyncGenerator[UnitOfWork]:
    """FastAPI YIELD dependency: one transaction per request, kept open until
    the response body is sent (template rendering — slot providers, the `can`
    filter — runs at ASGI send time, AFTER the route handler returns). The
    `yield` is what makes FastAPI drive the context manager to teardown then.
    Audit actor is Anonymous in v1 (read-only path); see plan caveat. Resolve
    everywhere via `Depends(request_uow)` so FastAPI caches one uow per request."""
    factory = request.app.state.uow_factory
    async with factory(actor=Anonymous()) as uow:
        yield uow

async def current_actor(
    request: Request, uow: UnitOfWork = Depends(request_uow)
) -> Actor:
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        token = auth[len("Bearer "):].strip()
        try:
            return await AuthenticateApiKey(key_string=SecretStr(token)).handle(uow, Anonymous())
        except AuthenticationFailed:
            return Anonymous()
    raw = request.session.get("user_id")
    if raw:
        # EntityId is a str subclass; the login handler stored str(user.id).
        # A tampered/non-matching value simply matches no row (→ Anonymous),
        # so no validation/500 risk — query with the string directly.
        user = await uow.query(User).where(User.id == EntityId(raw)).one_or_none()
        if user is not None and user.is_active:
            return user
    return Anonymous()

def make_check(uow: object, actor: Actor) -> Check:
    cache: dict[str, bool] = {}

    async def check(permission: str) -> bool:
        if permission not in cache:
            cache[permission] = await actor.has_permission(uow, permission)  # type: ignore[arg-type]
        return cache[permission]

    return check

async def get_check(request: Request, uow=Depends(request_uow),
                    actor: Actor = Depends(current_actor)) -> Check:
    check = make_check(uow, actor)
    request.state.actor = actor
    request.state.uow = uow
    request.state.check = check
    return check
```

In `app.py`, install the session middleware (needs `request.session`):

```python
    from starlette.middleware.sessions import SessionMiddleware
    app.add_middleware(SessionMiddleware, secret_key=secret, same_site="lax", https_only=False)
```

- [ ] **Step 4: Run — expect pass**

Run: `rtk proxy uv run pytest plugins/web/tests/test_web_auth.py::test_anonymous_when_no_session -v`
Expected: PASS.

---

### Task 3.2: Login / logout + `next` validation

**Files:**
- Create: `plugins/web/hearth_web/sessions.py`
- Create: `plugins/web/hearth_web/templates/login.html`, `base.html`
- Modify: `plugins/web/hearth_web/app.py`
- Test: `plugins/web/tests/test_web_auth.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_login_success_sets_session(web):
    resp = web.client.post("/login",
                           data={"email": "admin@x.com", "password": "adminpass"},
                           follow_redirects=False)
    assert resp.status_code == 303
    assert "session" in resp.cookies or resp.headers.get("set-cookie")

def test_login_failure_indistinguishable(web):
    resp = web.client.post("/login",
                           data={"email": "admin@x.com", "password": "wrong"})
    assert resp.status_code == 401
    assert "invalid credentials" in resp.text.lower()

def test_login_next_open_redirect_rejected(web):
    resp = web.client.post("/login",
                           data={"email": "admin@x.com", "password": "adminpass",
                                 "next": "https://evil.example/x"},
                           follow_redirects=False)
    assert resp.headers["location"] == "/admin"

def test_logout_clears_session(web):
    web.client.post("/login", data={"email": "admin@x.com", "password": "adminpass"})
    resp = web.client.post("/logout", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"
```

(`seed_admin` defaults to `admin@x.com` / `adminpass`.) After a successful login the default redirect target is `/admin`.

- [ ] **Step 2: Run — expect failure**

Run: `rtk proxy uv run pytest plugins/web/tests/test_web_auth.py -k "login or logout" -v`
Expected: FAIL (no `/login` route).

- [ ] **Step 3: Implement `sessions.py` and minimal templates**

`sessions.py`:

```python
"""Login/logout routes and safe-redirect validation."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import SecretStr

from hearth import Anonymous
from hearth_auth import AuthenticationFailed
from hearth_auth.actions.user_auth import AuthenticateUser
from hearth_commons import EmailAddress
from hearth_web.security import request_uow

session_router = APIRouter()

def validate_next(raw: str | None) -> str:
    """Only site-local paths are honored (open-redirect defense)."""
    if raw and raw.startswith("/") and not raw.startswith("//"):
        return raw
    return "/admin"

@session_router.get("/login")
async def login_form(
    request: Request,
    next_url: Annotated[str, Query(alias="next")] = "/admin",  # alias keeps the URL param "next"
) -> HTMLResponse:
    env = request.app.state.jinja_env
    html = await env.get_template("login.html").render_async(
        request=request, brand=request.app.state.brand, next=validate_next(next_url), error=None
    )
    return HTMLResponse(html)

@session_router.post("/login")
async def login_submit(
    request: Request,
    email: Annotated[str, Form()],
    password: Annotated[str, Form()],
    next_url: Annotated[str, Form(alias="next")] = "/admin",
    uow=Depends(request_uow),
) -> HTMLResponse | RedirectResponse:
    target = validate_next(next_url)
    try:
        user = await AuthenticateUser(
            email=EmailAddress(raw=email), password=SecretStr(password)
        ).handle(uow, Anonymous())
    except AuthenticationFailed:
        env = request.app.state.jinja_env
        html = await env.get_template("login.html").render_async(
            request=request, brand=request.app.state.brand, next=target,
            error="invalid credentials",
        )
        return HTMLResponse(html, status_code=401)
    request.session["user_id"] = str(user.id)
    return RedirectResponse(target, status_code=303)

@session_router.post("/logout")
async def logout(request: Request) -> RedirectResponse:
    request.session.clear()
    return RedirectResponse("/login", status_code=303)
```

`templates/base.html` (minimal shell; expanded in Milestone 6):

```html
<!doctype html>
<html lang="en" data-theme="light">
<head>
  <meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{% block title %}{{ brand.name }}{% endblock %}</title>
  <link rel="stylesheet" href="/static/base.css">
  <script>(function(){var t=localStorage.getItem('theme');if(t)document.documentElement.dataset.theme=t;})();</script>
</head>
<body>
  <main>{% block content %}{% endblock %}</main>
  <script src="/static/vendor/htmx.min.js" defer></script>
  <script src="/static/vendor/alpine.min.js" defer></script>
</body>
</html>
```

`templates/login.html`:

```html
{% extends "base.html" %}
{% block content %}
<form method="post" action="/login">
  {% if error %}<p class="error">{{ error }}</p>{% endif %}
  <input type="hidden" name="next" value="{{ next }}">
  <label>Email <input name="email" type="email" required></label>
  <label>Password <input name="password" type="password" required></label>
  <button type="submit">Sign in</button>
</form>
{% endblock %}
```

In `app.py`, include the session router and mount base static (so `/static/base.css` resolves):

```python
    from fastapi.staticfiles import StaticFiles
    from pathlib import Path as _Path
    from hearth_web.sessions import session_router
    app.include_router(session_router)
    app.mount("/static", StaticFiles(directory=str(_Path(__file__).parent / "static")), name="static")
```

Create an empty placeholder `plugins/web/hearth_web/static/base.css` (real styles in Milestone 6) and empty `static/vendor/htmx.min.js`, `static/vendor/alpine.min.js` so mounts resolve.

> **Mount ordering:** include plugin routers and mount per-plugin `/static/<name>` BEFORE mounting the base `/static`. Reorder `create_app` so the base `/static` mount is last.

- [ ] **Step 4: Run — expect pass**

Run: `rtk proxy uv run pytest plugins/web/tests/test_web_auth.py -k "login or logout" -v`
Expected: PASS.

---

### Task 3.3: API-key auth + resolution precedence

**Files:**
- Test: `plugins/web/tests/test_web_auth.py`

- [ ] **Step 1: Write the failing tests** (reuse `_whoami_module` from Task 3.1 — it returns `actor.actor_kind`. Build an isolated app with it via `web_factory`, create an API key with `CreateApiKey`, and assert the resolved actor.)

```python
import pytest
from hearth_auth.actions import CreateApiKey

@pytest.mark.asyncio
async def test_api_key_header_authenticates(web_factory):
    fx = await web_factory([_whoami_module()])
    _api_key, plaintext = await fx.harness.run(
        CreateApiKey(name="ci", permissions=[], owner_id=fx.admin.id)
    )
    resp = fx.client.get("/diag/whoami", headers={"Authorization": f"Bearer {plaintext}"})
    # NOTE: verify ApiKey.actor_kind in plugins/auth/hearth_auth/entities/api_key.py;
    # the Actor doc and convention give multi-word kinds an underscore ("api_key").
    assert resp.json()["kind"] == "api_key"

@pytest.mark.asyncio
async def test_session_takes_precedence_after_login(web_factory):
    fx = await web_factory([_whoami_module()])
    fx.client.post("/login", data={"email": "admin@x.com", "password": "adminpass"})
    resp = fx.client.get("/diag/whoami")
    assert resp.json()["kind"] == "user"
```

- [ ] **Step 2: Run — expect failure / iterate**

Run: `rtk proxy uv run pytest plugins/web/tests/test_web_auth.py -k "api_key or precedence" -v`
Expected: FAIL initially if `actor_kind` differs — fix the assertion to the real value, not the code.

- [ ] **Step 3: Implementation** — already present in `security.py` (Task 3.1). The Bearer-before-session ordering in `current_actor` is what these assert.

- [ ] **Step 4: Run — expect pass**

Run: `rtk proxy uv run pytest plugins/web/tests/test_web_auth.py -v`
Expected: PASS.

---

### Task 3.4: `Forbidden`, `requires_permission`, exception handlers

**Files:**
- Modify: `plugins/web/hearth_web/security.py`
- Modify: `plugins/web/hearth_web/app.py`
- Test: `plugins/web/tests/test_web_authz.py`

- [ ] **Step 1: Write the failing tests**

```python
import pytest
from fastapi import APIRouter, Depends
from starlette.testclient import TestClient

from hearth_web.extensions import WebModule
from hearth_web.security import requires_permission

def _gated_module():
    r = APIRouter()

    @r.get("/secret")
    async def secret(_: None = Depends(requires_permission("widgets:read"))):
        return {"ok": True}

    return WebModule(name="diag", router=r)

# Build an ISOLATED app with the gated module (own engine — never share the
# web fixture's StaticPool engine across two apps). Seed a no-grant user too,
# so the 403 test stays fully synchronous.
@pytest_asyncio.fixture
async def gated(web_factory):
    from pydantic import SecretStr
    from hearth_auth.actions import CreateUser
    from hearth_commons import EmailAddress

    fx = await web_factory([_gated_module()])
    await fx.harness.run(CreateUser(
        email=EmailAddress(raw="nobody@x.com"), password=SecretStr("pw"),
        display_name="Nobody"))
    return fx

def test_route_gate_allows_with_permission(gated):
    # admin has *:* so widgets:read passes
    gated.client.post("/login", data={"email": "admin@x.com", "password": "adminpass"})
    assert gated.client.get("/diag/secret").status_code == 200

def test_route_gate_denies_anonymous_redirect_or_401(gated):
    r = gated.client.get("/diag/secret", headers={"accept": "text/html"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/login")
    r2 = gated.client.get("/diag/secret", headers={"HX-Request": "true"})
    assert r2.status_code == 401

def test_route_gate_denies_authenticated_403(gated):
    # a fresh user with no grants → authenticated but forbidden → 403
    gated.client.post("/login", data={"email": "nobody@x.com", "password": "pw"})
    assert gated.client.get("/diag/secret").status_code == 403
```

> For a true 403 (authenticated-but-unauthorized), seed a non-admin user with no grants and log in as them; assert `403`. Add that test using `CreateUser` + a session login for that user.

- [ ] **Step 2: Run — expect failure**

Run: `rtk proxy uv run pytest plugins/web/tests/test_web_authz.py -v`
Expected: FAIL (`ImportError: requires_permission`).

- [ ] **Step 3: Implement gate + handlers**

Append to `security.py`:

```python
from urllib.parse import quote

class Forbidden(Exception):
    def __init__(self, actor: Actor) -> None:
        self.actor = actor

def requires_permission(permission: str):
    async def dep(check: Check = Depends(get_check), actor: Actor = Depends(current_actor)) -> None:
        if not await check(permission):
            raise Forbidden(actor)

    return dep

def _wants_html(request: Request) -> bool:
    if request.headers.get("HX-Request"):
        return False
    return "text/html" in request.headers.get("accept", "")

async def forbidden_handler(request: Request, exc: Forbidden):
    from fastapi.responses import RedirectResponse, Response
    if isinstance(exc.actor, Anonymous):
        if _wants_html(request):
            return RedirectResponse(f"/login?next={quote(request.url.path)}", status_code=303)
        return Response(status_code=401)
    return Response(status_code=403)
```

In `app.py`:

```python
    from hearth_web.security import Forbidden, forbidden_handler
    app.add_exception_handler(Forbidden, forbidden_handler)
```

Add the unexpected-exception handler mirroring the CLI's behavior (clean message unless `HEARTH_DEBUG`):

```python
    import os as _os
    from fastapi.responses import Response as _Response

    @app.exception_handler(Exception)
    async def _unexpected(request, exc):  # pyright: ignore[reportUnusedFunction]
        if _os.environ.get("HEARTH_DEBUG"):
            raise exc
        return _Response(f"ERROR: unexpected {type(exc).__name__}", status_code=500)
```

> Register `Forbidden` handler is sufficient for it; the broad `Exception` handler must not swallow `Forbidden`/`HTTPException` — FastAPI dispatches the most specific handler first, so order is fine.

- [ ] **Step 4: Run — expect pass**

Run: `rtk proxy uv run pytest plugins/web/tests/test_web_authz.py -v`
Expected: PASS.

---

### Task 3.5: `can` filter (`@pass_context`, async) + `requires_permission` export

**Files:**
- Modify: `plugins/web/hearth_web/rendering.py`
- Modify: `plugins/web/hearth_web/app.py`
- Modify: `plugins/web/hearth_web/__init__.py`
- Test: `plugins/web/tests/test_web_authz.py`

- [ ] **Step 1: Write the failing test**

```python
import pytest

@pytest.mark.asyncio
async def test_can_filter_gates_component(web):
    env = web.client.app.state.jinja_env
    from hearth_web.security import make_check
    from hearth import Anonymous
    async with web.harness.transaction() as uow:
        check = make_check(uow, Anonymous())
        tmpl = env.from_string('{% if "x:write" | can %}YES{% else %}NO{% endif %}')
        out = await tmpl.render_async(check=check)
        assert out == "NO"
```

- [ ] **Step 2: Run — expect failure**

Run: `rtk proxy uv run pytest plugins/web/tests/test_web_authz.py::test_can_filter_gates_component -v`
Expected: FAIL (`No filter named 'can'`).

- [ ] **Step 3: Implement the filter and register it**

Append to `rendering.py`:

```python
from jinja2 import pass_context

@pass_context
async def can_filter(ctx, permission: str) -> bool:
    check = ctx.get("check")
    if check is None:
        return False
    return await check(permission)
```

In `build_jinja_env`, before returning: `env.filters["can"] = can_filter`.

In `__init__.py`, export the public surface:

```python
"""hearth_web: web framework + admin console for Hearth."""

from hearth_web.extensions import NavItem, SlotContribution, WebModule
from hearth_web.security import current_actor, requires_permission

__all__ = [
    "NavItem",
    "SlotContribution",
    "WebModule",
    "current_actor",
    "requires_permission",
]
```

- [ ] **Step 4: Run — expect pass**

Run: `rtk proxy uv run pytest plugins/web/tests/test_web_authz.py -v`
Expected: PASS.

- [ ] **Step 5: Add the abstractness test and run**

```python
@pytest.mark.asyncio
async def test_unknown_plugin_permission_needs_no_web_change(web):
    # brand-new permission string nobody granted → can() is False, no error.
    from hearth_web.security import make_check
    from hearth import Anonymous
    async with web.harness.transaction() as uow:
        check = make_check(uow, Anonymous())
        assert await check("totally:new") is False
```

Run: `rtk proxy uv run pytest plugins/web/tests/test_web_authz.py -v`
Expected: PASS.

---

## Milestone 4 — Slots

### Task 4.1: Slot registry + `render_slot`

**Files:**
- Create: `plugins/web/hearth_web/slots.py`
- Modify: `plugins/web/hearth_web/app.py` (build slot registry; `page_context` dep)
- Modify: `plugins/web/hearth_web/rendering.py` (`page_context`, `render`)
- Test: `plugins/web/tests/test_web_slots.py`

- [ ] **Step 1: Write the failing tests**

```python
import pytest

from hearth import Anonymous
from hearth_web.extensions import SlotContribution, WebModule
from hearth_web.slots import build_slot_registry, make_render_slot
from hearth_web.security import make_check

@pytest.mark.asyncio
async def test_slot_contribution_renders(web, tmp_path):
    # a module contributing a fragment into a slot
    tdir = tmp_path / "templates"
    tdir.mkdir()
    (tdir / "w.html").write_text("WIDGET:{{ value }}")
    contrib = SlotContribution(slot="admin.dashboard.widgets", template="diag/w.html")
    mod = WebModule(name="diag", templates_dir="templates", package_dir=str(tmp_path),
                    contributions=[contrib])
    from hearth_web.rendering import build_jinja_env
    env = build_jinja_env([mod], override_dir=None)
    registry = build_slot_registry([mod])
    async with web.harness.transaction() as uow:
        check = make_check(uow, Anonymous())
        render_slot = make_render_slot(registry, uow, Anonymous(), check, env)
        out = await render_slot("admin.dashboard.widgets", value="hi")
        assert "WIDGET:hi" in out

@pytest.mark.asyncio
async def test_slot_render_failure_isolated(web, tmp_path, caplog):
    tdir = tmp_path / "templates"; tdir.mkdir()
    (tdir / "ok.html").write_text("OK")

    async def boom(uow, actor, **ctx):
        raise RuntimeError("provider down")

    good = SlotContribution(slot="s", template="diag/ok.html", order=2)
    bad = SlotContribution(slot="s", template="diag/ok.html", provider=boom, order=1)
    mod = WebModule(name="diag", templates_dir="templates", package_dir=str(tmp_path),
                    contributions=[good, bad])
    from hearth_web.rendering import build_jinja_env
    env = build_jinja_env([mod], override_dir=None)
    registry = build_slot_registry([mod])
    async with web.harness.transaction() as uow:
        check = make_check(uow, Anonymous())
        rs = make_render_slot(registry, uow, Anonymous(), check, env)
        out = await rs("s")
        assert "OK" in out  # good one survives
        assert "provider down" in caplog.text
```

- [ ] **Step 2: Run — expect failure**

Run: `rtk proxy uv run pytest plugins/web/tests/test_web_slots.py -k slot -v`
Expected: FAIL (`ModuleNotFoundError: hearth_web.slots`).

- [ ] **Step 3: Implement `slots.py`**

```python
"""Slot registry and async render_slot."""

from __future__ import annotations

import logging

from jinja2 import Environment
from markupsafe import Markup

from hearth import Actor
from hearth_web.extensions import SlotContribution, WebModule

logger = logging.getLogger("hearth_web")

def build_slot_registry(modules: list[WebModule]) -> dict[str, list[SlotContribution]]:
    reg: dict[str, list[SlotContribution]] = {}
    for m in modules:
        for c in m.contributions:
            reg.setdefault(c.slot, []).append(c)
    for slot in reg:
        reg[slot].sort(key=lambda c: c.order)
    return reg

def make_render_slot(registry, uow, actor: Actor, check, env: Environment):
    async def render_slot(name: str, **ctx) -> Markup:
        parts: list[str] = []
        for contrib in registry.get(name, []):
            if contrib.permission is not None and not await check(contrib.permission):
                continue
            try:
                data = await contrib.provider(uow, actor, **ctx) if contrib.provider else {}
                fragment_ctx = {
                    **ctx, **data,
                    "check": check, "current_actor": actor, "render_slot": render_slot,
                }
                html = await env.get_template(contrib.template).render_async(fragment_ctx)
            except Exception as exc:  # noqa: BLE001 — isolation is the contract
                logger.warning("slot %r contribution %r failed: %s: %s",
                               name, contrib.template, type(exc).__name__, exc)
                continue
            parts.append(html)
        return Markup("".join(parts))

    return render_slot
```

- [ ] **Step 4: Run — expect pass**

Run: `rtk proxy uv run pytest plugins/web/tests/test_web_slots.py -k slot -v`
Expected: PASS.

---

### Task 4.2: `page_context` dependency + `render()` helper

**Files:**
- Modify: `plugins/web/hearth_web/rendering.py`
- Modify: `plugins/web/hearth_web/app.py`

- [ ] **Step 1: Write the failing test** (a real page that renders a slot end-to-end through the request lifecycle — deferred to the admin dashboard test in Milestone 5; here add a focused test using a diag module with a slot-rendering page).

```python
import pytest
from fastapi import APIRouter, Depends, Request
from starlette.testclient import TestClient

from hearth_web.extensions import SlotContribution, WebModule
from hearth_web.rendering import page_context, render
from hearth_web.security import requires_permission

@pytest.mark.asyncio
async def test_page_renders_slot_through_request(web, tmp_path, monkeypatch):
    tdir = tmp_path / "templates"; tdir.mkdir()
    (tdir / "host.html").write_text("HOST[{{ render_slot('s') }}]")
    (tdir / "frag.html").write_text("FRAG")
    r = APIRouter()

    @r.get("/host")
    async def host(request: Request, _: None = Depends(page_context)):
        return await render(request, "diag/host.html")

    mod = WebModule(
        name="diag", router=r, templates_dir="templates", package_dir=str(tmp_path),
        contributions=[SlotContribution(slot="s", template="diag/frag.html")],
    )
    monkeypatch.setenv("HEARTH_WEB_SECRET_KEY", "test-secret-key")
    from hearth_web.app import create_app
    from conftest import make_registry
    app = create_app(engine=web.harness._engine, registry=make_registry(), modules=[mod])  # pyright: ignore[reportPrivateUsage]
    with TestClient(app) as c:
        assert c.get("/diag/host").text == "HOST[FRAG]"
```

- [ ] **Step 2: Run — expect failure**

Run: `rtk proxy uv run pytest plugins/web/tests/test_web_slots.py::test_page_renders_slot_through_request -v`
Expected: FAIL (`ImportError: page_context`).

- [ ] **Step 3: Implement `page_context` and `render`**

Append to `rendering.py`:

```python
from fastapi import Depends, Request
from fastapi.responses import HTMLResponse

from hearth_web.security import Check, current_actor, get_check, request_uow
from hearth_web.slots import make_render_slot

async def page_context(request: Request, check: Check = Depends(get_check)) -> None:
    actor = request.state.actor
    uow = request.state.uow
    env = request.app.state.jinja_env
    render_slot = make_render_slot(request.app.state.slots, uow, actor, check, env)
    request.state.render_slot = render_slot
    nav = []
    for item in request.app.state.nav:
        if item.permission is None or await check(item.permission):
            nav.append(item)
    request.state.nav = nav

async def render(request: Request, template: str, *, status_code: int = 200, **ctx) -> HTMLResponse:
    env = request.app.state.jinja_env
    base = {
        "request": request,
        "current_actor": request.state.actor,
        "check": request.state.check,
        "render_slot": request.state.render_slot,
        "nav": request.state.nav,
        "brand": request.app.state.brand,
    }
    html = await env.get_template(template).render_async({**base, **ctx})
    return HTMLResponse(html, status_code=status_code)
```

In `app.py`, build the slot registry into state (before nav is fine):

```python
    from hearth_web.slots import build_slot_registry
    app.state.slots = build_slot_registry(resolved_modules)
```

- [ ] **Step 4: Run — expect pass**

Run: `rtk proxy uv run pytest plugins/web/tests/test_web_slots.py::test_page_renders_slot_through_request -v`
Expected: PASS.

---

### Task 4.3: Slot permission gating + ordering + nav-section tests

**Files:**
- Test: `plugins/web/tests/test_web_slots.py`

- [ ] **Step 1: Write the remaining slot tests** (ordering, permission gate, provider data):

```python
@pytest.mark.asyncio
async def test_slot_contributions_ordered(web, tmp_path):
    tdir = tmp_path / "templates"; tdir.mkdir()
    (tdir / "a.html").write_text("A"); (tdir / "b.html").write_text("B")
    from hearth import Anonymous
    from hearth_web.rendering import build_jinja_env
    mod = WebModule(name="diag", templates_dir="templates", package_dir=str(tmp_path),
                    contributions=[SlotContribution(slot="s", template="diag/b.html", order=2),
                                   SlotContribution(slot="s", template="diag/a.html", order=1)])
    env = build_jinja_env([mod], override_dir=None)
    reg = build_slot_registry([mod])
    async with web.harness.transaction() as uow:
        from hearth_web.security import make_check
        rs = make_render_slot(reg, uow, Anonymous(), make_check(uow, Anonymous()), env)
        assert await rs("s") == "AB"

@pytest.mark.asyncio
async def test_slot_contribution_permission_gated(web, tmp_path):
    tdir = tmp_path / "templates"; tdir.mkdir()
    (tdir / "a.html").write_text("SECRET")
    from hearth import Anonymous
    from hearth_web.rendering import build_jinja_env
    mod = WebModule(name="diag", templates_dir="templates", package_dir=str(tmp_path),
                    contributions=[SlotContribution(slot="s", template="diag/a.html",
                                                    permission="x:write")])
    env = build_jinja_env([mod], override_dir=None)
    reg = build_slot_registry([mod])
    async with web.harness.transaction() as uow:
        from hearth_web.security import make_check
        rs = make_render_slot(reg, uow, Anonymous(), make_check(uow, Anonymous()), env)
        assert await rs("s") == ""  # anonymous lacks x:write
```

- [ ] **Step 2: Run — expect pass** (implementation already supports these)

Run: `rtk proxy uv run pytest plugins/web/tests/test_web_slots.py -v`
Expected: PASS.

---

## Milestone 5 — Admin console

### Task 5.1: Registry introspection helpers + mermaid

**Files:**
- Create: `plugins/web/hearth_web/admin/introspection.py`
- Test: `plugins/web/tests/test_web_admin.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_counts_from_registry(web):
    from hearth_web.admin.introspection import counts
    from conftest import make_registry
    c = counts(make_registry())
    assert c["plugins"] == 1
    assert c["entities"] >= 1  # auth ships User, Role, etc.

def test_mermaid_includes_tables(web):
    from hearth_web.admin.introspection import schema_mermaid
    from conftest import make_registry
    text = schema_mermaid(make_registry())
    assert text.startswith("erDiagram") or text.startswith("graph")
    assert "users" in text.lower() or "user" in text.lower()
```

- [ ] **Step 2: Run — expect failure**

Run: `rtk proxy uv run pytest plugins/web/tests/test_web_admin.py -k "counts or mermaid" -v`
Expected: FAIL.

- [ ] **Step 3: Implement `introspection.py`**

```python
"""Registry → view-model helpers for the admin console (read-only)."""

from __future__ import annotations

from typing import Any

from hearth.kernel.registry import Registry

def counts(registry: Registry) -> dict[str, int]:
    plugins = registry.plugins.values()
    return {
        "plugins": len(registry.plugins),
        "entities": sum(len(p.entities) for p in plugins),
        "actions": sum(len(p.actions) for p in plugins),
        "events": sum(len(p.events) for p in plugins),
    }

def entity_fields(entity_cls: type) -> list[dict[str, Any]]:
    """Columns + types + FK targets from the mapped table."""
    table = getattr(entity_cls, "__table__", None)
    if table is None:
        return []
    out = []
    for col in table.columns:
        fks = [fk.target_fullname for fk in col.foreign_keys]
        out.append({"name": col.name, "type": str(col.type), "nullable": col.nullable,
                    "references": fks})
    return out

def schema_mermaid(registry: Registry) -> str:
    """Build a mermaid ER diagram from entity tables + FKs across all plugins."""
    lines = ["erDiagram"]
    edges: list[str] = []
    for info in registry.plugins.values():
        for ent in info.entities:
            table = getattr(ent, "__table__", None)
            if table is None:
                continue
            lines.append(f"  {table.name} {{")
            for col in table.columns:
                lines.append(f"    {str(col.type).split('(')[0]} {col.name}")
            lines.append("  }")
            for col in table.columns:
                for fk in col.foreign_keys:
                    target = fk.column.table.name
                    edges.append(f"  {table.name} }}o--|| {target} : {col.name}")
    return "\n".join(lines + edges)
```

- [ ] **Step 4: Run — expect pass**

Run: `rtk proxy uv run pytest plugins/web/tests/test_web_admin.py -k "counts or mermaid" -v`
Expected: PASS.

---

### Task 5.2: Admin router + pages + templates

**Files:**
- Create: `plugins/web/hearth_web/admin/router.py`
- Create: admin templates (`templates/admin/*.html`, `templates/_partials/nav.html`)
- Modify: `plugins/web/hearth_web/app.py` (mount admin, set `ADMIN_NAV`, `/` redirect)
- Test: `plugins/web/tests/test_web_admin.py`

- [ ] **Step 1: Write the failing tests** (these require login as admin first)

```python
def _login(web):
    web.client.post("/login", data={"email": "admin@x.com", "password": "adminpass"})

def test_root_redirects_to_admin(web):
    r = web.client.get("/", follow_redirects=False)
    assert r.status_code in (303, 307) and r.headers["location"] == "/admin"

def test_dashboard_renders_counts(web):
    _login(web)
    r = web.client.get("/admin")
    assert r.status_code == 200
    assert "Plugins" in r.text

def test_plugins_page_lists_registry(web):
    _login(web)
    r = web.client.get("/admin/plugins")
    assert r.status_code == 200 and "auth" in r.text

def test_entity_detail_shows_fields(web):
    _login(web)
    r = web.client.get("/admin/entities/auth/User")
    assert r.status_code == 200 and "email" in r.text

def test_schema_page_renders_graph(web):
    _login(web)
    r = web.client.get("/admin/schema")
    assert r.status_code == 200 and "erDiagram" in r.text

def test_admin_requires_auth(web):
    r = web.client.get("/admin", headers={"accept": "text/html"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/login")
```

- [ ] **Step 2: Run — expect failure**

Run: `rtk proxy uv run pytest plugins/web/tests/test_web_admin.py -k "dashboard or plugins_page or entity_detail or schema or root_redirects or requires_auth" -v`
Expected: FAIL.

- [ ] **Step 3: Implement the admin router**

```python
"""The built-in read-only admin console (/admin)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from hearth_web.admin.introspection import counts, entity_fields, schema_mermaid
from hearth_web.extensions import NavItem
from hearth_web.rendering import page_context, render
from hearth_web.security import requires_permission

ADMIN_NAV = [
    NavItem(label="Dashboard", path="/admin", section="overview", order=0),
    NavItem(label="Plugins", path="/admin/plugins", section="system", order=10),
    NavItem(label="Entities", path="/admin/entities", section="system", order=20),
    NavItem(label="Actions", path="/admin/actions", section="system", order=30),
    NavItem(label="Events", path="/admin/events", section="system", order=40),
    NavItem(label="Schema", path="/admin/schema", section="system", order=50),
    NavItem(label="Database", path="/admin/db", section="system", order=60),
]

admin_router = APIRouter(
    prefix="/admin",
    dependencies=[Depends(requires_permission("admin:read")), Depends(page_context)],
)

@admin_router.get("")
async def dashboard(request: Request):
    return await render(request, "admin/dashboard.html",
                        counts=counts(request.app.state.registry))

@admin_router.get("/plugins")
async def plugins(request: Request):
    return await render(request, "admin/plugins.html",
                        plugins=list(request.app.state.registry.plugins.values()))

@admin_router.get("/plugins/{alias}")
async def plugin_detail(request: Request, alias: str):
    info = request.app.state.registry.plugins.get(alias)
    return await render(request, "admin/plugin_detail.html", plugin=info, alias=alias)

@admin_router.get("/entities")
async def entities(request: Request):
    return await render(request, "admin/entities.html",
                        plugins=list(request.app.state.registry.plugins.values()))

@admin_router.get("/entities/{alias}/{name}")
async def entity_detail(request: Request, alias: str, name: str):
    info = request.app.state.registry.plugins.get(alias)
    entity = next((e for e in (info.entities if info else []) if e.__name__ == name), None)
    fields = entity_fields(entity) if entity else []
    return await render(request, "admin/entity_detail.html",
                        alias=alias, name=name, fields=fields)

@admin_router.get("/actions")
async def actions(request: Request):
    return await render(request, "admin/actions.html",
                        plugins=list(request.app.state.registry.plugins.values()))

@admin_router.get("/events")
async def events(request: Request):
    return await render(request, "admin/events.html",
                        plugins=list(request.app.state.registry.plugins.values()))

@admin_router.get("/schema")
async def schema(request: Request):
    return await render(request, "admin/schema.html",
                        mermaid=schema_mermaid(request.app.state.registry))

@admin_router.get("/db")
async def db(request: Request):
    return await render(request, "admin/db.html")
```

- [ ] **Step 4: Implement admin templates** (concrete, minimal; styling in Milestone 6). Each extends `base.html` and reuses `_partials/nav.html`.

`templates/_partials/nav.html`:

```html
<nav>
  {% for section, items in nav | groupby('section') %}
    <div class="nav-section"><h3>{{ section }}</h3>
      {% for item in items %}
        <a href="{{ item.path }}">{{ item.label }}</a>
      {% endfor %}
    </div>
  {% endfor %}
</nav>
```

`templates/admin/dashboard.html`:

```html
{% extends "base.html" %}
{% block content %}
{% include "_partials/nav.html" %}
<section>
  <h1>{{ brand.name }}</h1>
  <ul class="counts">
    <li>Plugins: {{ counts.plugins }}</li>
    <li>Entities: {{ counts.entities }}</li>
    <li>Actions: {{ counts.actions }}</li>
    <li>Events: {{ counts.events }}</li>
  </ul>
  <div class="widgets">{{ render_slot("admin.dashboard.widgets") }}</div>
</section>
{% endblock %}
```

`templates/admin/plugins.html`:

```html
{% extends "base.html" %}
{% block content %}{% include "_partials/nav.html" %}
<h1>Plugins</h1>
<table><tr><th>Alias</th><th>Version</th><th>Depends</th><th>Extends</th></tr>
{% for p in plugins %}
<tr><td><a href="/admin/plugins/{{ p.alias }}">{{ p.alias }}</a></td>
    <td>{{ p.version }}</td><td>{{ p.depends_on | join(", ") }}</td>
    <td>{{ p.extends | join(", ") }}</td></tr>
{% endfor %}</table>
{% endblock %}
```

`templates/admin/plugin_detail.html`:

```html
{% extends "base.html" %}{% block content %}{% include "_partials/nav.html" %}
{% if plugin %}
<h1>{{ plugin.alias }}</h1>
<p>{{ plugin.package }} {{ plugin.version }}</p>
<h2>Entities</h2><ul>{% for e in plugin.entities %}
  <li><a href="/admin/entities/{{ plugin.alias }}/{{ e.__name__ }}">{{ e.__name__ }}</a></li>{% endfor %}</ul>
<h2>Actions</h2><ul>{% for a in plugin.actions %}<li>{{ a.__name__ }}</li>{% endfor %}</ul>
<h2>Events</h2><ul>{% for v in plugin.events %}<li>{{ v.__name__ }}</li>{% endfor %}</ul>
{% else %}<p>Unknown plugin {{ alias }}</p>{% endif %}{% endblock %}
```

`templates/admin/entities.html`:

```html
{% extends "base.html" %}{% block content %}{% include "_partials/nav.html" %}
<h1>Entities</h1>{% for p in plugins %}{% for e in p.entities %}
<a href="/admin/entities/{{ p.alias }}/{{ e.__name__ }}">{{ p.alias }}.{{ e.__name__ }}</a><br>
{% endfor %}{% endfor %}{% endblock %}
```

`templates/admin/entity_detail.html`:

```html
{% extends "base.html" %}{% block content %}{% include "_partials/nav.html" %}
<h1>{{ alias }}.{{ name }}</h1>
<table><tr><th>Field</th><th>Type</th><th>Nullable</th><th>References</th></tr>
{% for f in fields %}<tr><td>{{ f.name }}</td><td>{{ f.type }}</td>
<td>{{ f.nullable }}</td><td>{{ f.references | join(", ") }}</td></tr>{% endfor %}</table>
{% block panels %}{{ render_slot("admin.entity.detail.panels", alias=alias, name=name) }}{% endblock %}
{% endblock %}
```

`templates/admin/actions.html` and `events.html` (analogous list of `__name__` + Pydantic fields):

```html
{% extends "base.html" %}{% block content %}{% include "_partials/nav.html" %}
<h1>Actions</h1>{% for p in plugins %}{% for a in p.actions %}
<details><summary>{{ p.alias }}.{{ a.__name__ }}</summary>
<ul>{% for fname in a.model_fields %}<li>{{ fname }}</li>{% endfor %}</ul></details>
{% endfor %}{% endfor %}{% endblock %}
```

(`events.html`: same but `p.events`.)

`templates/admin/schema.html`:

```html
{% extends "base.html" %}{% block content %}{% include "_partials/nav.html" %}
<h1>Schema</h1>
<pre class="mermaid">{{ mermaid }}</pre>
<script type="module">
  import mermaid from "/static/vendor/mermaid.min.js";
  mermaid.initialize({ startOnLoad: true });
</script>{% endblock %}
```

> Vendor `mermaid.min.js` into `static/vendor/` (Milestone 6). The test only asserts the `erDiagram` text is present in the response, so it passes without the JS.

`templates/admin/db.html`:

```html
{% extends "base.html" %}{% block content %}{% include "_partials/nav.html" %}
<h1>Database</h1><p>Kernel outbox table: <code>_hearth_outbox</code></p>{% endblock %}
```

- [ ] **Step 5: Mount admin + `/` redirect in `app.py`**

```python
    from fastapi.responses import RedirectResponse
    from hearth_web.admin.router import ADMIN_NAV, admin_router

    @app.get("/")
    async def _root() -> RedirectResponse:  # pyright: ignore[reportUnusedFunction]
        return RedirectResponse("/admin", status_code=303)

    app.include_router(admin_router)
```

Change the nav line to seed admin nav: `app.state.nav = build_nav(resolved_modules, admin_nav=ADMIN_NAV)`.

- [ ] **Step 6: Run — expect pass**

Run: `rtk proxy uv run pytest plugins/web/tests/test_web_admin.py -v`
Expected: PASS.

---

### Task 5.3: Nav permission filtering + slot end-to-end through admin

**Files:**
- Test: `plugins/web/tests/test_web_authz.py`, `plugins/web/tests/test_web_slots.py`

- [ ] **Step 1: Add tests** for nav hidden without permission, and a dashboard widget contributed by a fake module showing through `/admin`.

```python
def test_nav_hidden_without_permission(web):
    # Add a plugin nav item gated on a permission the admin lacks? Admin has *:*,
    # so instead assert a gated nav item appears for admin and is absent for a
    # fresh no-grant user. Build a module with a gated nav item.
    ...
```

> Implement using a second app built with a `WebModule` whose `NavItem(permission="nobody:has")`; log in as a non-admin user (create via `CreateUser`); assert the label is absent. For admin (`*:*`) assert present.

- [ ] **Step 2-4:** Run, iterate, expect pass.

Run: `rtk proxy uv run pytest plugins/web/tests/test_web_authz.py plugins/web/tests/test_web_slots.py -v`

---

## Milestone 6 — Theme, assets, polish

### Task 6.1: Vendored assets + base.css with theme variables + toggle

**Files:**
- Replace placeholder: `static/base.css`, `static/theme-toggle.js`
- Add: `static/vendor/htmx.min.js`, `static/vendor/alpine.min.js`, `static/vendor/mermaid.min.js`
- Modify: `templates/base.html` (shell, theme toggle, branding, primary color)

- [ ] **Step 1: Vendor the JS libraries**

Run (downloads pinned versions into the vendor dir):
```bash
mkdir -p plugins/web/hearth_web/static/vendor
rtk proxy curl -sL https://unpkg.com/htmx.org@2.0.3/dist/htmx.min.js -o plugins/web/hearth_web/static/vendor/htmx.min.js
rtk proxy curl -sL https://unpkg.com/alpinejs@3.14.1/dist/cdn.min.js -o plugins/web/hearth_web/static/vendor/alpine.min.js
rtk proxy curl -sL https://unpkg.com/mermaid@11/dist/mermaid.esm.min.mjs -o plugins/web/hearth_web/static/vendor/mermaid.min.js
```
Expected: three non-empty files.

- [ ] **Step 2: Write `base.css`** with CSS custom properties and a dark theme block. Keep it real but compact:

```css
:root{--color-bg:#fff;--color-fg:#1a1a1a;--color-muted:#666;--color-accent:#2563eb;
  --color-border:#e5e7eb;--space:1rem;--radius:8px;font-family:system-ui,sans-serif}
:root[data-theme="dark"]{--color-bg:#0f1115;--color-fg:#e6e6e6;--color-muted:#9aa0aa;
  --color-border:#262b33}
*{box-sizing:border-box}body{margin:0;background:var(--color-bg);color:var(--color-fg)}
a{color:var(--color-accent)}main{max-width:1100px;margin:0 auto;padding:var(--space)}
nav .nav-section h3{color:var(--color-muted);font-size:.75rem;text-transform:uppercase}
table{border-collapse:collapse;width:100%}td,th{border:1px solid var(--color-border);
  padding:.4rem .6rem;text-align:left}.error{color:#dc2626}
.counts{display:flex;gap:var(--space);list-style:none;padding:0}
.widgets{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:var(--space)}
```

- [ ] **Step 3: Write `theme-toggle.js`**

```javascript
function toggleTheme(){
  const el=document.documentElement;
  const next=el.dataset.theme==='dark'?'light':'dark';
  el.dataset.theme=next; localStorage.setItem('theme',next);
}
window.toggleTheme=toggleTheme;
```

- [ ] **Step 4: Expand `base.html`** to include the toggle button, branding (logo + name), primary-color override, and the toggle script:

```html
<!doctype html>
<html lang="en" data-theme="light">
<head>
  <meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{% block title %}{{ brand.name }}{% endblock %}</title>
  <link rel="stylesheet" href="/static/base.css">
  {% if brand.primary_color %}<style>:root{--color-accent:{{ brand.primary_color }}}</style>{% endif %}
  <script>(function(){var t=localStorage.getItem('theme');if(t)document.documentElement.dataset.theme=t;})();</script>
</head>
<body>
  <header>
    {% if brand.logo_url %}<img src="{{ brand.logo_url }}" alt="" height="28">{% endif %}
    <strong>{{ brand.name }}</strong>
    <button type="button" onclick="toggleTheme()">◐</button>
    {% if current_actor is defined and current_actor.actor_kind != 'anonymous' %}
      <form method="post" action="/logout" style="display:inline"><button>Sign out</button></form>
    {% endif %}
  </header>
  <main>{% block content %}{% endblock %}</main>
  <script src="/static/theme-toggle.js"></script>
  <script src="/static/vendor/htmx.min.js" defer></script>
  <script src="/static/vendor/alpine.min.js" defer></script>
</body>
</html>
```

- [ ] **Step 5: Run the full suite**

Run: `rtk proxy uv run pytest plugins/web/tests/ -v`
Expected: PASS (templates still render; assertions unchanged).

> **Optional polish task:** after the suite is green, the frontend-design skill may be used to refine `base.css` and the shell layout. Keep structure (nav, content, slots) and the template/context contract stable.

---

## Milestone 7 — CLI serve & docs

### Task 7.1: `hearth web serve` command

**Files:**
- Create: `plugins/web/hearth_web/cli.py`
- Test: `plugins/web/tests/test_web_cli.py`

- [ ] **Step 1: Write the failing tests**

```python
from typer.testing import CliRunner

from hearth_web.cli import web_app

runner = CliRunner()

def test_create_app_builds_clean(monkeypatch):
    monkeypatch.setenv("HEARTH_WEB_SECRET_KEY", "k")
    from hearth.kernel._engine import make_async_engine  # pyright: ignore[reportPrivateUsage]
    from conftest import make_registry
    from hearth_web.app import create_app
    app = create_app(engine=make_async_engine("sqlite:///:memory:"),
                     registry=make_registry(), modules=[])
    assert app.title == "Hearth"

def test_serve_missing_secret_key_exits(monkeypatch):
    monkeypatch.delenv("HEARTH_WEB_SECRET_KEY", raising=False)
    monkeypatch.setenv("DATABASE_URL", "sqlite:///./x.db")
    result = runner.invoke(web_app, ["serve"])
    assert result.exit_code != 0
    assert "HEARTH_WEB_SECRET_KEY" in result.output
```

- [ ] **Step 2: Run — expect failure**

Run: `rtk proxy uv run pytest plugins/web/tests/test_web_cli.py -v`
Expected: FAIL (`ModuleNotFoundError: hearth_web.cli`).

- [ ] **Step 3: Implement `cli.py`**

```python
"""hearth web ... CLI subcommands. Registered via the `hearth.cli` entry point."""

from __future__ import annotations

import os
from typing import Annotated

import typer

web_app = typer.Typer(help="Serve the Hearth web console.", no_args_is_help=True)

@web_app.callback()
def _root() -> None:  # pyright: ignore[reportUnusedFunction]
    """hearth-web: browser console and plugin web framework."""

@web_app.command("serve")
def serve(
    host: Annotated[str, typer.Option(help="Bind host.")] = "127.0.0.1",
    port: Annotated[int, typer.Option(help="Bind port.")] = 8000,
    reload: Annotated[bool, typer.Option(help="Auto-reload (dev only).")] = False,
) -> None:
    """Run the web console with uvicorn."""
    if not os.environ.get("HEARTH_WEB_SECRET_KEY"):
        typer.echo("ERROR: HEARTH_WEB_SECRET_KEY environment variable is required.", err=True)
        raise typer.Exit(1)
    if not os.environ.get("DATABASE_URL"):
        typer.echo("ERROR: DATABASE_URL environment variable is required.", err=True)
        raise typer.Exit(1)
    import uvicorn

    uvicorn.run("hearth_web.app:create_app", factory=True, host=host, port=port, reload=reload)
```

- [ ] **Step 4: Run — expect pass**

Run: `rtk proxy uv run pytest plugins/web/tests/test_web_cli.py -v`
Expected: PASS.

- [ ] **Step 5: Verify CLI registration end-to-end**

Run: `rtk proxy uv run hearth web --help`
Expected: shows the `serve` command (proves the `hearth.cli` entry point loads).

---

### Task 7.2: Plugin reference doc

**Files:**
- Create: `docs/core/plugins/web.md`

- [ ] **Step 1: Write `docs/core/plugins/web.md`** mirroring `docs/core/plugins/auth.md`. Cover: install (`pip install hearth[web]`), what it provides, the `WebModule`/`NavItem`/`SlotContribution` contract with a worked example, slots + `render_slot`, authz model (`requires_permission` route gate and the `can` filter), the **naming caution** (`requires_permission` vs auth's `require_permission`/`@requires`), required env vars, theming/override, and `hearth web serve`. Include a "Build your own actor route" note pointing at `current_actor`. No placeholders — write the full reference.

- [ ] **Step 2: Verify no broken internal links**

Run: `rtk grep "\]\(\.\./" docs/core/plugins/web.md`
Expected: relative links resolve (compare against `auth.md`).

---

### Task 7.3: Full-suite green + lint/type pass

**Files:** none (verification)

- [ ] **Step 1: Run the whole web suite (both backends if DATABASE_URL set)**

Run: `rtk proxy uv run pytest plugins/web/tests/ -v`
Expected: all PASS.

- [ ] **Step 2: Run the kernel + auth suites to confirm no regressions**

Run: `rtk proxy uv run pytest -q`
Expected: all PASS (kernel must still pass with zero plugins; web is additive).

- [ ] **Step 3: Lint + type check**

Run: `rtk proxy uv run ruff check plugins/web && rtk proxy uv run basedpyright plugins/web`
Expected: clean (fix any findings; match the repo's strictness).

- [ ] **Step 4: Apply any lint/type fixups** until steps 1–3 are all clean.

> **Commits:** this plan intentionally contains **no commit steps** (per project rules, commit grouping is the user's call against the live tree). Group and commit by logical concern when the user authorizes it.

---

## Spec coverage check (self-review)

- **§1 plugin shape / no hearth.plugins entry** → Task 0.1. ✓
- **§2 hearth.web discovery, reserved/dedup/isolation, router/static/templates mount, PrefixLoader** → Tasks 1.1–1.3, 2.1. ✓
- **§3 slots: render_slot, SlotContribution, nav sections, failure isolation, symmetric** → Tasks 2.2, 4.1–4.3. ✓
- **§4 sessions, api-key + session resolution order, next validation, env vars** → Tasks 3.1–3.3. ✓
- **§5 abstract authz, memoized _check, requires_permission, can filter, 401/403/redirect, fail-closed** → Tasks 3.1, 3.4, 3.5. ✓
- **§6 admin pages (dashboard/plugins/entities/actions/events/schema/db), `/`→`/admin`, mermaid self-built, write-ready** → Tasks 5.1–5.3. ✓
- **§7 theme: CSS vars, light/dark, override via ChoiceLoader, branding env** → Tasks 2.1, 6.1. ✓
- **§8 hearth web serve** → Task 7.1. ✓
- **§9 create_app assembly order (env validate, session mw, exception handlers, jinja, admin+modules, static last, context globals)** → Tasks 0.3, 2.1, 3.1, 3.4, 4.2, 5.2. ✓
- **§10 vendored htmx/alpine, no CDN** → Task 6.1. ✓
- **§11 SDK exports + naming caution doc** → Tasks 3.5, 7.2. ✓
- **Tests** mirror the spec's test tables across the six files. ✓

**Known deferral carried from spec:** generic write surface, runtime-editable config entity, OAuth/MFA, deeper nav nesting — out of scope; foundation is built write-ready (Task 5.2 note).

**Implementer caveats flagged inline:** (1) request uow audit-actor is `Anonymous` in v1 (revisit with write surface); (2) `WebModule.package_dir` should be set explicitly by templated/static plugins — keep the field, prefer explicit over the fragile auto-derivation; (3) some tests sketch a fake-`Request`/diag-module approach — prefer the async `web` fixture pattern from conftest where possible.
