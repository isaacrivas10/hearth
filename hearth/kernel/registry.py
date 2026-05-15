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
        lines = ["plugin registry build failed:", *(f"  - {i}" for i in issues)]
        super().__init__("\n".join(lines))


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
    def from_modules(cls, specs: dict[str, _ModuleSpec]) -> Registry:
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

    @classmethod
    def build(cls) -> Registry:
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
            if dist is None:
                import_errors.append(f"{ep.name}: entry point has no distribution metadata")
                continue
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
