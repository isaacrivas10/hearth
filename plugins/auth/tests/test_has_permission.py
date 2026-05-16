"""Tests for has_permission resolution on User and ApiKey."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime, timedelta

import pytest

from hearth.testing import BaseHarness
from hearth_auth.entities import (
    ApiKey,
    ApiKeyPermission,
    Permission,
    Role,
    RolePermission,
    User,
    UserRole,
)
from hearth_auth.values import HashedSecret, PermissionName
from hearth_commons import EmailAddress

ENTITY_LIST = [User, Role, Permission, UserRole, RolePermission, ApiKey, ApiKeyPermission]


@pytest.fixture
async def harness(
    make_harness: Callable[[], BaseHarness],
) -> AsyncIterator[BaseHarness]:
    h = make_harness()
    await h.setup(entities=ENTITY_LIST)
    try:
        await h.reset()
        yield h
    finally:
        await h.teardown()


async def _make_user_with_perm(harness: BaseHarness, perm_resource: str, perm_action: str) -> User:
    """Build a User with a Role that holds a specific Permission. Returns the User."""
    async with harness.transaction() as uow:
        u = User(
            email=EmailAddress(raw=f"u-{perm_resource}@example.com"),
            password=HashedSecret.from_plaintext("x"),
        )
        await uow.save(u)
        r = Role(name=f"role-{perm_resource}")
        await uow.save(r)
        p = Permission(name=PermissionName(resource=perm_resource, action=perm_action))
        await uow.save(p)
        await uow.save(UserRole(user=u, role=r))
        await uow.save(RolePermission(role=r, permission=p))
        return u


async def test_user_has_granted_permission(harness: BaseHarness) -> None:
    u = await _make_user_with_perm(harness, "orders", "read")
    async with harness.transaction() as uow:
        u_fresh = await uow.query(User).where(User.id == u.id).one()
        assert await u_fresh.has_permission(uow, "orders:read") is True


async def test_user_lacks_ungranted_permission(harness: BaseHarness) -> None:
    u = await _make_user_with_perm(harness, "orders", "read")
    async with harness.transaction() as uow:
        u_fresh = await uow.query(User).where(User.id == u.id).one()
        assert await u_fresh.has_permission(uow, "orders:write") is False
        assert await u_fresh.has_permission(uow, "users:read") is False


async def test_user_wildcard_short_circuits(harness: BaseHarness) -> None:
    async with harness.transaction() as uow:
        u = User(
            email=EmailAddress(raw="admin@example.com"),
            password=HashedSecret.from_plaintext("x"),
        )
        await uow.save(u)
        r = Role(name="admin")
        await uow.save(r)
        wildcard = Permission(name=PermissionName(resource="*", action="*"))
        await uow.save(wildcard)
        await uow.save(UserRole(user=u, role=r))
        await uow.save(RolePermission(role=r, permission=wildcard))

    async with harness.transaction() as uow:
        u_fresh = await uow.query(User).where(User.id == u.id).one()
        assert await u_fresh.has_permission(uow, "anything:goes") is True
        assert await u_fresh.has_permission(uow, "orders:read") is True


async def test_api_key_has_granted_permission(harness: BaseHarness) -> None:
    async with harness.transaction() as uow:
        k = ApiKey(
            name="x",
            key_prefix="hak_abc",
            key_hash=HashedSecret.from_plaintext("s"),
        )
        await uow.save(k)
        p = Permission(name=PermissionName(resource="webhooks", action="receive"))
        await uow.save(p)
        await uow.save(ApiKeyPermission(api_key=k, permission=p))

    async with harness.transaction() as uow:
        k_fresh = await uow.query(ApiKey).where(ApiKey.id == k.id).one()
        assert await k_fresh.has_permission(uow, "webhooks:receive") is True
        assert await k_fresh.has_permission(uow, "orders:read") is False


async def test_api_key_wildcard_short_circuits(harness: BaseHarness) -> None:
    async with harness.transaction() as uow:
        k = ApiKey(
            name="superkey",
            key_prefix="hak_sup",
            key_hash=HashedSecret.from_plaintext("s"),
        )
        await uow.save(k)
        wildcard = Permission(name=PermissionName(resource="*", action="*"))
        await uow.save(wildcard)
        await uow.save(ApiKeyPermission(api_key=k, permission=wildcard))

    async with harness.transaction() as uow:
        k_fresh = await uow.query(ApiKey).where(ApiKey.id == k.id).one()
        assert await k_fresh.has_permission(uow, "anything:goes") is True


async def test_api_key_revoked_blocks_all_permissions(harness: BaseHarness) -> None:
    async with harness.transaction() as uow:
        k = ApiKey(
            name="revoked",
            key_prefix="hak_rev",
            key_hash=HashedSecret.from_plaintext("s"),
            revoked_at=datetime.now(UTC),
        )
        await uow.save(k)
        wildcard = Permission(name=PermissionName(resource="*", action="*"))
        await uow.save(wildcard)
        await uow.save(ApiKeyPermission(api_key=k, permission=wildcard))

    async with harness.transaction() as uow:
        k_fresh = await uow.query(ApiKey).where(ApiKey.id == k.id).one()
        assert await k_fresh.has_permission(uow, "anything:goes") is False


async def test_api_key_expired_blocks_all_permissions(harness: BaseHarness) -> None:
    past = datetime.now(UTC) - timedelta(days=1)
    async with harness.transaction() as uow:
        k = ApiKey(
            name="expired",
            key_prefix="hak_exp",
            key_hash=HashedSecret.from_plaintext("s"),
            expires_at=past,
        )
        await uow.save(k)
        wildcard = Permission(name=PermissionName(resource="*", action="*"))
        await uow.save(wildcard)
        await uow.save(ApiKeyPermission(api_key=k, permission=wildcard))

    async with harness.transaction() as uow:
        k_fresh = await uow.query(ApiKey).where(ApiKey.id == k.id).one()
        assert await k_fresh.has_permission(uow, "anything:goes") is False


# ---------------------------------------------------------------------------
# Bypass / isolation / edge-case suite
#
# These tests assert that a bad actor cannot squeak past a permission check by:
# (a) constructing a malformed permission string,
# (b) leaning on another user's or role's grants,
# (c) exploiting partial wildcards or wildcard-in-request,
# (d) retaining authority after being disabled, or
# (e) reaching through an API key's owner relationship.
# ---------------------------------------------------------------------------


async def test_user_with_zero_grants_denied_everything(harness: BaseHarness) -> None:
    """A user with no role assignments and no permissions denies every check."""
    async with harness.transaction() as uow:
        u = User(
            email=EmailAddress(raw="zero@example.com"),
            password=HashedSecret.from_plaintext("x"),
        )
        await uow.save(u)

    async with harness.transaction() as uow:
        u_fresh = await uow.query(User).where(User.id == u.id).one()
        assert await u_fresh.has_permission(uow, "orders:read") is False
        assert await u_fresh.has_permission(uow, "anything:goes") is False
        assert await u_fresh.has_permission(uow, "*:*") is False


async def test_cross_user_isolation(harness: BaseHarness) -> None:
    """User A's grant must not leak to User B, even with identical role/permission."""
    a = await _make_user_with_perm(harness, "orders", "read")
    async with harness.transaction() as uow:
        b = User(
            email=EmailAddress(raw="b@example.com"),
            password=HashedSecret.from_plaintext("x"),
        )
        await uow.save(b)

    async with harness.transaction() as uow:
        a_fresh = await uow.query(User).where(User.id == a.id).one()
        b_fresh = await uow.query(User).where(User.id == b.id).one()
        assert await a_fresh.has_permission(uow, "orders:read") is True
        assert await b_fresh.has_permission(uow, "orders:read") is False


