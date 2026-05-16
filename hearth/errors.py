"""Hearth exception types — see docs/core/."""

from __future__ import annotations


class HearthError(Exception):
    """Base class for all Hearth-raised exceptions."""


class EntityNotFoundError(HearthError):
    """Raised when a UnitOfWork.get cannot locate the requested entity."""

    def __init__(self, entity_type: type, entity_id: str) -> None:
        self.entity_type = entity_type
        self.entity_id = entity_id
        super().__init__(f"{entity_type.__name__} with id={entity_id} not found")


class IntegrityError(HearthError):
    """Raised on transaction commit when a database integrity constraint
    (UNIQUE, NOT NULL, FOREIGN KEY, CHECK) is violated.

    Wraps SQLAlchemy's `IntegrityError` so plugin code can catch failures
    without importing from `sqlalchemy`. The original DBAPI error is
    available on `__cause__`.
    """
