"""Tests for the Identity primitive — see docs/core/primitives/identity.md."""

import pytest
from pydantic import ValidationError

from hearth import Identity
from hearth.primitives.identity import System, User


def test_user_carries_user_id() -> None:
    u = User(user_id="u123")
    assert u.user_id == "u123"
    assert u.kind == "user"


def test_system_has_kind_system() -> None:
    s = System()
    assert s.kind == "system"


def test_identity_is_frozen() -> None:
    u = User(user_id="u123")
    with pytest.raises(ValidationError):
        u.user_id = "u456"  # type: ignore[misc]


def test_identity_structural_equality() -> None:
    a = User(user_id="u123")
    b = User(user_id="u123")
    c = User(user_id="u456")
    s1 = System()
    s2 = System()
    assert a == b
    assert a != c
    assert s1 == s2


def test_identity_is_a_base_class() -> None:
    assert issubclass(User, Identity)
    assert issubclass(System, Identity)