async def test_cross_role_isolation(harness: BaseHarness) -> None:
    """A user holding role R1 with permission X must not gain permission Y
    granted to a different role R2 they don't hold."""
    async with harness.transaction() as uow:
        u = User(
            email=EmailAddress(raw="ro@example.com"),
            password=HashedSecret.from_plaintext("x"),
        )
        await uow.save(u)
        r1 = Role(name="r1")
        r2 = Role(name="r2")
        await uow.save(r1)
        await uow.save(r2)
        p_read = Permission(name=PermissionName(resource="orders", action="read"))
        p_write = Permission(name=PermissionName(resource="orders", action="write"))
        await uow.save(p_read)
        await uow.save(p_write)
        await uow.save(UserRole(user=u, role=r1))
        await uow.save(RolePermission(role=r1, permission=p_read))
        # r2 holds write but u is NOT in r2
        await uow.save(RolePermission(role=r2, permission=p_write))

    async with harness.transaction() as uow:
        u_fresh = await uow.query(User).where(User.id == u.id).one()
        assert await u_fresh.has_permission(uow, "orders:read") is True
        assert await u_fresh.has_permission(uow, "orders:write") is False


async def test_user_with_multiple_roles_any_grants_succeeds(
    harness: BaseHarness,
) -> None:
    """A user with two roles satisfies permissions granted by either role."""
    async with harness.transaction() as uow:
        u = User(
            email=EmailAddress(raw="multi@example.com"),
            password=HashedSecret.from_plaintext("x"),
        )
        await uow.save(u)
        r1 = Role(name="reader")
        r2 = Role(name="writer")
        await uow.save(r1)
        await uow.save(r2)
        p_read = Permission(name=PermissionName(resource="orders", action="read"))
        p_write = Permission(name=PermissionName(resource="orders", action="write"))
        await uow.save(p_read)
        await uow.save(p_write)
        await uow.save(UserRole(user=u, role=r1))
        await uow.save(UserRole(user=u, role=r2))
        await uow.save(RolePermission(role=r1, permission=p_read))
        await uow.save(RolePermission(role=r2, permission=p_write))

    async with harness.transaction() as uow:
        u_fresh = await uow.query(User).where(User.id == u.id).one()
        assert await u_fresh.has_permission(uow, "orders:read") is True
        assert await u_fresh.has_permission(uow, "orders:write") is True
        assert await u_fresh.has_permission(uow, "orders:delete") is False


