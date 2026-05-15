"""PhoneNumber value type — E.164 format, validated at construction."""

from __future__ import annotations

import re

from hearth import Value, field_validator

_E164 = re.compile(r"^\+[1-9]\d{1,14}$")


class PhoneNumber(Value):
    raw: str

    @field_validator("raw")
    @classmethod
    def must_be_e164(cls, v: str) -> str:
        if not _E164.fullmatch(v):
            raise ValueError(
                "phone number must be in E.164 format (e.g., +50499998888)",
            )
        return v
