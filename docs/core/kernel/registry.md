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