@pytest.mark.parametrize(
    "malformed",
    [
        "",
        "orders",
        "orders:read:extra",
        "::",
        ":read",
        "orders:",
        ":",
        "  :  ",
    ],
)
async def test_malformed_permission_strings_always_deny(
    harness: BaseHarness, malformed: str
) -> None:
    """Malformed permission lookups never match — even when the actor holds
    the wildcard. This guards against typos and against grafted-input
    attacks that try to smuggle extra colons or empty segments through the
    splitter."""
    async with harness.transaction() as uow:
        u = User(
            email=EmailAddress(raw="adm@example.com"),
            password=HashedSecret.from_plaintext("x"),
        )
        await uow.save(u)
        r = Role(name="admin")
        await uow.save(r)
        wildcard = Permission(name=PermissionName(resource="*", action="*"))
        await uow.save(wildcard)
        await uow.save(UserRole(user=u, role=r))
        await uow.save(RolePermission(role=r, permission=wildcard))

    async with harness.transaction() as uow:
        u_fresh = await uow.query(User).where(User.id == u.id).one()
        assert await u_fresh.has_permission(uow, malformed) is False


async def test_wildcard_request_does_not_match_specific_grants(
    harness: BaseHarness,
) -> None:
    """A user with only `orders:read` MUST NOT satisfy a `*:*` request.

    The OR clause checks for an EXACT wildcard row OR an exact-match row;
    a specific grant cannot stand in for a wildcard request."""
    u = await _make_user_with_perm(harness, "orders", "read")
    async with harness.transaction() as uow:
        u_fresh = await uow.query(User).where(User.id == u.id).one()
        assert await u_fresh.has_permission(uow, "*:*") is False
        assert await u_fresh.has_permission(uow, "orders:*") is False
        assert await u_fresh.has_permission(uow, "*:read") is False


