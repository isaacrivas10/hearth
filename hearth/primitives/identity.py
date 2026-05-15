"""Identity primitive — see docs/core/primitives/identity.md."""

from typing import Literal

from pydantic import BaseModel, ConfigDict


class Identity(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: str


class User(Identity):
    kind: Literal["user"] = "user"  # pyright: ignore[reportIncompatibleVariableOverride]
    user_id: str


class System(Identity):
    kind: Literal["system"] = "system"  # pyright: ignore[reportIncompatibleVariableOverride]
