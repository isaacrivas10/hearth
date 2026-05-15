"""Event primitive — see docs/core/primitives/event.md."""

from __future__ import annotations

from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict


class Event(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    _hearth_plugin: ClassVar[str | None] = None

    def __init_subclass__(cls, *, plugin: str | None = None, **kwargs: Any) -> None:
        if plugin is not None:
            cls._hearth_plugin = plugin
        super().__init_subclass__(**kwargs)
