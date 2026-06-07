"""Shared pytest fixtures for hearth-web tests."""

import os
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncEngine

# Force import of auth plugin modules so subclasses are registered
import hearth_auth.actions
import hearth_auth.entities
import hearth_auth.events  # noqa: F401 - imported for side-effect of registering subclasses
from hearth.kernel._engine import make_async_engine
from hearth.kernel.registry import PluginInfo, Registry
from hearth.testing import BaseHarness
from hearth_auth.entities import User
from hearth_auth.testing import seed_admin


@dataclass
class WebFixture:
    """Fixture bundle for web tests."""

    client: TestClient
    harness: BaseHarness
    admin: User


def _collect_subclasses(base: type, top_package: str) -> list[type]:
    """Recursively collect non-abstract subclasses from a top-level package.

    Uses public __subclasses__() and filters by __module__ to avoid
    picking up dynamically created plugin base classes.
    """
    seen: set[type] = set()
    out: list[type] = []

    def walk(cls: type) -> None:
        for sub in cls.__subclasses__():
            if sub in seen:
                continue
            seen.add(sub)
            mod = getattr(sub, "__module__", "") or ""
            if (mod == top_package or mod.startswith(top_package + ".")) and not sub.__dict__.get(
                "__abstract__", False
            ):
                out.append(sub)
            walk(sub)

    walk(base)
    return out


def make_registry() -> Registry:
    """Build a minimal registry containing only the auth plugin.

    Uses public __subclasses__() to discover Entity/Action/Event classes
    registered under the hearth_auth package.
    """
    from hearth.primitives.action import Action
    from hearth.primitives.entity import Entity
    from hearth.primitives.event import Event

    entities = _collect_subclasses(Entity, "hearth_auth")
    actions = _collect_subclasses(Action, "hearth_auth")
    events = _collect_subclasses(Event, "hearth_auth")

    auth_info = PluginInfo(
        alias="auth",
        package="hearth-auth",
        version="0.0.1",
        module="hearth_auth",
        install_path="<test>",
        entities=entities,
        actions=actions,
        events=events,
    )

    return Registry(plugins={"auth": auth_info}, _topological_order=["auth"])


@asynccontextmanager
async def _web_app_context(
    engine: AsyncEngine,
    registry: Registry,
    modules: list,
) -> AsyncGenerator[tuple[TestClient, BaseHarness, User]]:
    """Create a web app context with harness, admin user, and test client."""
    from hearth_web import create_app

    harness = BaseHarness(engine)
    await harness.setup()

    admin = await seed_admin(harness)

    app = create_app(engine=engine, registry=registry, modules=modules)

    with TestClient(app) as client:
        yield client, harness, admin

    await engine.dispose()


@pytest.fixture
async def web() -> AsyncGenerator[WebFixture]:
    """Session-scoped web fixture with shared in-memory SQLite engine.

    Sets up a single engine, harness, admin user, and FastAPI test client
    for the entire test session. Uses the auth plugin registry.
    """
    os.environ["HEARTH_WEB_SECRET_KEY"] = "test-secret-key-for-testing-only"

    engine = make_async_engine("sqlite:///:memory:")
    registry = make_registry()

    async with _web_app_context(engine, registry, modules=[]) as (
        client,
        harness,
        admin,
    ):
        yield WebFixture(client=client, harness=harness, admin=admin)


@pytest.fixture
async def web_factory() -> AsyncGenerator[Callable[[list | None], Awaitable[WebFixture]]]:
    """Factory fixture for creating isolated web app instances.

    Each call to the returned builder creates a completely independent
    engine, harness, and app — critical to avoid StaticPool deadlock
    when multiple apps share the same in-memory SQLite connection.
    """
    os.environ["HEARTH_WEB_SECRET_KEY"] = "test-secret-key-for-testing-only"

    engines: list[AsyncEngine] = []
    clients: list[TestClient] = []

    async def build(modules: list | None = None) -> WebFixture:
        engine = make_async_engine("sqlite:///:memory:")
        engines.append(engine)
        registry = make_registry()

        harness = BaseHarness(engine)
        await harness.setup()

        admin = await seed_admin(harness)

        from hearth_web import create_app

        app = create_app(engine=engine, registry=registry, modules=modules or [])
        client = TestClient(app)
        client.__enter__()
        clients.append(client)

        return WebFixture(client=client, harness=harness, admin=admin)

    try:
        yield build
    finally:
        for client in clients:
            client.__exit__(None, None, None)
        for engine in engines:
            await engine.dispose()
