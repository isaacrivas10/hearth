"""EntityId — see docs/core/identifiers.md."""

from __future__ import annotations

from typing import Any

from pydantic import GetCoreSchemaHandler
from pydantic_core import CoreSchema, core_schema
from ulid import ULID


class EntityId(str):
    """ULID-backed identity for entities. Assigned by the kernel at first persist."""

    __slots__ = ()

    @classmethod
    def new(cls) -> EntityId:
        return cls(str(ULID()))

    @classmethod
    def __get_pydantic_core_schema__(
        cls,
        _source_type: Any,
        _handler: GetCoreSchemaHandler,
    ) -> CoreSchema:
        return core_schema.no_info_after_validator_function(
            cls._validate,
            core_schema.str_schema(),
        )

    @classmethod
    def _validate(cls, value: str) -> EntityId:
        try:
            ULID.from_str(value)
        except ValueError as exc:
            raise ValueError(f"not a valid ULID: {value!r}") from exc
        return cls(value)
