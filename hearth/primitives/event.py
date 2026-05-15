"""Event primitive — see docs/core/primitives/event.md."""

from pydantic import BaseModel, ConfigDict


class Event(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
