"""Action primitive — see docs/core/primitives/action.md."""

from __future__ import annotations

from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict

from hearth.primitives.actor import Actor
from hearth.unit_of_work import UnitOfWork


class Action(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    _hearth_plugin: ClassVar[str | None] = None

    def __init_subclass__(cls, *, plugin: str | None = None, **kwargs: Any) -> None:
        if plugin is not None:
            cls._hearth_plugin = plugin
        super().__init_subclass__(**kwargs)

    async def handle(self, uow: UnitOfWork, actor: Actor) -> Any:
        raise NotImplementedError(f"{type(self).__name__} must implement handle()")
