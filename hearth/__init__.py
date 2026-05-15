"""Hearth: a domain-agnostic business kernel with an SDK-first plugin contract.

The public SDK surface is re-exported here. See `docs/core/` for design
specs and `docs/architecture/` for the ADRs.
"""

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
    "computed_field",
    "field_validator",
    "model_validator",
]
