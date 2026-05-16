"""Tests for the EmailAddress value type."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from hearth_commons import EmailAddress


def test_email_valid() -> None:
    e = EmailAddress(raw="alice@example.com")
    assert e.raw == "alice@example.com"


def test_email_normalizes_case_and_whitespace() -> None:
    e = EmailAddress(raw="  Alice@Example.COM  ")
    assert e.raw == "alice@example.com"


def test_email_rejects_no_at_sign() -> None:
    with pytest.raises(ValidationError):
        EmailAddress(raw="not-an-email")


def test_email_rejects_missing_tld() -> None:
    with pytest.raises(ValidationError):
        EmailAddress(raw="alice@example")


def test_email_rejects_empty_local_part() -> None:
    with pytest.raises(ValidationError):
        EmailAddress(raw="@example.com")
