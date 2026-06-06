"""Tests for the Value primitive contract — see docs/core/primitives/value.md."""

import pytest
from pydantic import ValidationError

from hearth import Value, field_validator


class _Phone(Value):
    """Test fixture exercising the Value contract."""

    raw: str

    @field_validator("raw")
    @classmethod
    def must_be_e164(cls, v: str) -> str:
        if not v.startswith("+") or len(v) < 4:
            raise ValueError("must look E.164-ish")
        return v


def test_accepts_valid_input() -> None:
    assert _Phone(raw="+50499998888").raw == "+50499998888"


def test_rejects_invalid_input_at_construction() -> None:
    with pytest.raises(ValidationError):
        _Phone(raw="bad")


def test_is_frozen() -> None:
    p = _Phone(raw="+50499998888")
    with pytest.raises(ValidationError):
        p.raw = "+12345678901"  # type: ignore[misc]


def test_structural_equality_and_hash() -> None:
    a = _Phone(raw="+50499998888")
    b = _Phone(raw="+50499998888")
    c = _Phone(raw="+50499997777")
    assert a == b
    assert hash(a) == hash(b)
    assert a != c
    assert {a, b} == {a}


def test_json_roundtrip() -> None:
    a = _Phone(raw="+50499998888")
    assert _Phone.model_validate_json(a.model_dump_json()) == a


def test_extra_attributes_forbidden() -> None:
    with pytest.raises(ValidationError):
        _Phone.model_validate({"raw": "+50499998888", "extra_field": "nope"})