async def test_resource_wildcard_grant_covers_all_actions_on_that_resource(
    harness: BaseHarness,
) -> None:
    """A `Permission("orders", "*")` grant authorizes every action on orders
    but does NOT leak to other resources. Common case: an admin role for a
    single resource without enumerating every action."""
    async with harness.transaction() as uow:
        u = User(
            email=EmailAddress(raw="orders-admin@example.com"),
            password=HashedSecret.from_plaintext("x"),
        )
        await uow.save(u)
        r = Role(name="orders-admin")
        await uow.save(r)
        partial = Permission(name=PermissionName(resource="orders", action="*"))
        await uow.save(partial)
        await uow.save(UserRole(user=u, role=r))
        await uow.save(RolePermission(role=r, permission=partial))

    async with harness.transaction() as uow:
        u_fresh = await uow.query(User).where(User.id == u.id).one()
        # All actions on the granted resource succeed.
        assert await u_fresh.has_permission(uow, "orders:read") is True
        assert await u_fresh.has_permission(uow, "orders:write") is True
        assert await u_fresh.has_permission(uow, "orders:delete") is True
        # Literal `orders:*` matches the grant row directly.
        assert await u_fresh.has_permission(uow, "orders:*") is True
        # Other resources remain locked.
        assert await u_fresh.has_permission(uow, "users:read") is False
        assert await u_fresh.has_permission(uow, "invoices:write") is False
        # Does NOT escalate to full wildcard.
        assert await u_fresh.has_permission(uow, "*:*") is False


async def test_action_wildcard_grant_covers_action_on_all_resources(
    harness: BaseHarness,
) -> None:
    """A `Permission("*", "read")` grant authorizes the read action on every
    resource (rarer but used for "auditor"-shaped roles), and does NOT leak
    to other actions."""
    async with harness.transaction() as uow:
        u = User(
            email=EmailAddress(raw="auditor@example.com"),
            password=HashedSecret.from_plaintext("x"),
        )
        await uow.save(u)
        r = Role(name="auditor")
        await uow.save(r)
        cross = Permission(name=PermissionName(resource="*", action="read"))
        await uow.save(cross)
        await uow.save(UserRole(user=u, role=r))
        await uow.save(RolePermission(role=r, permission=cross))

    async with harness.transaction() as uow:
        u_fresh = await uow.query(User).where(User.id == u.id).one()
        # The granted action on any resource succeeds.
        assert await u_fresh.has_permission(uow, "orders:read") is True
        assert await u_fresh.has_permission(uow, "users:read") is True
        assert await u_fresh.has_permission(uow, "invoices:read") is True
        # Literal `*:read` matches the grant row directly.
        assert await u_fresh.has_permission(uow, "*:read") is True
        # Other actions remain locked.
        assert await u_fresh.has_permission(uow, "orders:write") is False
        assert await u_fresh.has_permission(uow, "users:delete") is False
        # Does NOT escalate to full wildcard.
        assert await u_fresh.has_permission(uow, "*:*") is False


async def test_partial_wildcards_compose_without_leaking(
    harness: BaseHarness,
) -> None:
    """A user with BOTH `("orders", "*")` and `("*", "read")` has:
      - every action on orders
      - the read action on every resource
    But not `users:write` (no grant intersects there)."""
    async with harness.transaction() as uow:
        u = User(
            email=EmailAddress(raw="compose@example.com"),
            password=HashedSecret.from_plaintext("x"),
        )
        await uow.save(u)
        r = Role(name="hybrid")
        await uow.save(r)
        orders_all = Permission(name=PermissionName(resource="orders", action="*"))
        any_read = Permission(name=PermissionName(resource="*", action="read"))
        await uow.save(orders_all)
        await uow.save(any_read)
        await uow.save(UserRole(user=u, role=r))
        await uow.save(RolePermission(role=r, permission=orders_all))
        await uow.save(RolePermission(role=r, permission=any_read))

    async with harness.transaction() as uow:
        u_fresh = await uow.query(User).where(User.id == u.id).one()
        # Via orders_all:
        assert await u_fresh.has_permission(uow, "orders:write") is True
        # Via any_read:
        assert await u_fresh.has_permission(uow, "users:read") is True
        # Via either (orders:read is on the intersection):
        assert await u_fresh.has_permission(uow, "orders:read") is True
        # Not granted by either:
        assert await u_fresh.has_permission(uow, "users:write") is False
        assert await u_fresh.has_permission(uow, "invoices:delete") is False
        # Still not full wildcard.
        assert await u_fresh.has_permission(uow, "*:*") is False


