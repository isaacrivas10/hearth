"""Tests for the plugin Registry."""

from __future__ import annotations

import sys
import types

import pytest

from hearth import EntityId, ForeignKey, References, bases_for
from hearth.kernel.registry import (
    PluginInfo,
    Registry,
    RegistryBuildError,
    _ModuleSpec,
)


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
    EntityBase, _, _ = bases_for("test_indexing")  # noqa: N806

    class TestCust(EntityBase):
        name: str

    _attach_to_module(mod, TestCust)

    registry = Registry.from_modules(
        {
            "test_indexing": _ModuleSpec(
                module=mod,
                package="hearth-test",
                version="0.0.1",
                requires=(),
            ),
        },
    )
    assert TestCust in registry.get("test_indexing").entities


def test_phase2_rejects_class_with_mismatched_plugin_alias() -> None:
    mod = _fake_module("hearth_test_mismatch")
    EBase, _, _ = bases_for("WRONG_ALIAS")  # noqa: N806

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
    EBaseB, _, _ = bases_for("pkg_b_alias")  # noqa: N806

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
    EBase, ABase, _ = bases_for("not_the_real_alias")  # noqa: N806

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
    EBase, _, _ = bases_for("happy_alias")  # noqa: N806

    class Customer(EBase):
        name: str

    _attach_to_module(mod, Customer)

    registry = Registry.from_modules(
        {"happy_alias": _ModuleSpec(module=mod, package="x", version="0", requires=())},
    )
    assert Customer in registry.get("happy_alias").entities


def test_phase3_depends_on_from_requires_dist() -> None:
    mod_a = _fake_module("hearth_test_dep_a")
    mod_b = _fake_module("hearth_test_dep_b")
    _ = bases_for("dep_alias_a")
    _ = bases_for("dep_alias_b")

    registry = Registry.from_modules(
        {
            "dep_alias_a": _ModuleSpec(module=mod_a, package="pkg-a", version="0", requires=()),
            "dep_alias_b": _ModuleSpec(
                module=mod_b,
                package="pkg-b",
                version="0",
                requires=("pkg-a",),
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
                "cy_a": _ModuleSpec(
                    module=mod_a,
                    package="pkg-a",
                    version="0",
                    requires=("pkg-b",),
                ),
                "cy_b": _ModuleSpec(
                    module=mod_b,
                    package="pkg-b",
                    version="0",
                    requires=("pkg-a",),
                ),
            },
        )
    assert "cycle" in str(exc.value).lower()


def test_phase3_extends_derived_from_references() -> None:
    mod_target = _fake_module("hearth_test_ext_target")
    EBaseT, _, _ = bases_for("ext_target")  # noqa: N806

    class Customer(EBaseT):
        __module__ = "hearth_test_ext_target"
        name: str

    mod_target.Customer = Customer  # type: ignore[attr-defined]

    mod_extender = _fake_module("hearth_test_ext_extender")
    EBaseE, _, _ = bases_for("ext_extender")  # noqa: N806
    # Expose EntityId and Customer in the fake module's namespace so PEP-563
    # stringified annotations resolve when SQLAlchemy maps the entity.
    mod_extender.EntityId = EntityId  # type: ignore[attr-defined]
    mod_extender.Customer = Customer  # type: ignore[attr-defined]

    class Order(EBaseE):
        __module__ = "hearth_test_ext_extender"
        customer_id: EntityId = ForeignKey()
        customer: Customer = References(Customer)

    mod_extender.Order = Order  # type: ignore[attr-defined]

    registry = Registry.from_modules(
        {
            "ext_target": _ModuleSpec(
                module=mod_target,
                package="pkg-target",
                version="0",
                requires=(),
            ),
            "ext_extender": _ModuleSpec(
                module=mod_extender,
                package="pkg-extender",
                version="0",
                requires=("pkg-target",),
            ),
        },
    )
    assert registry.get("ext_extender").extends == ["ext_target"]
    assert registry.get("ext_target").extends == []


def test_phase3_invariant_extends_subset_of_depends_on() -> None:
    mod_target = _fake_module("hearth_test_inv_target")
    EBaseT, _, _ = bases_for("inv_target")  # noqa: N806

    class Cust(EBaseT):
        __module__ = "hearth_test_inv_target"
        name: str

    mod_target.Cust = Cust  # type: ignore[attr-defined]

    mod_extender = _fake_module("hearth_test_inv_extender")
    EBaseE, _, _ = bases_for("inv_extender")  # noqa: N806
    # Expose EntityId and Cust in the fake module's namespace so PEP-563
    # stringified annotations resolve when SQLAlchemy maps the entity.
    mod_extender.EntityId = EntityId  # type: ignore[attr-defined]
    mod_extender.Cust = Cust  # type: ignore[attr-defined]

    class Ord(EBaseE):
        __module__ = "hearth_test_inv_extender"
        customer_id: EntityId = ForeignKey()
        customer: Cust = References(Cust)

    mod_extender.Ord = Ord  # type: ignore[attr-defined]

    with pytest.raises(RegistryBuildError) as exc:
        Registry.from_modules(
            {
                "inv_target": _ModuleSpec(
                    module=mod_target,
                    package="pkg-target",
                    version="0",
                    requires=(),
                ),
                "inv_extender": _ModuleSpec(
                    module=mod_extender,
                    package="pkg-extender",
                    version="0",
                    requires=(),  # missing pkg-target as Python dep
                ),
            },
        )
    msg = str(exc.value).lower()
    assert "inv_extender" in msg
    assert "inv_target" in msg


def test_registry_build_with_no_entry_points(monkeypatch: pytest.MonkeyPatch) -> None:
    import hearth.kernel.registry as reg_mod

    monkeypatch.setattr(reg_mod.importlib.metadata, "entry_points", lambda **kw: [])
    registry = Registry.build()
    assert registry.plugins == {}


def test_registry_build_imports_entry_point_modules(monkeypatch: pytest.MonkeyPatch) -> None:
    import hearth.kernel.registry as reg_mod

    mod = _fake_module("hearth_test_buildep")
    EBase, _, _ = bases_for("buildep")  # noqa: N806

    class Cust(EBase):
        name: str

    _attach_to_module(mod, Cust)

    class FakeEP:
        name = "buildep"
        value = "hearth_test_buildep"
        dist = type(
            "FakeDist",
            (),
            {
                "name": "pkg-buildep",
                "version": "0.0.1",
                "requires": (),
            },
        )()

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
