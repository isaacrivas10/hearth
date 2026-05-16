"""Hearth: a domain-agnostic business kernel with an SDK-first plugin contract.

The public SDK surface is re-exported here. See `docs/core/` for design
specs and `docs/architecture/` for the ADRs.
"""

from functools import cache
from typing import Any

from pydantic import computed_field, field_validator, model_validator
from sqlalchemy import and_ as _sa_and
from sqlalchemy import or_ as _sa_or

from hearth import agg
from hearth.constraints import Unique
from hearth.errors import EntityNotFoundError, HearthError, IntegrityError
from hearth.fields import Field, ForeignKey
from hearth.identifiers import EntityId
from hearth.kernel.query import Query
from hearth.kernel.transaction import scope, transaction, transaction_factory
from hearth.primitives.action import Action
from hearth.primitives.actor import Actor, Anonymous, PluginActor, System
from hearth.primitives.entity import Entity
from hearth.primitives.event import Event
from hearth.primitives.value import Value
from hearth.references import References
from hearth.unit_of_work import UnitOfWork

# Re-export SA's boolean combinators as Any-typed callables. Composite Value
# comparisons like `PermissionName.resource == "*"` are SA ColumnElements at
# runtime, but pyright sees them as bool (because dataclass_transform hides
# the underlying Mapped[T]). Typing the callables as Any matches the Query
# API's `WhereClause = Any` convention in hearth/kernel/query.py.
and_: Any = _sa_and
or_: Any = _sa_or


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
    "Actor",
    "Anonymous",
    "Entity",
    "EntityId",
    "EntityNotFoundError",
    "Event",
    "Field",
    "ForeignKey",
    "HearthError",
    "IntegrityError",
    "PluginActor",
    "Query",
    "References",
    "System",
    "Unique",
    "UnitOfWork",
    "Value",
    "agg",
    "and_",
    "bases_for",
    "computed_field",
    "field_validator",
    "model_validator",
    "or_",
    "scope",
    "transaction",
    "transaction_factory",
]
