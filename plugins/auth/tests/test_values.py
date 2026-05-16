"""Tests for the auth plugin's Value types."""

from __future__ import annotations

import pytest
from pydantic import SecretStr, ValidationError

from hearth_auth.values import HashedSecret, PermissionName


def test_permission_name_round_trip() -> None:
    pn = PermissionName(resource="orders", action="read")
    assert pn.resource == "orders"
    assert pn.action == "read"


def test_permission_name_equality() -> None:
    a = PermissionName(resource="orders", action="read")
    b = PermissionName(resource="orders", action="read")
    c = PermissionName(resource="orders", action="write")
    assert a == b
    assert a != c
    assert hash(a) == hash(b)


def test_permission_name_wildcards_are_valid() -> None:
    pn = PermissionName(resource="*", action="*")
    assert pn.resource == "*"
    assert pn.action == "*"


@pytest.mark.parametrize(
    ("resource", "action"),
    [
        ("", "read"),
        ("orders", ""),
        ("", ""),
        ("   ", "read"),
        ("orders", "   "),
        (" orders", "read"),
        ("orders ", "read"),
        ("orders", " read"),
        ("orders", "read "),
        ("\torders", "read"),
        ("orders\n", "read"),
    ],
)
def test_permission_name_rejects_empty_or_padded_components(resource: str, action: str) -> None:
    """The validator is the source-of-truth defense against empty/padded grants.

    Without it, `PermissionName(resource="", action="")` would persist a row
    that matches `has_permission(":")` via the OR clause in
    `permission_grant_exists`, silently granting unintended access.
    """
    with pytest.raises(ValidationError):
        PermissionName(resource=resource, action=action)


def test_hashed_secret_from_and_verify_str() -> None:
    h = HashedSecret.from_plaintext("hunter2")
    assert h.hash.startswith("$argon2id$")
    assert h.verify("hunter2") is True
    assert h.verify("wrong") is False


def test_hashed_secret_accepts_secret_str() -> None:
    h = HashedSecret.from_plaintext(SecretStr("hunter2"))
    assert h.verify(SecretStr("hunter2")) is True
    assert h.verify("hunter2") is True


def test_hashed_secret_two_hashes_differ_for_same_plaintext() -> None:
    """argon2id includes a random salt; two hashes of the same plaintext differ."""
    h1 = HashedSecret.from_plaintext("same-input")
    h2 = HashedSecret.from_plaintext("same-input")
    assert h1.hash != h2.hash
    assert h1.verify("same-input") is True
    assert h2.verify("same-input") is True
