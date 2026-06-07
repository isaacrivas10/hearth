import pytest
from fastapi import APIRouter, Depends

from hearth import EntityId
from hearth_auth.actions import (
    AssignPermissionToRole,
    CreateApiKey,
    CreatePermission,
    CreateRole,
    RevokeApiKey,
)
from hearth_auth.entities.user import User
from hearth_auth.values import HashedSecret, PermissionName
from hearth_commons import EmailAddress
from hearth_web.extensions import WebModule
from hearth_web.security import current_actor, request_uow, requires_permission


def _whoami_module() -> WebModule:
    """A diagnostic module (test-only) whose route reports the resolved actor."""
    r = APIRouter()

    @r.get("/whoami")
    async def whoami(actor=Depends(current_actor)):  # noqa: B008
        return {"kind": actor.actor_kind}

    return WebModule(name="diag", router=r)


@pytest.mark.asyncio
async def test_anonymous_when_no_session(web_factory):
    fx = await web_factory([_whoami_module()])
    assert fx.client.get("/diag/whoami").json()["kind"] == "anonymous"


def _csrf_token(resp):
    """Extract the RAW CSRF token from the login form.

    The cookie holds the HMAC-signed token; the raw token to submit lives in
    the hidden form field. Always read it from the rendered HTML.
    """
    return resp.text.split('name="csrf_token" value="')[1].split('"')[0]


