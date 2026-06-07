from dataclasses import dataclass

import pytest
import pytest_asyncio
from fastapi import APIRouter, Depends
from starlette.testclient import TestClient

from hearth_web.extensions import NavItem, WebModule
from hearth_web.security import requires_permission


def _gated_module():
    r = APIRouter()

    @r.get("/secret")
    async def secret(_: None = Depends(requires_permission("widgets:read"))):
        return {"ok": True}

    return WebModule(name="diag", router=r)


def _csrf_token(resp):
    """Extract the RAW CSRF token from the login form.

    The cookie holds the HMAC-signed token; the raw token to submit lives in
    the hidden form field. Always read it from the rendered HTML.
    """
    return resp.text.split('name="csrf_token" value="')[1].split('"')[0]


# Build an ISOLATED app with the gated module (own engine — never share the
# web fixture's StaticPool engine across two apps). Seed a no-grant user too,
# so the 403 test stays fully synchronous.
@pytest_asyncio.fixture
async def gated(web_factory):
    from pydantic import SecretStr

    from hearth_auth.actions import CreateUser
    from hearth_commons import EmailAddress

    fx = await web_factory([_gated_module()])
    await fx.harness.run(
        CreateUser(
            email=EmailAddress(raw="nobody@x.com"), password=SecretStr("pw"), display_name="Nobody"
        )
    )
    return fx


def test_route_gate_allows_with_permission(gated):
    # admin has *:* so widgets:read passes
    get_resp = gated.client.get("/login")
    csrf_token = _csrf_token(get_resp)
    gated.client.post(
        "/login", data={"email": "admin@x.com", "password": "adminpass", "csrf_token": csrf_token}
    )
    assert gated.client.get("/diag/secret").status_code == 200


def test_route_gate_denies_anonymous_redirect_or_401(gated):
    r = gated.client.get("/diag/secret", headers={"accept": "text/html"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/login")
    r2 = gated.client.get("/diag/secret", headers={"HX-Request": "true"})
    assert r2.status_code == 401


def test_route_gate_denies_authenticated_403(gated):
    # a fresh user with no grants → authenticated but forbidden → 403
    get_resp = gated.client.get("/login")
    csrf_token = _csrf_token(get_resp)
    gated.client.post(
        "/login", data={"email": "nobody@x.com", "password": "pw", "csrf_token": csrf_token}
    )
    assert gated.client.get("/diag/secret").status_code == 403


@pytest.mark.asyncio
async def test_can_filter_gates_component(web):
    env = web.client.app.state.jinja_env
    from hearth import Anonymous
    from hearth_web.security import make_check

    async with web.harness.transaction() as uow:
        check = make_check(uow, Anonymous())
        tmpl = env.from_string('{% if "x:write" | can %}YES{% else %}NO{% endif %}')
        out = await tmpl.render_async(check=check)
        assert out == "NO"


@pytest.mark.asyncio
async def test_unknown_plugin_permission_needs_no_web_change(web):
    # brand-new permission string nobody granted → can() is False, no error.
    from hearth import Anonymous
    from hearth_web.security import make_check

    async with web.harness.transaction() as uow:
        check = make_check(uow, Anonymous())
        assert await check("totally:new") is False


@dataclass
class WebFixture:
    client: TestClient
    harness: "BaseHarness"
    admin: "User"


@pytest.mark.asyncio
async def test_nav_hidden_without_permission(web_factory):
    """Nav items gated by permission are hidden from users lacking it."""
    from pydantic import SecretStr

    from hearth.kernel._engine import make_async_engine
    from hearth.testing import BaseHarness
    from hearth_auth.actions import CreateUser
    from hearth_auth.testing import seed_admin
    from hearth_commons import EmailAddress
    from hearth_web.app import create_app

    from .conftest import make_registry

    async def build_app(modules):
        engine = make_async_engine("sqlite:///:memory:")
        harness = BaseHarness(engine)
        await harness.setup()
        admin = await seed_admin(harness)
        await harness.run(
            CreateUser(
                email=EmailAddress(raw="nobody@x.com"),
                password=SecretStr("pw"),
                display_name="Nobody",
            )
        )
        app = create_app(engine=engine, registry=make_registry(), modules=modules)
        client = TestClient(app)
        return WebFixture(client=client, harness=harness, admin=admin)

    # Module with a gated nav item
    r = APIRouter()
    mod = WebModule(
        name="secret",
        router=r,
        nav=[
            NavItem(label="Secret", path="/secret", section="extensions", permission="nobody:has")
        ],
    )

    # Test as admin (has *:* permission)
    admin_fx = await build_app([mod])
    get_resp = admin_fx.client.get("/login")
    csrf_token = get_resp.cookies.get("csrf_token")
    if csrf_token is None:
        csrf_token = get_resp.text.split('name="csrf_token" value="')[1].split('"')[0]
    admin_fx.client.post(
        "/login", data={"email": "admin@x.com", "password": "adminpass", "csrf_token": csrf_token}
    )
    r = admin_fx.client.get("/admin")
    assert "Secret" in r.text  # admin sees it

    # Test as no-grant user
    user_fx = await build_app([mod])
    get_resp = user_fx.client.get("/login")
    csrf_token = get_resp.cookies.get("csrf_token")
    if csrf_token is None:
        csrf_token = get_resp.text.split('name="csrf_token" value="')[1].split('"')[0]
    user_fx.client.post(
        "/login", data={"email": "nobody@x.com", "password": "pw", "csrf_token": csrf_token}
    )
    r = user_fx.client.get("/admin")
    assert "Secret" not in r.text  # user doesn't see it

    # Cleanup
    for fx in [admin_fx, user_fx]:
        await fx.harness.teardown()


@pytest.mark.asyncio
async def test_make_check_memoizes_per_permission():
    from hearth_web.security import make_check

    class _CountingActor:
        actor_kind = "counting"

        def __init__(self) -> None:
            self.calls = 0

        async def has_permission(self, uow, permission):  # noqa: ANN001
            self.calls += 1
            return True

    actor = _CountingActor()
    check = make_check(uow=None, actor=actor)

    assert await check("admin:read") is True
    assert await check("admin:read") is True
    assert actor.calls == 1  # same permission resolved once, then cached

    assert await check("other:read") is True
    assert actor.calls == 2  # a distinct permission triggers a fresh resolution
