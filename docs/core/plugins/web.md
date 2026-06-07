# Plugin: hearth-web

> **Source package**: `plugins/web/hearth_web/`
> **Install**: `pip install hearth[web]`
> **Status**: v0.0.1 (first release; tracks the 2026-06-05 spec).

## What hearth-web provides

A FastAPI + Jinja2 (async) + HTMX presentation plugin that serves a read-only admin console, owns session/API-key auth transport, and lets other plugins add *and extend* pages, nav, and in-page slots — all served via `hearth web serve`.

The plugin contributes no entities, actions, or events to the kernel registry (no `hearth.plugins` entry point). It is a presentation layer only, depending on `hearth` (kernel) and `hearth-auth` (authz primitives).

## SDK surface

```python
from hearth_web import (
    # Plugin contribution objects
    WebModule, NavItem, SlotContribution,
    # App factory (for tests / embedding)
    create_app,
    # Auth transport (rare — transport layers only)
    current_actor,
    # Route-level authz gate
    requires_permission,
)
```

For test fixtures: `from hearth_web.tests.conftest import WebFixture, make_registry, web_factory`.

## Plugin contribution contract (`WebModule`)

Plugins expose their web surface by declaring a `WebModule` in the `hearth.web` entry point group. The module specifies:

- **`router`** — FastAPI `APIRouter` mounted at `/<module.name>`
- **`templates_dir`** — relative path to Jinja templates (namespaced under `module.name/`)
- **`static_dir`** — relative path to static files (served at `/static/<module.name>/`)
- **`nav`** — `NavItem` list for sidebar navigation
- **`contributions`** — `SlotContribution` list for in-page fragment injection
- **`package_dir`** — **required** when using templates/static: `os.path.dirname(__file__)`

### Worked example

```python
# myplugin/web.py
from fastapi import APIRouter
from hearth_web import WebModule, NavItem, SlotContribution

router = APIRouter()

@router.get("/dashboard")
async def dashboard():
    return {"widgets": ["a", "b"]}

module = WebModule(
    name="inventory",
    router=router,
    templates_dir="templates",
    static_dir="static",
    package_dir=os.path.dirname(__file__),
    nav=[
        NavItem(label="Stock", path="/inventory/dashboard", permission="inventory:read",
                section="operations", order=10, icon="box"),
    ],
    contributions=[
        SlotContribution(
            slot="admin.dashboard.widgets",
            template="inventory/widget.html",
            permission="inventory:read",
            order=5,
            provider=lambda uow, actor, **_: {"count": 42},
        ),
    ],
)
```

Entry point declaration in `pyproject.toml`:

```toml
[project.entry-points."hearth.web"]
inventory = "myplugin.web:module"
```

Reserved module names (will be rejected with a warning): `admin`, `login`, `logout`, `static`, `health`, empty string.

## Slots + `render_slot`

Slots are named insertion points in templates. A plugin contributes a `SlotContribution`:

```python
SlotContribution(
    slot="admin.dashboard.widgets",   # target slot name
    template="inventory/widget.html", # template path (namespaced)
    provider=lambda uow, actor, **ctx: {"data": ...},  # optional async data
    permission="inventory:read",      # optional — gated by authz
    order=5,                          # lower renders first
)
```

In a template, invoke the async `render_slot` function:

```jinja
<div class="widgets">
  {{ render_slot("admin.dashboard.widgets", extra="context") }}
</div>
```

The `render_slot` function:
- **Permission gates** contributions — skipped if `check(permission)` is false
- **Injects provider data** into the fragment context
- **Passes `check`, `current_actor`, and `render_slot`** recursively to fragments
- **Isolates failures** — one broken provider logs a warning and continues
- **Guards recursion** — max depth 10 per slot name

### Built-in slots

| Slot | Location | Purpose |
|------|----------|---------|
| `admin.dashboard.widgets` | Admin dashboard | Plugin dashboard widgets |
| `admin.entity.detail.panels` | Entity detail page | Extra panels per entity |

## Authz model

hearth-web provides **two enforcement surfaces**:

### 1. Route gate — `requires_permission`

```python
from fastapi import APIRouter, Depends
from hearth_web import requires_permission

router = APIRouter()

@router.get("/secret")
async def secret(_: None = Depends(requires_permission("widgets:read"))):
    return {"widgets": ["a", "b"]}
```

