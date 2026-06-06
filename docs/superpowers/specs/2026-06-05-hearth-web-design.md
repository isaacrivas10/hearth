# Spec: `hearth-web` — Web Framework & Admin Plugin

> **Date**: 2026-06-05
> **Status**: Draft (brainstorming complete; implementation plan next)
> **Parent ADRs**: [ADR-0001 (vision)](../../architecture/0001-vision.md),
> [ADR-0003 (plugin model)](../../architecture/0003-plugin-model.md),
> [ADR-0004 (SDK-first)](../../architecture/0004-sdk-first.md),
> [ADR-0008 (built-in plugins)](../../architecture/0008-built-in-plugins.md).
> **Depends on**: [hearth-auth](../../core/plugins/auth.md),
> [Plugin Registry & CLI](2026-05-14-registry-cli-design.md).

## Purpose

Ship the first-party way to put a Hearth instance on the web. `hearth-web` is a
presentation plugin that:

- Serves a browser application over HTTP, started from the `hearth` CLI.
- Ships a built-in **read-only admin console** that surfaces what the instance
  *is* — installed plugins, entities and their fields, actions, events, the
  schema graph, and database status — with **no domain code required**.
- Provides an **extension contract** so other plugins (POS, inventory,
  storefront, …) add their own pages and navigation, *and extend existing pages
  and navigation*, in the same application — mirroring how plugins already append
  commands to the CLI.
- Owns **session and authentication transport**, which `hearth-auth`
  deliberately does not provide.

It is the foundation for ship-with-little-to-no-code web services on Hearth,
and the reference for operators who would rather build their own front end.

### The "Airflow webserver" analogy, and where it diverges

Like Airflow's webserver, `hearth-web` is an optional process that renders the
state of the system and is extended by plugins. Unlike Airflow, the extension
path is the *primary* design constraint, not an afterthought: a plugin
contributes a page with a router and a few templates — no compiled JS bundle,
no module federation. (Airflow's move to a React UI made its plugin-view story
harder; that is the failure mode this spec exists to avoid.)

## Scope

**In:**

- A new plugin package `plugins/web/hearth_web/`, installed via
  `pip install hearth[web]`, hard-depending on `hearth-auth`.
- A FastAPI application built by an app factory, served by `hearth web serve`.
- Server-rendered UI: **FastAPI + Jinja2 (async mode) + HTMX** (+ Alpine.js for
  local interactivity). No SPA, no build step.
- The `hearth.web` entry-point group: plugin-contributed pages, navigation,
  templates, and static assets, discovered at startup.
- A **slot** mechanism: plugins extend *existing* pages and nav sections
  (inject a widget, a panel, a nav item) without editing core.
- Session + authentication transport: signed cookie sessions for the browser,
  **and** API-key header authentication for programmatic callers.
- Abstract, permission-driven access control covering pages, nav items, slots,
  and in-page components (buttons, tabs, views) — with **zero hard-coded
  knowledge of any specific permission**.
- The built-in read-only admin console (plugins, entities, actions, events,
  schema, db status), architected so a future write surface is purely additive.
- A polished default theme with light/dark mode, fully overridable; no enforced
  palette.

**Out (deferred, designed to land additively):**

- Generic write surface — invoking arbitrary actions / editing arbitrary
  entities through the UI (a Django-admin-sized follow-up spec). The foundation
  is built write-ready, but no write pages ship in this slice.
- Stored, runtime-editable web configuration. `hearth-web`'s own presentation
  config is **env-driven** in this slice (config-as-code, consistent with
  ADR-0002 "the image is the instance's identity"); it ships **no config
  entity** and stays out of the schema registry. Runtime-editable web settings
  land with the write surface (at which point web would gain a typed config
  entity as a conscious, documented step). *Other plugins'* config is each
  plugin's own entity — never `hearth-web`'s concern; a plugin surfaces its
  settings by contributing a page/slot that reads its own config entity.
- A standalone first-class JSON API as a separate product. "Build your own"
  means *not running `hearth-web`* and using the kernel SDK directly; the kernel
  already runs headless without it (ADR-0001). We do not commit to maintaining a
  second public surface.
- OAuth / OTP / magic-link / MFA login (these are `hearth-auth` concerns; web
  consumes whatever authentication actions auth exposes).
