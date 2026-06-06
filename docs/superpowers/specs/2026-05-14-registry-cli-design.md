# Spec: Plugin Registry and `hearth` CLI

> **Date**: 2026-05-14
> **Status**: Approved (brainstorming complete; implementation plan next)
> **Parent ADRs**: [ADR-0003 (plugin model)](../../architecture/0003-plugin-model.md), [ADR-0008 (built-in plugins)](../../architecture/0008-built-in-plugins.md).
> **Supersedes**: [docs/core/kernel/registry.md](../../core/kernel/registry.md) (stub).

## Purpose

Land the two foundational pieces that everything else in the kernel needs:

- **Plugin Registry**: the runtime, startup-built record of "what plugins are installed in this Python environment," derived from `[project.entry-points."hearth.plugins"]` declarations.
- **`hearth` CLI**: the command-line surface for plugin discovery, schema introspection, and (in a later spec) migration operations.

Both pieces are prerequisites for the migration system. Migrations need the registry to know what plugins exist; the CLI is the invocation surface where migration verbs live.

## Scope

In: registry discovery and validation; the `hearth` CLI with seven v1 commands (`--version`, `plugins list`, `plugins deps`, `plugins show`, `db init`, `db status`, `db graph`); shared engine-factory helper extracted from the test harnesses; extension of the `plugin=` kwarg pattern to `Event` (Entity and Action already support it); the `bases_for(alias)` convenience helper.

Out: migration commands (separate spec), production `App` entry point, JSON output mode, config files, plugin extension points (Airflow/VSCode-style hooks), hot-reload, marketplace install UX. See the "Deferred concerns" section.

## Design

### Three new modules

```
hearth/
  cli/
    __init__.py            # Typer app, --version callback, registers subcommand groups
    _plugins.py            # `hearth plugins list/deps/show` command group
    _db.py                 # `hearth db init/status/graph` command group
  kernel/
    registry.py            # PluginInfo, Registry, RegistryBuildError
    _engine.py             # make_async_engine(url), _to_async_url — shared by CLI + harness
```

Underscore-prefixed CLI submodules because the CLI's surface is the `hearth` command, not Python imports — plugin authors never `from hearth.cli._plugins import ...`.

### Discovery contract

Every plugin's `pyproject.toml` adds one entry:

```toml
[project.entry-points."hearth.plugins"]
commerce = "hearth_commerce"
```

The entry-point **name** is the plugin's alias (used in `plugin="commerce"`); the **value** is the Python module path the kernel will import. Vocabulary libraries like `hearth-commons` (which contribute only `Value` types, no entities/actions/events) **do not** add this entry — they're libraries, not plugins, and stay invisible to the registry.

### Plugin-author idiom — base classes, not repeated kwargs

The kernel exports a one-line helper:

```python
# hearth_commerce/_base.py
from hearth import bases_for

Entity, Action, Event = bases_for("commerce")
```

Every concrete class in the plugin then imports from `_base`:

```python
# hearth_commerce/entities/customer.py
from hearth_commerce._base import Entity
from hearth_commons import PhoneNumber

class Customer(Entity):
    phone: PhoneNumber
    name: str | None = None
```

`bases_for(alias)` returns three subclasses of `hearth.Entity` / `hearth.Action` / `hearth.Event` pre-bound to the plugin alias via `__init_subclass__`. The Entity base is constructed with `__abstract__ = True` in its class namespace so the metaclass skips field-synthesis on it (the metaclass guards on `namespace.get("__abstract__", False)` in `_EntityMeta.__new__`); the Action and Event bases need no such marker since they are Pydantic `BaseModel` subclasses, not SQLAlchemy mapped classes. The alias appears **exactly once** in the entire plugin codebase.

The **longhand form remains supported** — plugin authors who want full IDE visibility into their bases (or want to add plugin-local behavior to them) can write:

```python
# hearth_commerce/_base.py — equivalent longhand
from hearth import Entity as _Entity, Action as _Action, Event as _Event

class Entity(_Entity, plugin="commerce"):
    __abstract__ = True

class Action(_Action, plugin="commerce"):
    pass

class Event(_Event, plugin="commerce"):
    pass
```

Both forms route through the same `__init_subclass__` inheritance machinery. The kernel has one mechanism to maintain; the choice is purely cosmetic.

### Registry build flow

`Registry.build()` runs three phases. Each phase collects all errors before raising:

**Phase 1 — Import.** Iterate `importlib.metadata.entry_points(group="hearth.plugins")`. For each entry, try `importlib.import_module(value)`. Import side-effects register Entity/Action/Event subclasses via the metaclass / `__init_subclass__` hooks (which set `_hearth_plugin` via inherited bases).

