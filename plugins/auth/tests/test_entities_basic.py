"""Tests for User / Role / Permission entities (basic shape only).
`has_permission` is tested in test_has_permission.py once joins exist."""

from __future__ import annotations

from hearth_auth.entities import Permission, Role, User
from hearth_auth.values import HashedSecret, PermissionName
from hearth_commons import EmailAddress


def test_user_basic_construction() -> None:
    u = User(
        email=EmailAddress(raw="alice@example.com"),
        password=HashedSecret.from_plaintext("hunter2"),
        display_name="Alice",
    )
    assert u.email.raw == "alice@example.com"
    assert u.display_name == "Alice"
    assert u.is_active is True
    assert u.last_login_at is None
    assert u.actor_kind == "user"


def test_role_basic_construction() -> None:
    r = Role(name="admin", description="The admin role.")
    assert r.name == "admin"
    assert r.description == "The admin role."


def test_permission_basic_construction() -> None:
    p = Permission(
        name=PermissionName(resource="orders", action="read"),
        description="Read orders.",
    )
    assert p.name.resource == "orders"
    assert p.name.action == "read"


def test_user_actor_kind_is_class_var() -> None:
    """Plugin authors should be able to read actor_kind off the class
    without constructing an instance."""
    assert User.actor_kind == "user"