- Real-time/websocket features, i18n machinery, and a plugin asset build
  pipeline.

## Design

### 1. Plugin shape & dependencies

```text
plugins/web/
  pyproject.toml
  hearth_web/
    __init__.py        # public SDK surface (see §11)
    app.py             # create_app(): build FastAPI, discover extensions, mount
    extensions.py      # WebModule, NavItem, SlotContribution, hearth.web discovery
    slots.py           # slot registry + render_slot
    sessions.py        # SessionMiddleware config, login / logout handlers
    security.py        # current_actor, can(), requires_permission dependencies
    cli.py             # web_app Typer group: serve
    admin/             # the built-in read-only admin (consumes the same contract it hosts)
      router.py
      ...
    templates/         # base shell + admin templates
    static/            # theming CSS variables, htmx.min.js, alpine.min.js, base.css
```

`pyproject.toml`:

```toml
[project]
name = "hearth-web"
dependencies = [
    "hearth",
    "hearth-auth",            # hard dependency: web's authz reads the permissions table
    "fastapi>=0.115",
    "uvicorn>=0.30",
    "jinja2>=3.1",
    "itsdangerous>=2.2",      # cookie session signing
    "python-multipart>=0.0.9",# form posts
]

[project.entry-points."hearth.cli"]
web = "hearth_web.cli:web_app"

[tool.uv.sources]
hearth = { workspace = true }
hearth-auth = { workspace = true }
```

`hearth-web` **contributes no entities, actions, or events**, so — exactly like
`hearth-commons` — it does **not** register in the `hearth.plugins` entry-point
group, and the schema registry stays schema-only. `hearth-web` *introspects* the
registry; it never appears in it. It *hosts* the `hearth.web` group and
*contributes* one `hearth.cli` group.

Root `pyproject.toml` gains a `web = ["hearth-web"]` extra (per ADR-0008,
extras are the manifest).

### 2. Extension contract — the `hearth.web` entry-point group

A direct structural mirror of the existing `hearth.cli` loader
(`hearth/cli/__init__.py:_register_plugin_cli_groups`). A contributing plugin
declares:

```toml
[project.entry-points."hearth.web"]
inventory = "hearth_inventory.web:pages"
```

The entry-point **name** is the contribution's mount namespace; the **value**
resolves to a `WebModule`:

```python
from hearth_web import WebModule, NavItem
from fastapi import APIRouter

router = APIRouter()

@router.get("/stock")
async def stock(...): ...

pages = WebModule(
    name="inventory",
    router=router,                # mounted under /inventory
    templates_dir="templates",    # folded into the Jinja2 loader as inventory/*
    static_dir="static",          # optional; mounted at /static/inventory
    nav=[NavItem(label="Stock", path="/inventory/stock",
                 section="operations",        # land inside an existing nav section (§3)
                 permission="inventory:read", order=100)],
    contributions=[...],          # extend existing pages/nav via slots (§3)
)
```

A web contribution carries what the CLI's bare Typer app does not:

| Carried | What `create_app()` does with it |
| --- | --- |
| `router: APIRouter` | `app.include_router(router, prefix="/<name>")` |
| `templates_dir` | folded into the Jinja2 loader under a `<name>/` prefix (see below) |
| `static_dir` (optional) | `app.mount("/static/<name>", StaticFiles(...))` |
| `nav: list[NavItem]` | merged into the global nav, ordered, permission-gated |
| `contributions: list[SlotContribution]` | registered into the slot registry (§3) |

`NavItem` fields: `label`, `path`, `permission: str | None` (visibility gate;
`None` = always visible to authenticated users), `order: int`,
`section: str | None` (target nav section; see §3), `icon: str | None`.

`NavItem.path` is an **absolute** path the author keeps in sync with their
router prefix + route (`/inventory` + `/stock` → `/inventory/stock`). This is a
deliberate, simple tradeoff: drift means a nav link 404s. (A relative
route-local form composed by the framework is the ergonomic alternative if drift
proves painful; not adopted in v1.)

