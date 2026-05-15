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