async def test_api_key_resource_wildcard_grant_covers_actions(
    harness: BaseHarness,
) -> None:
    """Partial wildcards work for ApiKey grants too (symmetric with User)."""
    async with harness.transaction() as uow:
        k = ApiKey(
            name="webhooks-key",
            key_prefix="hak_wh",
            key_hash=HashedSecret.from_plaintext("s"),
        )
        await uow.save(k)
        partial = Permission(name=PermissionName(resource="webhooks", action="*"))
        await uow.save(partial)
        await uow.save(ApiKeyPermission(api_key=k, permission=partial))

    async with harness.transaction() as uow:
        k_fresh = await uow.query(ApiKey).where(ApiKey.id == k.id).one()
        assert await k_fresh.has_permission(uow, "webhooks:receive") is True
        assert await k_fresh.has_permission(uow, "webhooks:replay") is True
        assert await k_fresh.has_permission(uow, "orders:read") is False
        assert await k_fresh.has_permission(uow, "*:*") is False


async def test_disabled_user_denied_all_permissions(harness: BaseHarness) -> None:
    """A user disabled after grants are issued is blocked at has_permission.

    Mirrors ApiKey's revoked_at/expires_at defense. Without it, an actor
    captured at authenticate time and reused (long-lived session, queued job)
    would still pass `@requires` until the next re-authentication."""
    async with harness.transaction() as uow:
        u = User(
            email=EmailAddress(raw="will-be-disabled@example.com"),
            password=HashedSecret.from_plaintext("x"),
        )
        await uow.save(u)
        r = Role(name="admin")
        await uow.save(r)
        wildcard = Permission(name=PermissionName(resource="*", action="*"))
        await uow.save(wildcard)
        await uow.save(UserRole(user=u, role=r))
        await uow.save(RolePermission(role=r, permission=wildcard))

    # While active: passes.
    async with harness.transaction() as uow:
        u_fresh = await uow.query(User).where(User.id == u.id).one()
        assert await u_fresh.has_permission(uow, "orders:read") is True

    # Disable in a separate transaction.
    async with harness.transaction() as uow:
        u_disable = await uow.query(User).where(User.id == u.id).one()
        u_disable.is_active = False
        await uow.save(u_disable)

    # Re-fetch in yet another transaction (fresh session — no SA identity-map cache).
    async with harness.transaction() as uow:
        u_recheck = await uow.query(User).where(User.id == u.id).one()
        assert u_recheck.is_active is False
        assert await u_recheck.has_permission(uow, "orders:read") is False
        assert await u_recheck.has_permission(uow, "anything:goes") is False


