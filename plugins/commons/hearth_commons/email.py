"""EmailAddress value type — basic RFC-shaped check, validated at construction."""

from __future__ import annotations

import re

from hearth import Value, field_validator

# Pragmatic email pattern: local-part@domain.tld. Not full RFC 5322 (that grammar
# accepts pathological forms nobody uses); this catches typos while staying
# readable. If we ever need stricter validation, switch to email-validator.
_EMAIL = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")


class EmailAddress(Value):
    raw: str

    @field_validator("raw")
    @classmethod
    def must_look_like_email(cls, v: str) -> str:
        normalized = v.strip().lower()
        if not _EMAIL.fullmatch(normalized):
            raise ValueError(
                "email must look like local@domain.tld (e.g., alice@example.com)",
            )
        return normalized
