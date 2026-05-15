"""Tests for hearth_commons.PhoneNumber."""

import pytest
from pydantic import ValidationError

from hearth_commons import PhoneNumber


def test_accepts_valid_e164() -> None:
    p = PhoneNumber(raw="+50499998888")
    assert p.raw == "+50499998888"


def test_rejects_missing_plus() -> None:
    with pytest.raises(ValidationError):
        PhoneNumber(raw="50499998888")


def test_rejects_letters() -> None:
    with pytest.raises(ValidationError):
        PhoneNumber(raw="+phone-number")


def test_rejects_too_short() -> None:
    with pytest.raises(ValidationError):
        PhoneNumber(raw="+1")


def test_rejects_too_long() -> None:
    with pytest.raises(ValidationError):
        PhoneNumber(raw="+1234567890123456")


def test_rejects_leading_zero_country_code() -> None:
    with pytest.raises(ValidationError):
        PhoneNumber(raw="+0123456789")


def test_structural_equality() -> None:
    a = PhoneNumber(raw="+50499998888")
    b = PhoneNumber(raw="+50499998888")
    c = PhoneNumber(raw="+50499997777")
    assert a == b
    assert hash(a) == hash(b)
    assert a != c


def test_is_frozen() -> None:
    p = PhoneNumber(raw="+50499998888")
    with pytest.raises(ValidationError):
        p.raw = "+12345678901"  # type: ignore[misc]