**Phase 2 — Alias/kwarg consistency.** Walk `Entity.__subclasses__()` (recursively), `Action.__subclasses__()`, `Event.__subclasses__()`. For each class, check `_hearth_plugin` matches the entry-point alias of the class's *source distribution*. Resolution: take the top-level package of `cls.__module__` (e.g. `hearth_commerce.entities.customer` → `hearth_commerce`); look it up in `importlib.metadata.packages_distributions()` to get the distribution name (`hearth-commerce`); look up that distribution's `hearth.plugins` entry-point name (`commerce`); compare against `cls._hearth_plugin`. Mismatches (e.g., a class with `_hearth_plugin="commerce"` declared in a package that ships under a different distribution) are errors with the offending class and the resolved alias both named. The registry caches the module→alias map built during Phase 1 so this lookup is O(1).

**Phase 3 — Dependency graph.** For each plugin, read `importlib.metadata.requires(package_name)` and intersect with the set of known plugin aliases — that's the plugin's `depends_on`. Then walk each entity's `__table__.foreign_keys`, resolve each FK's target table back to its owning plugin — that's the plugin's `extends`. Then **validate `extends ⊆ depends_on`**: if a plugin FKs into another plugin it does not declare as a Python dependency, that's a `RegistryBuildError` naming the offending FK column, target plugin, and the missing `dependencies` entry the plugin author should add. Topologically sort by `depends_on`; cycles produce an error.

### Registry data model

```python
class PluginInfo:
    alias: str                       # "commerce"
    package: str                     # "hearth-commerce" (PyPI distribution name)
    version: str                     # "0.0.1"
    module: str                      # "hearth_commerce"
    install_path: str                # filesystem location
    depends_on: list[str]            # other plugin aliases this imports
    extends: list[str]               # ⊆ depends_on; plugins this has FKs into
    entities: list[type[Entity]]
    actions: list[type[Action]]
    events: list[type[Event]]


class Registry:
    plugins: dict[str, PluginInfo]            # by alias
    dependency_graph: dict[str, list[str]]    # alias → depends_on
    extension_graph: dict[str, list[str]]     # alias → extends

    @classmethod
    def build(cls) -> "Registry": ...
    def get(self, alias: str) -> PluginInfo: ...
    def topological_order(self) -> list[str]: ...
    def entities_for(self, alias: str) -> list[type[Entity]]: ...
```

Built once at process start (no hot-reload, per ADR-0003). Read-only after construction.

### `depends_on` vs `extends` — operational distinction

Two semantically distinct relationships, both surfaced first-class:

- **depends_on**: A's code imports B (Pydantic event types, Action classes, helper functions). No schema coupling required. A can be uninstalled without affecting B's schema state.
- **extends**: A has entities with `References(...)` into B's tables — a *schema-level* coupling. Removing A leaves orphan rows referencing B's tables; removing B while A is installed corrupts A's foreign keys.

`extends ⊆ depends_on` (you can't FK into a plugin you don't import). The kernel **derives** `extends` from `Entity.__table__.foreign_keys`; plugin authors don't declare it.

### Cross-package namespace contributions are rejected

A third-party library cannot inject entities into another plugin's namespace by string-matching the kwarg. The Phase 2 validation refuses any `_hearth_plugin` value that doesn't match the entry-point alias of the source distribution. The error message points to the correct alternative — define a peer plugin with cross-plugin `References` — and includes a sample fix inline.

This preserves the marketplace trust boundary: installing plugin X never silently extends plugin Y's tables. It also keeps the schema log unambiguous when migrations land — every table belongs to exactly one plugin.

### CLI scaffold

Typer-based (>= 0.12), entry-point declared in root `pyproject.toml`:

```toml
[project]
dependencies = [
    # ... existing ...
    "typer>=0.12",
]

[project.scripts]
hearth = "hearth.cli:app"
```

Sync command shells wrap async kernel work via `asyncio.run`. Each `db` command owns its engine end-to-end (open → run → dispose), no connection pooling across invocations.

### Seven v1 commands

#### `hearth --version`
Prints `hearth <version>` from `importlib.metadata.version("hearth")`. Exits 0.

#### `hearth plugins list`
```
Installed plugins (3):
  commerce        0.0.1   hearth_commerce          deps: (none)
  tax_brackets    1.2.0   hearth_tax_brackets      deps: commerce        extends: commerce
  audit_log       0.4.1   hearth_audit_log         deps: commerce        extends: (none)
```
Columns: alias, version, module, `depends_on`, `extends`. Sorted by alias. Builds the registry; surfaces validation errors if build fails.

