"""Verify bases_for(alias) returns Entity/Action/Event bases pre-bound to the alias,
and the equivalent longhand form behaves identically."""

from hearth import Action, Entity, Event, bases_for


def test_bases_for_returns_three_subclasses() -> None:
    E, A, Ev = bases_for("test_plugin_alpha")  # noqa: N806
    assert issubclass(E, Entity)
    assert issubclass(A, Action)
    assert issubclass(Ev, Event)


def test_bases_for_propagates_plugin_alias_via_inheritance() -> None:
    E, A, Ev = bases_for("test_plugin_beta")  # noqa: N806

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
    E, _, _ = bases_for("test_plugin_gamma")  # noqa: N806
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
