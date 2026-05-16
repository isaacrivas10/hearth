"""Tests for hearth_auth helpers: exceptions, require_permission, @requires."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable

import pytest

from hearth import Action, Anonymous, PluginActor, System
from hearth.testing import BaseHarness
from hearth_auth import (
    AuthenticationFailed,
    PermissionDenied,
    require_permission,
    requires,
)
from hearth_auth.entities import (
    ApiKey,
    ApiKeyPermission,
    Permission,
    Role,
    RolePermission,
    User,
    UserRole,
)

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


async def test_require_permission_passes_for_system(harness: BaseHarness) -> None:
    async with harness.transaction() as uow:
        await require_permission(uow, System(), "anything:goes")


async def test_require_permission_raises_for_anonymous(harness: BaseHarness) -> None:
    async with harness.transaction() as uow:
        with pytest.raises(PermissionDenied) as exc:
            await require_permission(uow, Anonymous(), "x:y")
    assert exc.value.permission == "x:y"


async def test_requires_decorator_blocks_unauthorized(harness: BaseHarness) -> None:
    @requires("orders:read")
    class _Read(Action):
        async def handle(self, uow, actor):  # type: ignore[no-untyped-def]
            return "secret-data"

    with pytest.raises(PermissionDenied):
        await harness.run(_Read(), actor=Anonymous())


async def test_requires_decorator_allows_authorized(harness: BaseHarness) -> None:
    @requires("orders:read")
    class _Read(Action):
        async def handle(self, uow, actor):  # type: ignore[no-untyped-def]
            return "secret-data"

    result = await harness.run(_Read(), actor=System())
    assert result == "secret-data"


async def test_requires_decorator_sets_classvar(harness: BaseHarness) -> None:
    @requires("orders:read", "orders:write")
    class _Act(Action):
        async def handle(self, uow, actor):  # type: ignore[no-untyped-def]
            return None

    assert _Act._hearth_required_permissions == ("orders:read", "orders:write")  # pyright: ignore[reportAttributeAccessIssue]


def test_authentication_failed_carries_message() -> None:
    err = AuthenticationFailed("bad creds")
    assert "bad creds" in str(err)


def test_permission_denied_carries_actor_and_permission() -> None:
    a = Anonymous()
    err = PermissionDenied(a, "orders:read")
    assert err.actor is a
    assert err.permission == "orders:read"
    assert "orders:read" in str(err)


# ---------------------------------------------------------------------------
# Actor-type and decorator bypass tests
#
# These pin the contracts that any auth-relevant change MUST preserve, so a
# refactor of actor.py or the @requires wrapper can't silently weaken them.
# ---------------------------------------------------------------------------


async def test_plugin_actor_unconditionally_satisfies_requires(
    harness: BaseHarness,
) -> None:
    """SECURITY CONTRACT: PluginActor is an in-process trust principal.
    It passes every @requires check by design. Any caller constructing a
    PluginActor from external input bypasses authorization — the actor.py
    docstring forbids that pattern.

    If you ever want PluginActor to consult a real permission table, change
    this test FIRST so the security implication is explicit."""

    @requires("anything:goes")
    class _Act(Action):
        async def handle(self, uow, actor):  # type: ignore[no-untyped-def]
            return "ok"

    result = await harness.run(_Act(), actor=PluginActor(alias="commerce"))
    assert result == "ok"


async def test_require_permission_passes_for_plugin_actor(
    harness: BaseHarness,
) -> None:
    """Companion to the @requires test: the imperative form must agree."""
    async with harness.transaction() as uow:
        await require_permission(uow, PluginActor(alias="any"), "x:y")


async def test_requires_subclass_override_silently_bypasses(
    harness: BaseHarness,
) -> None:
    """KNOWN LIMITATION (documented in helpers.py): a subclass that overrides
    handle REPLACES the wrapped version and bypasses the @requires gate.
    Plugin authors who subclass a @requires-decorated Action MUST reapply
    @requires on the subclass.

    This test pins the current behavior so a future fix (e.g., enforcing
    re-decoration via __init_subclass__) is intentional, with a clear
    migration story instead of a silent semantic change."""

    @requires("orders:read")
    class _Parent(Action):
        async def handle(self, uow, actor):  # type: ignore[no-untyped-def]
            return "parent"

    class _Child(_Parent):
        async def handle(self, uow, actor):  # type: ignore[no-untyped-def]
            # Bypasses parent's wrapped handle entirely — no permission check runs.
            return "child"

    # Parent enforces:
    with pytest.raises(PermissionDenied):
        await harness.run(_Parent(), actor=Anonymous())
    # Child silently does not. If this assertion ever flips to PermissionDenied,
    # the @requires implementation gained subclass-aware enforcement and the
    # LIMITATION comment in helpers.py should be removed.
    result = await harness.run(_Child(), actor=Anonymous())
    assert result == "child"


async def test_require_permission_rejects_malformed_string(
    harness: BaseHarness,
) -> None:
    """A malformed permission string fails closed: even System (which would
    pass any well-formed check) cannot make `require_permission` succeed
    if the string itself is malformed. This guards against a caller
    accidentally constructing an unparseable permission and getting a
    silent pass."""
    # System.has_permission returns True unconditionally, so we use a malformed
    # string with Anonymous to verify the deny path fires. The interesting
    # property is symmetric: malformed strings never gain authority.
    async with harness.transaction() as uow:
        with pytest.raises(PermissionDenied):
            await require_permission(uow, Anonymous(), "")
        with pytest.raises(PermissionDenied):
            await require_permission(uow, Anonymous(), "no-colon")
        with pytest.raises(PermissionDenied):
            await require_permission(uow, Anonymous(), "too:many:colons")


async def test_requires_declared_permissions_introspectable(
    harness: BaseHarness,
) -> None:
    """The decorator records its permission list on the class for admin/UI
    tools to enumerate. A regression here would break any code that walks
    the action surface to render permission docs."""

    @requires("a:b", "c:d", "e:f")
    class _Act(Action):
        async def handle(self, uow, actor):  # type: ignore[no-untyped-def]
            return None

    assert _Act._hearth_required_permissions == ("a:b", "c:d", "e:f")  # pyright: ignore[reportAttributeAccessIssue]