#### `hearth plugins deps`
Text-tree of the dependency graph, distinguishing `extends` (schema-touching) from plain `depends_on` (imports only):
```
Dependency graph:
  commerce
  ├── tax_brackets       (extends)
  └── audit_log          (uses)
```

#### `hearth plugins show <alias>`
```
Plugin: commerce
  Package:       hearth-commerce 0.0.1
  Module:        hearth_commerce
  Install path:  .venv/lib/python3.13/site-packages/hearth_commerce
  Depends on:    (none)
  Extends:       (none)
  Extended by:   tax_brackets
  Used by:       tax_brackets, audit_log

  Entities (1):  Customer
  Actions (1):   IdentifyCustomer
  Events (1):    CustomerIdentified
```
Unknown alias → exit 1 with the list of installed plugins.

#### `hearth db init`
Connects via `DATABASE_URL`; creates kernel-internal tables (`_hearth_outbox` today; future migrations add `_hearth_schema_log`). Idempotent. Plugin tables are **not** touched — that's migrations' job. SQLite must be a file URL; `sqlite:///:memory:` is rejected.

#### `hearth db status`
Shows DB connection status, kernel tables present/missing, and (once migrations exist) tracked plugin tables and orphan tables. v1's output is sparse but informative; the format absorbs migration data additively.

#### `hearth db graph`
Registry-based text rendering of the schema graph: tables grouped by anchor (root tables with no inbound FKs), inbound edges shown with `(column → target_table.column, on_delete)`. Future additive enhancements (out of v1 scope): `--format=dot`, `--format=mermaid`, `-o <file>`, `--from-db` for live introspection once the schema log exists.

### Error handling

`Registry.build()` raises `RegistryBuildError` carrying the list of issues. CLI commands catch it and render:

```
$ hearth plugins list
ERROR: cannot build plugin registry.

  • inventory       import failed
                    ModuleNotFoundError: No module named 'hearth_inventory_pkg'

  • orders          entry-point alias 'orders' but entities declare plugin='order'
                    (mismatch in: hearth_orders.entities.Order)

Fix the above and rerun. Exit code 1.
```