**Template namespacing requires a `PrefixLoader`, not bare `ChoiceLoader`.**
`ChoiceLoader` only does try-in-order lookup — two plugins each shipping
`index.html` would silently collide. The Jinja2 environment is therefore
`ChoiceLoader([operator_override_loader?, admin_loader, PrefixLoader({name:
FileSystemLoader(plugin_dir) for each module})])`. The `PrefixLoader` gives each
plugin its own `<name>/...` namespace; the outer `ChoiceLoader` gives the
operator-override directory first-match precedence (§7).

#### Discovery discipline (identical to the CLI loader)

`create_app()` iterates `importlib.metadata.entry_points(group="hearth.web")`
and, for each:

- **Reserved namespaces** (`""`/root, `admin`, `login`, `logout`, `static`,
  `health`) are refused with a warning. The admin owns root (`/`, which
  redirects to `/admin`) and the whole `/admin/*` subtree; everything else is
  open to plugins.
- **Duplicate names** are refused with a warning (first wins; no silent
  overwrite).
- **Load/mount failure is isolated** — a broken module logs
  `WARNING: failed to load web module 'X': ...` and is skipped. A bad plugin
  never crashes the server or hides the admin.

This makes the admin itself just the *first consumer* of the contract it hosts
(its pages, nav, and slots are registered through the same internal path), so
the contract is exercised from day one rather than privileged.

### 3. Extending existing pages & navigation — slots

§2 lets a plugin add *whole new pages* and *top-level nav*. Plugins also need to
extend *existing* surfaces — inject a widget into the dashboard, a panel into an
entity page, an item inside an existing nav section — without editing core. This
is the "host advertises extension points, plugins satisfy them" pattern
(Airflow/VSCode); the registry-CLI spec deferred it, and this spec builds it for
the web.

**A slot is a named region a host template exposes:**

```html
{# admin/dashboard.html #}
<div class="widgets">{{ render_slot("admin.dashboard.widgets") }}</div>
```

**A `SlotContribution` is a fragment a plugin targets at a slot**, declared on
its `WebModule.contributions`:

```python
from hearth_web import SlotContribution

SlotContribution(
    slot="admin.dashboard.widgets",
    template="inventory/widgets/low_stock.html",  # namespaced partial (§2)
    provider=low_stock_widget,    # optional async (uow, actor, **ctx) -> dict
    permission="inventory:read",  # gated via the same predicate as everything (§5)
    order=50,
)
```

`render_slot(name, **ctx)` is an **async** Jinja global (auto-awaited in `{{ }}`
in async mode, §9). It:

1. looks up every contribution registered for `name`, across all modules, sorted
   by `order`;
2. filters by the permission predicate (§5); `permission=None` = visible to any
   authenticated actor;
3. awaits `provider(uow, actor, **ctx)` if present → a data dict;
4. renders the contribution's namespaced template with `{**ctx, **data}` plus the
   standard context (`can`, `current_actor`);
5. concatenates the fragments and returns them marked safe.

**Render failures are isolated** — a contribution that raises (in its provider or
template) logs a warning and is skipped; the rest of the slot and the page still
render. Same disciplines as §2: ordered, permission-gated, failure-isolated.

**uow lifetime.** Providers run *during template rendering*, which with Starlette
`TemplateResponse` happens at ASGI send time — **after** the route handler
coroutine returns. The request `uow` must therefore be a FastAPI **yield
dependency** (`async def get_uow(): async with hearth.scope(...) as uow: yield
uow`), which FastAPI tears down only after the response body is sent, not when
the handler returns. (Opening the uow with `async with` *inside the handler body*
would close it before render and hand providers a dead session.) This is the
same request-scoped uow `_check`/`can` and write pages use (§5, §6).

**Symmetric by design.** A *plugin's own* page can advertise slots too — it just
calls `render_slot("inventory.stock.row_actions", item=item)` in its template,
and other plugins contribute to that name. The admin is simply the
best-documented host: it publishes a **stable starter set** of slots as its
extension contract (names firm up in implementation):

| Slot | Where / context passed |
| --- | --- |
| `admin.nav.<section>` | a named nav section (see below) |
| `admin.dashboard.widgets` | dashboard cards |
| `admin.entity.detail.panels` | extra panels on an entity page (`entity` in ctx) |
| `admin.header.actions` | top-bar buttons |