@pytest.mark.asyncio
async def test_login_success_sets_session(web):
    get_resp = web.client.get("/login")
    csrf_token = _csrf_token(get_resp)
    resp = web.client.post(
        "/login",
        data={"email": "admin@x.com", "password": "adminpass", "csrf_token": csrf_token},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert "session" in resp.cookies or resp.headers.get("set-cookie")


@pytest.mark.asyncio
async def test_login_failure_indistinguishable(web):
    get_resp = web.client.get("/login")
    csrf_token = _csrf_token(get_resp)
    resp = web.client.post(
        "/login",
        data={"email": "admin@x.com", "password": "wrong", "csrf_token": csrf_token},
    )
    assert resp.status_code == 401
    assert "invalid credentials" in resp.text.lower()


@pytest.mark.asyncio
async def test_login_next_open_redirect_rejected(web):
    get_resp = web.client.get("/login")
    csrf_token = _csrf_token(get_resp)
    resp = web.client.post(
        "/login",
        data={
            "email": "admin@x.com",
            "password": "adminpass",
            "next": "https://evil.example/x",
            "csrf_token": csrf_token,
        },
        follow_redirects=False,
    )
    assert resp.headers["location"] == "/admin"


@pytest.mark.asyncio
async def test_logout_clears_session(web):
    get_resp = web.client.get("/login")
    csrf_token = _csrf_token(get_resp)
    web.client.post(
        "/login", data={"email": "admin@x.com", "password": "adminpass", "csrf_token": csrf_token}
    )
    get_resp2 = web.client.get("/login")
    csrf_token2 = _csrf_token(get_resp2)
    resp = web.client.post("/logout", data={"csrf_token": csrf_token2}, follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"


@pytest.mark.asyncio
async def test_login_csrf_rejected(web):
    resp = web.client.post("/login", data={"email": "admin@x.com", "password": "adminpass"})
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_logout_csrf_rejected(web):
    get_resp = web.client.get("/login")
    csrf_token = _csrf_token(get_resp)
    web.client.post(
        "/login", data={"email": "admin@x.com", "password": "adminpass", "csrf_token": csrf_token}
    )
    resp = web.client.post("/logout")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_api_key_header_authenticates(web_factory):
    fx = await web_factory([_whoami_module()])
    _api_key, plaintext = await fx.harness.run(
        CreateApiKey(name="ci", permissions=[], owner_id=fx.admin.id)
    )
    resp = fx.client.get("/diag/whoami", headers={"Authorization": f"Bearer {plaintext}"})
    assert resp.json()["kind"] == "api_key"


@pytest.mark.asyncio
async def test_session_takes_precedence_after_login(web_factory):
    fx = await web_factory([_whoami_module()])
    get_resp = fx.client.get("/login")
    csrf_token = _csrf_token(get_resp)
    fx.client.post(
        "/login",
        data={"email": "admin@x.com", "password": "adminpass", "csrf_token": csrf_token},
    )
    resp = fx.client.get("/diag/whoami")
    assert resp.json()["kind"] == "user"


@pytest.mark.asyncio
async def test_api_key_with_permissions_accesses_gated_route(web_factory):
    r = APIRouter()

    @r.get("/widgets")
    async def widgets(_: None = Depends(requires_permission("widgets:read"))):
        return {"widgets": ["a", "b"]}

    mod = WebModule(name="diag", router=r)

    fx = await web_factory([mod])
    perm = await fx.harness.run(
        CreatePermission(name=PermissionName(resource="widgets", action="read"))
    )
    role = await fx.harness.run(CreateRole(name="widget-reader"))
    await fx.harness.run(AssignPermissionToRole(role_id=role.id, permission_id=perm.id))
    api_key, plaintext = await fx.harness.run(
        CreateApiKey(name="ci", permissions=[PermissionName(resource="widgets", action="read")])
    )

    resp = fx.client.get("/diag/widgets", headers={"Authorization": f"Bearer {plaintext}"})
    assert resp.status_code == 200
    assert resp.json() == {"widgets": ["a", "b"]}


@pytest.mark.asyncio
async def test_api_key_without_permission_denied(web_factory):
    r = APIRouter()

    @r.get("/widgets")
    async def widgets(_: None = Depends(requires_permission("widgets:read"))):
        return {"widgets": []}

    mod = WebModule(name="diag", router=r)

    fx = await web_factory([mod])
    api_key, plaintext = await fx.harness.run(CreateApiKey(name="ci", permissions=[]))

    resp = fx.client.get("/diag/widgets", headers={"Authorization": f"Bearer {plaintext}"})
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_logout_clears_session_completely(web_factory):
    from hearth_web.security import current_actor

    r = APIRouter()

    @r.get("/whoami")
    async def whoami(actor=Depends(current_actor)):
        return {"kind": actor.actor_kind}

    mod = WebModule(name="diag", router=r)

    fx = await web_factory([mod])
    get_resp = fx.client.get("/login")
    csrf_token = _csrf_token(get_resp)
    fx.client.post(
        "/login", data={"email": "admin@x.com", "password": "adminpass", "csrf_token": csrf_token}
    )

    resp = fx.client.get("/diag/whoami")
    assert resp.json()["kind"] == "user"

    get_resp2 = fx.client.get("/login")
    csrf_token2 = _csrf_token(get_resp2)
    fx.client.post("/logout", data={"csrf_token": csrf_token2})

    resp = fx.client.get("/diag/whoami")
    assert resp.json()["kind"] == "anonymous"
    assert "session" not in fx.client.cookies


@pytest.mark.asyncio
async def test_concurrent_requests_isolated_uow(web_factory):
    r = APIRouter()

    @r.post("/create-user")
    async def create_user(uow=Depends(request_uow)):
        user = User(
            email=EmailAddress(raw=f"user-{EntityId.new()}@x.com"),
            password=HashedSecret.from_plaintext("pw"),
        )
        await uow.save(user)
        return {"id": str(user.id)}

    @r.get("/count")
    async def count(uow=Depends(request_uow)):

        return {"count": await uow.query(User).count()}

    mod = WebModule(name="diag", router=r)

    fx = await web_factory([mod])

    # Get baseline count (admin user from fixture)
    baseline = fx.client.get("/diag/count")
    base_count = baseline.json()["count"]

    r1 = fx.client.post("/diag/create-user")
    r2 = fx.client.post("/diag/create-user")
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r1.json()["id"] != r2.json()["id"]

    cnt = fx.client.get("/diag/count")
    assert cnt.json()["count"] == base_count + 2


@pytest.mark.asyncio
async def test_revoked_api_key_rejected(web_factory):
    r = APIRouter()

    @r.get("/secret")
    async def secret(_: None = Depends(requires_permission("x:y"))):
        return {"ok": True}

    mod = WebModule(name="diag", router=r)

    fx = await web_factory([mod])
    await fx.harness.run(CreatePermission(name=PermissionName(resource="x", action="y")))
    api_key, plaintext = await fx.harness.run(
        CreateApiKey(name="ci", permissions=[PermissionName(resource="x", action="y")])
    )

    resp = fx.client.get("/diag/secret", headers={"Authorization": f"Bearer {plaintext}"})
    assert resp.status_code == 200

    await fx.harness.run(RevokeApiKey(api_key_id=api_key.id))

    resp = fx.client.get("/diag/secret", headers={"Authorization": f"Bearer {plaintext}"})
    assert resp.status_code == 401
