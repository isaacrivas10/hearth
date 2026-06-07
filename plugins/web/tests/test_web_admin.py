"""Tests for hearth-web admin endpoints."""

import pytest


def test_health_no_auth(web):
    resp = web.client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_counts_from_registry(web):
    from hearth_web.admin.introspection import counts
    from plugins.web.tests.conftest import make_registry

    c = counts(make_registry())
    assert c["plugins"] == 1
    assert c["entities"] >= 1  # auth ships User, Role, etc.


def test_mermaid_includes_tables(web):
    from hearth_web.admin.introspection import schema_mermaid
    from plugins.web.tests.conftest import make_registry

    text = schema_mermaid(make_registry())
    assert text.startswith("erDiagram") or text.startswith("graph")
    assert "users" in text.lower() or "user" in text.lower()


def _login(web):
    get_resp = web.client.get("/login")
    csrf_token = (
        get_resp.cookies.get("csrf_token")
        or get_resp.text.split('name="csrf_token" value="')[1].split('"')[0]
    )
    web.client.post(
        "/login", data={"email": "admin@x.com", "password": "adminpass", "csrf_token": csrf_token}
    )


def test_root_redirects_to_admin(web):
    r = web.client.get("/", follow_redirects=False)
    assert r.status_code in (303, 307) and r.headers["location"] == "/admin"


def test_dashboard_renders_counts(web):
    _login(web)
    r = web.client.get("/admin")
    assert r.status_code == 200
    assert "Plugins" in r.text


def test_plugins_page_lists_registry(web):
    _login(web)
    r = web.client.get("/admin/plugins")
    assert r.status_code == 200 and "auth" in r.text


def test_entity_detail_shows_fields(web):
    _login(web)
    r = web.client.get("/admin/entities/auth/User")
    assert r.status_code == 200 and "email" in r.text


def test_schema_page_renders_graph(web):
    _login(web)
    r = web.client.get("/admin/schema")
    assert r.status_code == 200 and "erDiagram" in r.text


def test_admin_requires_auth(web):
    # Clear any existing session
    web.client.cookies.clear()
    r = web.client.get("/admin", headers={"accept": "text/html"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/login")


def test_branding_env_vars_rendered(web, monkeypatch):
    from starlette.testclient import TestClient

    from hearth.kernel._engine import make_async_engine  # pyright: ignore[reportPrivateUsage]
    from hearth.testing import BaseHarness
    from hearth_auth.testing import seed_admin
    from hearth_web.app import create_app
    from plugins.web.tests.conftest import make_registry

    monkeypatch.setenv("HEARTH_WEB_SECRET_KEY", "test-secret")
    monkeypatch.setenv("HEARTH_WEB_BRAND_NAME", "MyStore")
    monkeypatch.setenv("HEARTH_WEB_PRIMARY_COLOR", "#ff6b35")
    monkeypatch.setenv("HEARTH_WEB_LOGO_URL", "/static/logo.svg")

    engine = make_async_engine("sqlite:///:memory:")
    harness = BaseHarness(engine)
    import asyncio

    asyncio.run(harness.setup())
    asyncio.run(seed_admin(harness))
    app = create_app(engine=engine, registry=make_registry(), modules=[])
    with TestClient(app) as client:
        get_resp = client.get("/login")
        csrf_token = (
            get_resp.cookies.get("csrf_token")
            or get_resp.text.split('name="csrf_token" value="')[1].split('"')[0]
        )
        client.post(
            "/login",
            data={"email": "admin@x.com", "password": "adminpass", "csrf_token": csrf_token},
        )
        r = client.get("/admin")
        assert "MyStore" in r.text
        assert "#ff6b35" in r.text
        assert "/static/logo.svg" in r.text
    asyncio.run(engine.dispose())


def test_template_override_dir(web, tmp_path, monkeypatch):
    from starlette.testclient import TestClient

    from hearth.kernel._engine import make_async_engine  # pyright: ignore[reportPrivateUsage]
    from hearth.testing import BaseHarness
    from hearth_auth.testing import seed_admin
    from hearth_web.app import create_app
    from plugins.web.tests.conftest import make_registry

    override = tmp_path / "templates"
    override.mkdir()
    (override / "admin").mkdir(parents=True)
    (override / "admin" / "dashboard.html").write_text("OVERRIDE WORKS")

    monkeypatch.setenv("HEARTH_WEB_SECRET_KEY", "test-secret")
    monkeypatch.setenv("HEARTH_WEB_TEMPLATE_DIR", str(override))

    engine = make_async_engine("sqlite:///:memory:")
    harness = BaseHarness(engine)
    import asyncio

    asyncio.run(harness.setup())
    asyncio.run(seed_admin(harness))
    app = create_app(engine=engine, registry=make_registry(), modules=[])
    with TestClient(app) as client:
        get_resp = client.get("/login")
        csrf_token = (
            get_resp.cookies.get("csrf_token")
            or get_resp.text.split('name="csrf_token" value="')[1].split('"')[0]
        )
        client.post(
            "/login",
            data={"email": "admin@x.com", "password": "adminpass", "csrf_token": csrf_token},
        )
        r = client.get("/admin")
        assert "OVERRIDE WORKS" in r.text
    asyncio.run(engine.dispose())


@pytest.mark.asyncio
async def test_actions_page_renders(web):
    _login(web)
    r = web.client.get("/admin/actions")
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_events_page_renders(web):
    _login(web)
    r = web.client.get("/admin/events")
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_db_page_renders_with_outbox_and_tables(web):
    _login(web)
    r = web.client.get("/admin/db")
    assert r.status_code == 200
    # The kernel outbox table is created by the test harness, so it is present.
    assert "_hearth_outbox" in r.text
    assert "Outbox rows" in r.text