**Nav is a shallow tree of sections.** `NavItem.section` names the section to
nest under (e.g. `"operations"`). The admin defines the baseline sections; a
plugin targets one to add an item inside it without touching core. An unknown or
missing section name **falls back to a default "Extensions" section with a
warning** — it degrades visibly, never silently vanishes. One level of
sectioning in v1; deeper nesting is deferred.

A lazy/expensive widget needs no new machinery: its contribution template can be
a bare `<div hx-get="/inventory/widgets/low-stock">` stub pointing at one of the
plugin's own routes, loaded after first paint by HTMX.

### 4. Sessions & authentication

`hearth-auth` returns entities and explicitly owns no session/token state; that
transport is this plugin's job.

**Per-request actor resolution order** (a FastAPI dependency, `current_actor`):

1. `Authorization: Bearer <key>` header → `AuthenticateApiKey(<key>)` →
   `ApiKey` actor. (First-class: any plugin route can rely on it. The `Bearer`
   scheme is the wire format; the token is the raw API key string.)
2. Else session cookie `user_id` → load the `User` entity in the request uow →
   `User` actor.
3. Else **anonymous** (no actor).

**Browser session:** Starlette `SessionMiddleware`, signed with
`HEARTH_WEB_SECRET_KEY` (required env var; `create_app()` refuses to start
without it). HTTP-only, `SameSite=Lax`, `Secure` when served over TLS.

**Login flow:**

- `GET /login` renders the form (carrying the `next` param through).
- `POST /login` calls the `AuthenticateUser` action; on success stores
  `user_id` in the session and redirects to `next` (validated, below); on
  failure re-renders with the single indistinguishable error auth already
  guarantees.
- `POST /logout` clears the session.

**`next` redirect is validated against open-redirect**: it is honored only if it
is a site-local path (starts with a single `/`, no scheme, no `//` authority);
otherwise it falls back to `/admin` (the post-login home).

**Unauthenticated (anonymous) handling** is content-negotiated: HTML navigations
redirect to `/login?next=...`; HTMX / `fetch` / API requests get `401`. This is
distinct from the authenticated-but-unauthorized case, which is `403` (§5).

Required env / config:

| Var | Meaning | Required |
| --- | --- | --- |
| `DATABASE_URL` | kernel DB (same var the CLI uses) | yes |
| `HEARTH_WEB_SECRET_KEY` | cookie signing secret | yes |
| `HEARTH_WEB_BRAND_NAME` | shell title (default `"Hearth"`) | no |
| `HEARTH_WEB_PRIMARY_COLOR` | theme accent (CSS color) | no |
| `HEARTH_WEB_LOGO_URL` | shell logo | no |

### 5. Authorization — abstract and permission-table-driven

The framework holds **zero knowledge of any specific permission**, the same way
the registry holds zero knowledge of any specific plugin. It does not enumerate,
seed, or special-case permissions. It only ever *validates a declared
permission string against the current actor*, reading the live permissions table
through `hearth-auth`'s public surface. A new permission introduced by any
plugin — built-in or third-party — requires **no change to `hearth-web`**.