async def test_api_key_owner_permissions_do_not_transitively_grant(
    harness: BaseHarness,
) -> None:
    """An ApiKey owned by a wildcard-admin User MUST NOT inherit the owner's
    permissions. Keys carry their own ApiKeyPermission grants; the owner
    pointer is metadata, not an authorization edge."""
    async with harness.transaction() as uow:
        admin = User(
            email=EmailAddress(raw="admin-owner@example.com"),
            password=HashedSecret.from_plaintext("x"),
        )
        await uow.save(admin)
        r = Role(name="admin")
        await uow.save(r)
        wildcard = Permission(name=PermissionName(resource="*", action="*"))
        await uow.save(wildcard)
        await uow.save(UserRole(user=admin, role=r))
        await uow.save(RolePermission(role=r, permission=wildcard))

        # Key owned by admin, but with NO permissions of its own.
        k = ApiKey(
            name="ownerless-perms",
            key_prefix="hak_own",
            key_hash=HashedSecret.from_plaintext("s"),
            owner=admin,
        )
        await uow.save(k)

    async with harness.transaction() as uow:
        k_fresh = await uow.query(ApiKey).where(ApiKey.id == k.id).one()
        assert await k_fresh.has_permission(uow, "orders:read") is False
        assert await k_fresh.has_permission(uow, "anything:goes") is False
        assert await k_fresh.has_permission(uow, "*:*") is False


async def test_api_key_with_disabled_owner_still_passes_today(
    harness: BaseHarness,
) -> None:
    """Documents CURRENT BEHAVIOR: an ApiKey's permissions are independent
    of whether its owning User is disabled. Disabling a User does NOT
    automatically revoke their API keys.

    If we later decide the symmetric behavior — disabling a user revokes
    their keys — this test fails and forces an explicit migration. The
    cheap-and-explicit alternative is for operators to call RevokeApiKey on
    each owned key alongside DisableUser."""
    async with harness.transaction() as uow:
        owner = User(
            email=EmailAddress(raw="disabled-owner@example.com"),
            password=HashedSecret.from_plaintext("x"),
            is_active=False,
        )
        await uow.save(owner)
        k = ApiKey(
            name="key",
            key_prefix="hak_k",
            key_hash=HashedSecret.from_plaintext("s"),
            owner=owner,
        )
        await uow.save(k)
        p = Permission(name=PermissionName(resource="webhooks", action="receive"))
        await uow.save(p)
        await uow.save(ApiKeyPermission(api_key=k, permission=p))

    async with harness.transaction() as uow:
        k_fresh = await uow.query(ApiKey).where(ApiKey.id == k.id).one()
        # CURRENT: the key still grants its own permissions.
        assert await k_fresh.has_permission(uow, "webhooks:receive") is True


async def test_revoking_user_role_removes_access(harness: BaseHarness) -> None:
    """Removing a UserRole row revokes the user's access via that role."""
    async with harness.transaction() as uow:
        u = User(
            email=EmailAddress(raw="rev@example.com"),
            password=HashedSecret.from_plaintext("x"),
        )
        await uow.save(u)
        r = Role(name="role")
        await uow.save(r)
        p = Permission(name=PermissionName(resource="orders", action="read"))
        await uow.save(p)
        await uow.save(UserRole(user=u, role=r))
        await uow.save(RolePermission(role=r, permission=p))

    async with harness.transaction() as uow:
        u_fresh = await uow.query(User).where(User.id == u.id).one()
        assert await u_fresh.has_permission(uow, "orders:read") is True

    async with harness.transaction() as uow:
        link = await (
            uow.query(UserRole)
            .where(UserRole.user_id == u.id)
            .where(UserRole.role_id == r.id)
            .one()
        )
        await uow.delete(link)

    async with harness.transaction() as uow:
        u_fresh = await uow.query(User).where(User.id == u.id).one()
        assert await u_fresh.has_permission(uow, "orders:read") is False


@pytest.mark.parametrize(
    "shaped",
    [
        "orders';--",
        "orders' OR 1=1--",
        "orders\x00:read",
        "or\nders:read",
    ],
)
async def test_sql_injection_shaped_names_do_not_match(harness: BaseHarness, shaped: str) -> None:
    """Parameterization sanity: malicious-shaped resource/action strings
    in the request do not produce SQL injection — they simply don't match."""
    u = await _make_user_with_perm(harness, "orders", "read")
    async with harness.transaction() as uow:
        u_fresh = await uow.query(User).where(User.id == u.id).one()
        assert await u_fresh.has_permission(uow, shaped) is False
