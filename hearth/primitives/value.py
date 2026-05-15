"""Value primitive — see docs/core/primitives/value.md."""

from typing import Any

from pydantic import BaseModel, ConfigDict


class Value(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        validate_assignment=True,
        extra="forbid",
    )

    def __composite_values__(self) -> tuple[Any, ...]:
        # SA's composite() uses this to decompose a Value into its sub-columns
        # at flush time. Order matches model_fields so it lines up with the
        # mapped_column list passed to composite() by the Entity metaclass.
        return tuple(getattr(self, f) for f in type(self).model_fields)