Four call sites (route, nav, slot, component), **one mechanism**. All resolve
through a single request-scoped predicate, `_check(permission)`, which awaits the
actor's `has_permission(uow, permission)` (the auth Actor contract, including
wildcard semantics) and **memoizes the boolean per distinct permission string**
for the life of the request. The web layer does **not** delegate to auth's
`require_permission` raiser — doing so would bypass this cache — it calls
`has_permission` itself and raises its own web-level error. (Note the naming
proximity, called out in §11: auth's `require_permission` is a raising coroutine;
web's `requires_permission` is a FastAPI dependency factory.)

- **Route gate** — `requires_permission("admin:read")` dependency → `_check`; on
  `False` raises a web `Forbidden`. The exception handler (§9) translates it by
  actor state: **anonymous** → redirect to `/login?next=` for HTML, `401` for
  HTMX/fetch; **authenticated but lacking the permission** → `403` (a logged-in
  user is not bounced to login).
- **Nav gate** — `NavItem.permission` renders the item only if `_check` is true.
- **Slot gate** — `SlotContribution.permission` renders the fragment only if
  `_check` is true (§3).
- **Component gate** — an async `can` **filter** registered on the Jinja
  environment, for buttons, tabs, views, table rows. Async filters are
  auto-awaited by Jinja in async mode (§9), so templates read naturally:

  ```html
  {% if "users:write" | can %}
    <button hx-post="/admin/users/new">New user</button>
  {% endif %}
  ```

  The filter is registered once at startup but must read **per-request** state
  (`_check`, which closes over the request `uow` and `actor`). It is therefore
  decorated `@jinja2.pass_context`: the per-request `_check` is placed in the
  template context (alongside `render_slot`, §9 step 8) and the filter retrieves
  it from the passed context. A bare startup-registered filter cannot see
  request state and is wrong here.

A page gating a handful of distinct permissions costs a handful of checks
regardless of how many buttons reference them. No grant enumeration, no reach
into auth internals — just the public predicate, cached.

**Unknown / not-yet-created permissions fail closed automatically.**
`has_permission` returns `False` when no granted row matches the requested
string, and `permission_grant_exists` also returns `False` for malformed strings
(wrong colon count, empty segments). A permission *check* never raises for an
unknown permission — `PermissionNotFound` is raised only by `CreateApiKey` at
key-creation time, never on the read path. So `"brand:new" | can` is simply
`False` and a gated route denies (403/redirect); `_check` needs no special-casing
beyond memoizing the boolean. This is what makes
`test_unknown_plugin_permission_needs_no_web_change` pass: a brand-new permission
string gates correctly with zero framework change.

Anonymous actors hold no permissions; `_check` is `False` for every string.

### 6. Built-in admin console (read-only)

All admin pages require login and the `admin:read` permission (the bootstrap
admin's `*:*` satisfies it; an operator grants `admin:read` to others through
the normal auth Action surface — the framework neither defines nor seeds it
beyond declaring the string its own pages require).

| Path | Surfaces | Source |
| --- | --- | --- |
| `/` | redirects to `/admin` | — |
| `/admin` | dashboard: counts (plugins / entities / actions / events), db status, outbox depth | `Registry` + DB |
| `/admin/plugins`, `/admin/plugins/{alias}` | mirror `hearth plugins list` / `show`: deps, extends, contributed primitives | `Registry` |
| `/admin/entities`, `/admin/entities/{plugin}/{name}` | fields, types, references | `Registry` + `__table__` |
| `/admin/actions`, `/admin/events` | class lists with their Pydantic schemas | `Registry` |
| `/admin/schema` | schema graph, mermaid in-browser | `Registry` (web builds its own mermaid; see note) |
| `/admin/db` | kernel tables present/missing, outbox | DB introspection |
| `/health` | liveness (no auth) | — |

The admin owns root (`/`) and the entire `/admin/*` subtree — both reserved in
§2 — so a future write page (e.g. *Create User* at `/admin/users/new`) stays
inside the admin's namespace without colliding with plugin mounts.

The admin reads **metadata** — the registry and DB schema — not domain data. It
deliberately does **not** use the `View` primitive (still a stub), keeping this
slice decoupled from that open spec.

**Schema graph note.** The registry-CLI spec ships only text rendering for
`hearth db graph`; `--format=mermaid` is deferred there. So the schema page
**does not** depend on a CLI mermaid renderer — it walks the registry's FK data
(the same `Entity.__table__.foreign_keys` the registry already resolves) and
emits mermaid itself, client-side-rendered. No kernel/CLI change is required,
keeping the "kernel unchanged" claim in the File-level summary true.

**Why the detail pages are in scope** (not deferred with the write surface):
viewing entities, fields, actions, events, and the schema *is* the explicit
out-of-the-box requirement — "view Hearth details such as table entities,
actions, events, values, or any existing primitive." These pages are the
admin's reason to exist, not optional polish; only *mutation* is deferred.

#### Built write-ready

The read-only scope ships the full machinery a write page needs, so adding one
later is additive, not structural:

- `current_actor` + the **request-scoped `uow` yield dependency** (§3) are
  already wired for every request.
- The Action-invocation shape is the same one `hearth auth bootstrap` uses —
  `await SomeAction(...).handle(uow, actor)` — except a web route receives the
  `uow` from the yield dependency rather than opening its own `async with
  hearth.scope(...)`.
- Component gating via the `can` filter already exists for write affordances.

A future *Create User* page is then just: a gated route that depends on `uow` and
`current_actor`, renders a form, and on POST calls `CreateUser(...).handle(uow,
actor)`. No new framework concept.

### 7. Theme & customization

One fixed shell structure (top bar + nav + content); the *look* is free.

- **Polished, modern default theme** shipped out of the box: clean layout,
  sensible typography, accessible spacing, responsive.
- **Light / dark mode**: `prefers-color-scheme` by default, with a persisted
  toggle (localStorage; `data-theme` on `<html>`). No flash-of-wrong-theme
  (inline head script sets the attribute before paint).
- **All colors are CSS custom properties** (`--color-bg`, `--color-fg`,
  `--color-accent`, …) with neutral defaults; `HEARTH_WEB_PRIMARY_COLOR`
  overrides the accent, branding env vars set name/logo.
- **Deep override** via the Jinja `ChoiceLoader`: an operator template directory
  (configurable path) takes precedence over the shipped templates, so any
  template — including the base shell — can be replaced without forking. The
  *structure* (nav, content slot, the `can`/`current_actor`/`render_slot`
  context) stays stable; the markup and styling are replaceable.

### 8. CLI serving

`hearth-web` contributes a `hearth.cli` group, exactly like `hearth-auth`:

```toml
[project.entry-points."hearth.cli"]
web = "hearth_web.cli:web_app"
```

```text
hearth web serve [--host 127.0.0.1] [--port 8000] [--reload]
```

`serve` runs uvicorn against `hearth_web.app:create_app` (import string form so
`--reload` works). It is the dev / single-process path; production guidance
(gunicorn with uvicorn workers behind a reverse proxy, TLS, secret management)
is documented, not wrapped. A `hearth web routes` debug command (lists mounted
routers, nav, and slots) is optional and may be dropped if it earns nothing.

### 9. Application assembly (`create_app()`)

1. Read & validate required env (`DATABASE_URL`, `HEARTH_WEB_SECRET_KEY`); raise
   a clear startup error if either is missing.
2. Build the FastAPI app; install `SessionMiddleware`.
3. Register exception handlers: web `Forbidden` → content-negotiated
   `401`/redirect (anonymous) or `403` (authenticated), per §4/§5; and a
   top-level handler rendering unexpected errors as a clean page unless
   `HEARTH_DEBUG=1` (mirrors the CLI's handler).
4. Build the shared Jinja2 environment **in async mode** (`enable_async=True`, so
   `render_slot` and the `can` filter are awaited during rendering):
   `ChoiceLoader([operator_override_loader?, admin_loader, PrefixLoader({name:
   FileSystemLoader(dir)})])` (§2). Register the async `can` filter.
5. Register the admin router, nav, and slots (through the same internal path
   plugin modules use).
6. Discover `hearth.web` entry points; for each `WebModule`, mount its router,
   fold its templates, mount its static dir, merge its nav, and register its slot
   contributions — under the discovery discipline of §2.
7. Mount `/static/<name>` per module, then `/static` for the shipped base assets
   (more-specific mounts first).
8. Inject request-scoped globals into the template context: `current_actor`,
   `render_slot`, resolved nav (already permission-filtered), branding. (`can` is
   a registered filter from step 4.)

The registry is built once at startup (per ADR-0003, no hot-reload) and shared
read-only with the admin.

### 10. Stack additions

FastAPI, uvicorn, Jinja2, itsdangerous, python-multipart. HTMX and Alpine.js are
**vendored as static files** (no CDN dependency — single-tenant, operator-
controlled, may run air-gapped).

### 11. Public SDK surface

```python
from hearth_web import (
    WebModule,            # plugin page-contribution object
    NavItem,              # a navigation entry (label, path, permission, order, section, icon)
    SlotContribution,     # a fragment targeting a named slot in an existing page
    current_actor,        # FastAPI dependency → resolved Actor (or anonymous)
    requires_permission,  # FastAPI dependency factory → route gate
    # `can` (filter) and `render_slot` (global) are injected into the template
    # environment, not imported by plugin authors
)
```

Plugin authors building web pages import only these. They never touch
`create_app`, the session machinery, the slot registry, or auth internals.

**Naming caution:** web's `requires_permission` (FastAPI dependency factory)
sits one `s` away from auth's `require_permission` (raising coroutine) and
auth's `@requires` (action decorator). The plugin reference
(`docs/core/plugins/web.md`) must call out the distinction prominently, since a
web-page author typically has both modules imported.

## File-level summary

### New

- `plugins/web/pyproject.toml`
- `plugins/web/hearth_web/{__init__,app,extensions,slots,sessions,security,cli}.py`
- `plugins/web/hearth_web/admin/` (router + page handlers)
- `plugins/web/hearth_web/templates/` (base shell, login, admin pages)
- `plugins/web/hearth_web/static/` (base.css with theme vars, theme-toggle,
  vendored htmx + alpine)
- `docs/core/plugins/web.md` (plugin reference, mirroring `auth.md`)
- `tests/` for the plugin (see Tests)

### Modified

- Root `pyproject.toml`: add `web = ["hearth-web"]` extra; add `hearth-web` to
  the uv workspace members.

### Unchanged (deliberately)

- The kernel. `hearth-web` is built entirely on existing public surfaces:
  `Registry`, `hearth.scope`, the auth actions and `require_permission`, the
  `hearth.cli` and (new-to-callers, existing-as-pattern) entry-point mechanism.
  If the plugin cannot be built without a kernel change, that change is a
  separate SDK gap to surface — not folded silently into this work.

## Tests

`tests/` using FastAPI `TestClient` + the kernel `Harness` + `seed_admin` /
`grant_permissions` from `hearth_auth.testing`.

### Extension contract — `tests/test_web_extensions.py`

| Test | Validates |
| --- | --- |
| `test_web_module_mounts_router` | a contributed router's route responds under `/<name>` |
| `test_web_module_templates_namespaced` | a contributed template renders via `<name>/...` |
| `test_web_module_static_mounted` | static asset served at `/static/<name>` |
| `test_nav_items_merge_and_order` | nav from multiple modules merges in `order` |
| `test_reserved_namespace_refused` | a module named `login` is skipped with a warning |
| `test_duplicate_name_refused` | second module with same name skipped, first wins |
| `test_broken_module_isolated` | a module that raises on load is skipped; admin still serves |

### Slots & nav sections — `tests/test_web_slots.py`

| Test | Validates |
| --- | --- |
| `test_slot_contribution_renders` | a contribution appears in its host slot |
| `test_slot_contributions_ordered` | multiple contributions render in `order` |
| `test_slot_provider_supplies_data` | `provider` data reaches the fragment template |
| `test_slot_contribution_permission_gated` | a contribution is hidden when `can` is false |
| `test_slot_render_failure_isolated` | a raising contribution is skipped; slot/page still render |
| `test_nav_item_targets_section` | a `section`-tagged item nests under that section |
| `test_nav_unknown_section_falls_back` | unknown section → default "Extensions" + warning |

### Auth & sessions — `tests/test_web_auth.py`

| Test | Validates |
| --- | --- |
| `test_login_success_sets_session` | valid creds → redirect + session cookie |
| `test_login_failure_indistinguishable` | bad creds → re-render, single error |
| `test_login_next_open_redirect_rejected` | non-local `next` falls back to `/` |
| `test_logout_clears_session` | session removed |
| `test_api_key_header_authenticates` | `Authorization: Bearer` key → ApiKey actor on a route |
| `test_session_takes_precedence_after_login` | resolution order honored |
| `test_unauthenticated_html_redirects` | HTML nav → `/login?next=` |
| `test_unauthenticated_htmx_401` | HX-Request / fetch → 401 |

### Authorization — `tests/test_web_authz.py`

| Test | Validates |
| --- | --- |
| `test_route_gate_allows_with_permission` | actor with `admin:read` reaches page |
| `test_route_gate_denies_authenticated_403` | authenticated actor lacking it → 403 (not login) |
| `test_wildcard_admin_passes_all_gates` | `*:*` satisfies every gate |
| `test_can_filter_gates_component` | the `can` filter toggles rendered markup by permission |
| `test_nav_hidden_without_permission` | gated nav item absent for actor lacking it |
| `test_permission_check_memoized_per_request` | N references to one permission → one check |
| `test_unknown_plugin_permission_needs_no_web_change` | a freshly-created permission string gates correctly with no framework change |

### Admin console — `tests/test_web_admin.py`

| Test | Validates |
| --- | --- |
| `test_root_redirects_to_admin` | `/` → `/admin` |
| `test_dashboard_renders_counts` | counts reflect the registry |
| `test_plugins_page_lists_registry` | installed plugins shown |
| `test_entity_detail_shows_fields` | fields/types/references rendered |
| `test_actions_events_pages_render` | schemas surfaced |
| `test_schema_page_renders_graph` | graph output present |
| `test_health_no_auth` | `/health` reachable unauthenticated |

### CLI — `tests/test_web_cli.py`

| Test | Validates |
| --- | --- |
| `test_serve_missing_secret_key_exits` | clear error when `HEARTH_WEB_SECRET_KEY` unset |
| `test_create_app_builds_clean` | factory builds with a fake plugin installed |

## Decisions log (locked in brainstorming)

| Decision | Rationale |
| --- | --- |
| Single plugin (`hearth-web`), admin baked in — not split into web + admin | No real case for the framework without the admin; pure plumbing users build their own. (Option A) |
| Server-rendered FastAPI + Jinja2 (async) + HTMX; no SPA | The discriminating constraint is *cheap multi-plugin page composition*. A contribution is a router + templates, no JS build. SPA / Python-UI frameworks make extension worse. |
| `hearth.web` entry-point group mirrors `hearth.cli` | Reuse the proven discovery/dedup/isolation discipline already in the CLI loader. |
| Slots for extending existing pages/nav; `render_slot` + `SlotContribution` | Plugins must extend, not just add, surfaces — the "host advertises extension points" pattern — without editing core. |
| Nav is a shallow section tree; unknown section falls back visibly | Plugins add items inside existing sections; silent disappearance is the failure mode to avoid. |
| Jinja async mode; `can` is an async filter, `render_slot` an async global | Permission checks and slot providers both hit the DB (async); async rendering avoids a separate pre-pass and keeps templates natural. |
| Web owns sessions; auth owns only the authenticate actions | `hearth-auth` explicitly provides no session/token state. |
| API-key header auth is first-class, not deferred | A plugin may require programmatic access through the web server. |
| Authorization is abstract — validate declared strings against the permissions table, never enumerate/seed/special-case | Same dynamic-discovery spirit as the registry; new permissions need no web change. |
| One predicate for route / nav / slot / component gating | Consistency; component-level gating (buttons/tabs/views) was a hard requirement. |
| Per-request memoized permission checks | Component/slot gating implies many checks per render; memoize per distinct permission. |
| Read-only admin now, built write-ready | Matches the repo's vertical-slice discipline; write surface is a clean follow-up. |
| Web config is env-driven; no config entity in this slice (B1) | Config-as-code matches ADR-0002 image-as-identity; runtime-editable config lands with the write surface. Other plugins' config is their own entity. |
| Polished default theme + light/dark, fully overridable; no enforced palette | Good out of the box without dictating brand; CSS vars + ChoiceLoader override. |
| HTMX/Alpine vendored, no CDN | Single-tenant, operator-controlled, possibly air-gapped. |
| `hearth-web` stays out of the `hearth.plugins` registry | Contributes no schema; like `commons`, it is presentation/library, not a schema-bearing plugin. |

## Deferred concerns (captured for future specs)

1. **Generic write surface** — invoke any action / edit any entity through the
   UI. The big follow-up; foundation is built write-ready. Runtime-editable web
   config (a typed `WebSettings` entity, making web schema-bearing) lands here.
2. **Standalone JSON API product surface.** Not committed to; "build your own"
   means running the kernel headless without `hearth-web`.
3. **Additional auth mechanisms** (OAuth/OTP/MFA) — land in `hearth-auth` first,
   surface here as login routes.
4. **Deeper nav nesting** (beyond one section level) and **slot composition
   ordering across plugins** if real conflicts appear.
5. **Plugin asset build pipeline** (bundling/transpiling plugin JS/CSS) — only
   if a plugin ever needs more than vendored HTMX/Alpine + plain CSS.
6. **Real-time / websockets**, **i18n machinery**, **`hearth web routes`** debug
   command.
7. **`hearth packages list`** surfacing presentation/library plugins like
   `hearth-web` and `hearth-commons` (shared with the registry-CLI spec's
   deferred item).