DB-connection failures, missing `DATABASE_URL`, and unknown plugin aliases produce their own specific error messages. Exit codes: 0 success; 1 runtime/validation errors; 2 usage errors (Typer's default).

**Unhandled exception path**: the Typer app installs a top-level handler that catches anything other than `RegistryBuildError`, `DatabaseConnectionError`, or `typer.BadParameter` and renders it as `ERROR: unexpected <ExceptionType>: <message>` followed by `Re-run with HEARTH_DEBUG=1 for a full traceback.` Exit code 1. When `HEARTH_DEBUG=1` is set in the environment, the handler re-raises so the traceback surfaces normally.

**Zero plugins installed**: `plugins list` renders `Installed plugins (0): (none)`. `plugins deps` renders `Dependency graph: (no plugins installed)`. `plugins show <alias>` exits 1 since no aliases are valid. `db init` still works — it only creates the kernel's own `_hearth_outbox` table, which is plugin-independent.

**Lazy registry build**: only `plugins list/deps/show` build the registry. `--version` and the `db` commands skip it, so a broken plugin install doesn't block `hearth db init`.

### DB connection lifecycle

CLI invocations are short-lived; each `db` command owns its engine:

```python
async def _init_impl() -> None:
    url = os.environ.get("DATABASE_URL")
    if url is None:
        raise typer.BadParameter("DATABASE_URL not set")
    engine = make_async_engine(url)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(METADATA.create_all)
    finally:
        await engine.dispose()
```

The `make_async_engine(url)` helper currently exists in fragments across `PostgresHarness` (`_to_async_url`) and `Harness` (SQLite + `StaticPool` + FK-pragma listener). This spec consolidates it into `hearth/kernel/_engine.py` as a single dialect-aware factory; both harnesses become thinner.

## Event `plugin=` kwarg extension

`Entity` (via `__init_subclass__`/metaclass) and `Action` (via `__init_subclass__`) already accept the `plugin=` kwarg and set `_hearth_plugin` on the class. Only `Event` needs the change today:

```python
# hearth/primitives/event.py
class Event(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    _hearth_plugin: ClassVar[str | None] = None

    def __init_subclass__(cls, *, plugin: str | None = None, **kwargs: Any) -> None:
        if plugin is not None:
            cls._hearth_plugin = plugin
        super().__init_subclass__(**kwargs)
```

After this, all three primitives accept the kwarg either directly or via inheritance — which is exactly what `bases_for(alias)` relies on.

## File-level summary

### New
- `hearth/cli/__init__.py`, `hearth/cli/_plugins.py`, `hearth/cli/_db.py`
- `hearth/kernel/registry.py`
- `hearth/kernel/_engine.py`
- `hearth/__init__.py` exports `bases_for`

### Modified
- `pyproject.toml` (root): add `typer>=0.12` to `[project] dependencies`; add `[project.scripts] hearth = "hearth.cli:app"`.
- `plugins/commerce/pyproject.toml`: add `[project.entry-points."hearth.plugins"]` block.
- `plugins/commons/pyproject.toml`: **no change** (library, not a plugin).
- `hearth/primitives/event.py`: extend `__init_subclass__` for `plugin=` kwarg (Entity and Action already have it).
- `hearth/testing/_base.py`, `harness.py`, `postgres_harness.py`: route through `make_async_engine`.
- `docs/core/kernel/registry.md`: replace stub with finalized design pointing at this doc.
- Reference plugin (`hearth_commerce`): add `_base.py` using `bases_for("commerce")`; convert existing entity/action/event definitions to inherit from the local bases (drops explicit `plugin="commerce"` kwargs).

## Tests

### Registry unit tests — `tests/test_registry.py`
No DB needed. Synthesize entry points via a test helper that injects fake plugins into `importlib.metadata` (or constructs `PluginInfo`s directly for graph-level tests).

| Test | Validates |
|---|---|
| `test_build_with_one_plugin` | Single plugin, happy path. |
| `test_build_with_dep_chain` | A → B → C produces topological order C, B, A. |
| `test_build_detects_alias_kwarg_mismatch` | Entity with `plugin="Commerce"` but entry-point alias `commerce` raises `RegistryBuildError` naming the class. |
| `test_build_detects_cross_package_namespace_violation` | Class in non-commerce package declaring `plugin="commerce"` is rejected. |
| `test_build_detects_missing_plugin_dep` | Plugin requires another plugin with no entry point → error. |
| `test_build_detects_cycle` | A → B → A raises naming the cycle members. |
| `test_build_collects_multiple_errors` | Two unrelated problems surface in one exception. |
| `test_extends_derived_from_references` | Plugin X with `References(commerce.Customer)` has `commerce` in `extends`. |
| `test_extends_excludes_self` | Internal references don't appear in own `extends`. |
| `test_depends_distinguished_from_extends` | Import-only dep doesn't put the dep in `extends`. |
| `test_extends_must_be_subset_of_depends_on` | Plugin with FK into another plugin it doesn't declare as a Python dep raises `RegistryBuildError`. |
| `test_bases_for_propagates_plugin` | Entity / Action / Event from `bases_for("x")` set `_hearth_plugin="x"` on subclasses; the synthesized Entity carries `__abstract__=True`. |
| `test_zero_plugins_build_returns_empty_registry` | `Registry.build()` with no entry points succeeds; `plugins` is empty. |

### CLI integration tests — `tests/test_cli.py`
Use Typer's `CliRunner`. The CLI builds its own engine from `DATABASE_URL` per command, so it cannot share the in-memory SQLite + `StaticPool` connection that the test `Harness` uses. Instead, the test fixture writes a fresh `sqlite:///<tmp_path>/test.db` URL into the environment for the duration of each test; the existing parametrized `make_harness` factory from `conftest.py` is **not** used for CLI db-command tests.

When `DATABASE_URL` is set externally (a real Postgres available), the fixture optionally also exercises a Postgres run — mirroring the parametrized pattern used elsewhere but at the env-var layer rather than the harness layer.

| Test | Validates |
|---|---|
| `test_version_prints_kernel_version` | Exit 0, output matches. |
| `test_plugins_list_renders_columns` | Output contains alias, version, deps, extends columns. |
| `test_plugins_list_zero_plugins` | With no installed plugins, output is `Installed plugins (0): (none)`. |
| `test_plugins_deps_renders_tree` | Output renders the dependency tree with `(extends)` vs `(uses)` annotations and root-first topological order. |
| `test_plugins_show_unknown_alias_exits_1` | Error names installed plugins. |
| `test_db_init_creates_outbox` | Idempotent; second invocation reports "already exists". |
| `test_db_init_missing_database_url_exits_1` | Clear error. |
| `test_db_init_rejects_in_memory_sqlite` | `sqlite:///:memory:` is rejected with a clear error pointing at file URLs. |
| `test_db_status_reports_connection_ok` | Connection status surfaced. |
| `test_db_graph_text_output` | All tables and FK arrows rendered. |
| `test_registry_build_errors_surface_in_cli` | `plugins list` against a broken registry exits 1 with the bulleted list. |
| `test_unexpected_exception_renders_clean_error` | A non-`RegistryBuildError` raised inside a CLI command produces a `ERROR: unexpected ...` message and exit 1 (not a Python traceback) unless `HEARTH_DEBUG=1`. |

## Deferred concerns (captured for future specs)

These are deliberately out of v1 scope. Each is designed to land additively without breaking the v1 API.

1. **Applied-vs-installed plugin tracking.** Lives in the migration spec. `PluginInfo` and CLI output formats are designed to absorb a `schema_status: Literal["applied", "pending", "n/a"]` field additively. Only entity-defining plugins are subject to the distinction — plugins contributing only actions/events/views have no schema state.

2. **Cross-package namespace contributions.** *Rejected by design.* Every entity must declare its own plugin's alias. The alternative (peer plugin + cross-plugin `References`) is what's blessed. If a "host advertises extension points, satisfiers plug in" pattern (Airflow/VSCode-style) becomes necessary, it lands as a separate mechanism in a future spec.

3. **Marketplace install UX.** Future. The registry/CLI architecture supports it — lifecycle events ("install plugin X") trigger the same `Registry.build()` validation the CLI already invokes.

4. **Vocabulary library tracking.** Libraries like `hearth-commons` are deliberately invisible to the registry. A future `hearth packages list` could surface installed Hearth-ecosystem libraries if useful.

5. **`db graph` extras**: `--format=dot`, `--format=mermaid`, `-o <file>`, `--from-db` live-introspection mode. Additive on top of v1's text/registry-based default.

6. **JSON output mode (`--json`)** across commands. Defer until programmatic consumers appear.

7. **Config file**. `DATABASE_URL` env var only for v1. A future `hearth.toml` or `--db <url>` flag is additive.

8. **Plugin extension points** (the Airflow/VSCode-style "host advertises hooks, plugins satisfy them"). Distinct mechanism from registration; v3+ if needed.

9. **`hearth serve` / production `App` entry point.** Its own spec.

10. **`hearth db migrate ...` command surface.** Spec 2.

11. **Hot-reload.** Forbidden by ADR-0003.

## Decisions log (locked in brainstorming)

| Decision | Rationale |
|---|---|
| Discovery via `[project.entry-points."hearth.plugins"]`, zero-boilerplate | Plugin name/version from standard Python packaging; one entry-point line per plugin; no `Plugin` class or `register()` function. |
| `bases_for(alias)` helper + longhand both supported | Same `__init_subclass__` mechanism underneath; helper is ergonomic default, longhand for explicit control. |
| Vocabulary libraries (e.g. `hearth-commons`) are not plugins | They contribute no runtime entities/actions/events; regular Python imports suffice. |
| `depends_on` and `extends` are first-class, separate relationships | Operationally different (extends touches schema; depends_on may not). `extends` derived from `__table__.foreign_keys`. |
| Cross-package namespace contributions rejected at registry-build time | Preserves marketplace trust boundary and unambiguous schema-log ownership. |
| Validation collects all errors before raising | Better operator UX than fix-rerun-fix-rerun cycles. |
| Lazy registry build | `db` commands work even when plugin imports are broken. |
| Engine factory consolidated in `hearth/kernel/_engine.py` | Shared by CLI, in-memory `Harness`, and `PostgresHarness`; ends today's split between harness files. |

## Migration decisions held in waiting (for spec 2)

The brainstorming session that preceded this spec produced six decisions about the future migration system. Recording them here so they aren't lost; they'll go straight into spec 2 without re-derivation.

1. **Engine**: kernel-native orchestration; `alembic.autogenerate.compare_metadata` and `alembic.operations.Operations` used as libraries only (no `env.py`, no `alembic_version`, no Alembic CLI).
2. **Authoring**: generate-and-vet at build/pre-deploy, apply at runtime — same code path with different triggers. Plugin developers get autogenerated draft revisions they review and commit into the plugin package.
3. **Destructive ops**: per-op explicit acknowledgment. Build fails listing each destructive op until each is acknowledged.
4. **Plugin removal**: orphan by default. The kernel stops managing the plugin's tables but leaves them and their data; dropping is a separate, explicit, per-op-acknowledged action. Schema log tags every table to plugin+version so orphans can be re-adopted on re-install.
5. **Plugin authors ship autogenerated-then-reviewed revision files** in their plugin package (intent capture for renames/backfills).
6. **Scope**: forward-only; minimal data-backfill support; zero-downtime/online migrations deferred.