Behavior:
- **Anonymous** → 303 redirect to `/login?next=...` (HTML) or 401 (HTMX)
- **Authenticated, no permission** → 403
- **Has permission** → proceeds

### 2. Template filter — `can`

```jinja
{% if "widgets:write" | can %}
  <button>Edit</button>
{% endif %}
```

The `can` filter reads the memoized `check` predicate from the render context.

### Naming caution

| Package | Function | Purpose |
|---------|----------|---------|
| `hearth_web` | `requires_permission` | **FastAPI dependency** for routes |
| `hearth_auth` | `require_permission` | **Action helper** for action handlers |
| `hearth_auth` | `@requires` | **Decorator** for action classes |

Do not confuse `hearth_web.requires_permission` (transport) with `hearth_auth.require_permission` (domain).

### Abstract permission strings

Permissions are opaque `<resource>:<action>` strings validated against the actor's `has_permission`. The kernel has no permission registry — plugins declare what they need; `hearth-auth` stores grants. Unknown permissions fail closed (`False`).

## Environment variables

| Variable | Required | Default | Purpose |
|----------|----------|---------|---------|
| `HEARTH_WEB_SECRET_KEY` | **yes** | — | Session + CSRF signing key |
| `DATABASE_URL` | **yes** | — | SQLAlchemy async engine URL |
| `HEARTH_WEB_BRAND_NAME` | no | `Hearth` | Brand name in header/title |
| `HEARTH_WEB_PRIMARY_COLOR` | no | — | CSS `--color-accent` override |
| `HEARTH_WEB_LOGO_URL` | no | — | Logo image in header |
| `HEARTH_WEB_TEMPLATE_DIR` | no | — | Override directory for templates (ChoiceLoader priority) |
| `HEARTH_DEBUG` | no | — | Re-raise exceptions instead of 500 page |

## Theming & template override

- **Light/dark theme**: CSS custom properties on `:root` and `:root[data-theme="dark"]`. Persisted in `localStorage`, applied on load.
- **Primary color**: `--color-accent` overridden by `HEARTH_WEB_PRIMARY_COLOR`.
- **Template override**: Set `HEARTH_WEB_TEMPLATE_DIR=/path/to/templates`. The override directory is checked **first** (via `ChoiceLoader`), then built-in admin templates, then plugin templates. Example: override `admin/dashboard.html` to customize the dashboard.

## CLI: `hearth web serve`

```bash
hearth web serve --host 0.0.0.0 --port 8000 --reload
```

Options:
- `--host` — bind host (default: `127.0.0.1`)
- `--port` — bind port (default: `8000`)
- `--reload` — auto-reload for development (default: `false`)

Requires `HEARTH_WEB_SECRET_KEY` and `DATABASE_URL` in environment.

## Build your own actor route

Need to read the current actor in a route? Use the `current_actor` dependency:

```python
from fastapi import APIRouter, Depends
from hearth_web import current_actor

router = APIRouter()

@router.get("/whoami")
async def whoami(actor=Depends(current_actor)):
    return {"kind": actor.actor_kind, "id": getattr(actor, "id", None)}
```

Actor kinds: `anonymous`, `user`, `api_key`, `system`. The actor is resolved per-request (Bearer token first, then session).

## Admin console

The built-in `/admin` console (requires `admin:read` permission) provides:

- **Dashboard** — registry counts, plugin widget slot
- **Plugins** — list with versions, dependencies, extensions; detail pages
- **Entities** — cross-plugin entity listing with field types, nullability, FK references
- **Actions** — action listing with Pydantic fields
- **Events** — event listing with Pydantic fields
- **Schema** — mermaid ER diagram generated from registry tables + FKs
- **Database** — kernel outbox table info

All pages share the nav sidebar (filtered by permission) and extend `base.html` for consistent branding/theme.

## Testing

Use the provided test fixtures:

```python
from hearth_web.tests.conftest import web, web_factory, make_registry

# Shared app (for simple tests)
def test_something(web):
    ...

# Isolated app per test (for custom modules)
async def test_with_module(web_factory):
    fx = await web_factory([my_module])
    ...
```

Both fixtures share an in-memory SQLite engine with `StaticPool` between `BaseHarness` (table creation/seeding) and `create_app`. The `web_factory` creates fully isolated engines to avoid StaticPool deadlocks.