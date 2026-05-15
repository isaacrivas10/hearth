"""Hearth: a domain-agnostic business kernel with an SDK-first plugin contract.

The public SDK surface is re-exported here. See `docs/core/` for design
specs and `docs/architecture/` for the ADRs.
"""

from functools import cache

from pydantic import computed_field, field_validator, model_validator

from hearth import agg
from hearth.errors import EntityNotFoundError, HearthError
from hearth.fields import Field, ForeignKey
from hearth.identifiers import EntityId
from hearth.kernel.query import Query
from hearth.primitives.action import Action
from hearth.primitives.entity import Entity
from hearth.primitives.event import Event
from hearth.primitives.identity import Identity
from hearth.primitives.value import Value
from hearth.references import References
from hearth.unit_of_work import UnitOfWork


@cache
def bases_for(alias: str) -> tuple[type[Entity], type[Action], type[Event]]:
    """Return Entity/Action/Event base classes pre-bound to the plugin alias."""

    class _PluginEntity(Entity, plugin=alias):
        __abstract__ = True

    class _PluginAction(Action, plugin=alias):
        pass

    class _PluginEvent(Event, plugin=alias):
        pass

    return _PluginEntity, _PluginAction, _PluginEvent


__all__ = [
    "Action",
    "Entity",
    "EntityId",
    "EntityNotFoundError",
    "Event",
    "Field",
    "ForeignKey",
    "HearthError",
    "Identity",
    "Query",
    "References",
    "UnitOfWork",
    "Value",
    "agg",
    "bases_for",
    "computed_field",
    "field_validator",
    "model_validator",
]
