"""Tests for nav assembly and slot rendering."""

import pytest
from fastapi import APIRouter, Depends, Request
from starlette.testclient import TestClient

from hearth import Anonymous
from hearth_web.extensions import NavItem, SlotContribution, WebModule
from hearth_web.rendering import KNOWN_SECTIONS, build_jinja_env, build_nav, page_context, render
from hearth_web.security import make_check
from hearth_web.slots import build_slot_registry, make_render_slot


def test_nav_items_merge_and_order():
    a = WebModule(name="a", nav=[NavItem(label="Z", path="/a", section="operations", order=20)])
    b = WebModule(name="b", nav=[NavItem(label="A", path="/b", section="operations", order=10)])
    nav = build_nav([a, b], admin_nav=[])
    ops = [i.label for i in nav if i.section == "operations"]
    assert ops == ["A", "Z"]


def test_nav_unknown_section_falls_back(caplog):
    m = WebModule(name="a", nav=[NavItem(label="X", path="/a", section="nope")])
    nav = build_nav([m], admin_nav=[])
    assert nav[0].section == "extensions"
    assert "unknown nav section" in caplog.text
    assert "extensions" in KNOWN_SECTIONS


@pytest.mark.asyncio
async def test_slot_contribution_renders(web, tmp_path):
    tdir = tmp_path / "templates"
    tdir.mkdir()
    (tdir / "w.html").write_text("WIDGET:{{ value }}")
    contrib = SlotContribution(slot="admin.dashboard.widgets", template="diag/w.html")
    mod = WebModule(
        name="diag", templates_dir="templates", package_dir=str(tmp_path), contributions=[contrib]
    )
    env = build_jinja_env([mod], override_dir=None)
    registry = build_slot_registry([mod])
    async with web.harness.transaction() as uow:
        check = make_check(uow, Anonymous())
        render_slot = make_render_slot(registry, uow, Anonymous(), check, env)
        out = await render_slot("admin.dashboard.widgets", value="hi")
        assert "WIDGET:hi" in out


@pytest.mark.asyncio
async def test_slot_render_failure_isolated(web, tmp_path, caplog):
    tdir = tmp_path / "templates"
    tdir.mkdir()
    (tdir / "ok.html").write_text("OK")

    async def boom(uow, actor, **ctx):
        raise RuntimeError("provider down")

    good = SlotContribution(slot="s", template="diag/ok.html", order=2)
    bad = SlotContribution(slot="s", template="diag/ok.html", provider=boom, order=1)
    mod = WebModule(
        name="diag", templates_dir="templates", package_dir=str(tmp_path), contributions=[good, bad]
    )
    env = build_jinja_env([mod], override_dir=None)
    registry = build_slot_registry([mod])
    async with web.harness.transaction() as uow:
        check = make_check(uow, Anonymous())
        rs = make_render_slot(registry, uow, Anonymous(), check, env)
        out = await rs("s")
        assert "OK" in out  # good one survives
        assert "provider down" in caplog.text


@pytest.mark.asyncio
async def test_page_renders_slot_through_request(web, tmp_path, monkeypatch):
    tdir = tmp_path / "templates"
    tdir.mkdir()
    (tdir / "host.html").write_text("HOST[{{ render_slot('s') }}]")
    (tdir / "frag.html").write_text("FRAG")
    r = APIRouter()

    @r.get("/host")
    async def host(request: Request, _: None = Depends(page_context)):
        return await render(request, "diag/host.html")

    mod = WebModule(
        name="diag",
        router=r,
        templates_dir="templates",
        package_dir=str(tmp_path),
        contributions=[SlotContribution(slot="s", template="diag/frag.html")],
    )
    monkeypatch.setenv("HEARTH_WEB_SECRET_KEY", "test-secret-key")
    from hearth.kernel._engine import make_async_engine  # pyright: ignore[reportPrivateUsage]
    from hearth.testing import BaseHarness
    from hearth_auth.testing import seed_admin
    from hearth_web.app import create_app

    from .conftest import make_registry

    engine = make_async_engine("sqlite:///:memory:")
    harness = BaseHarness(engine)
    await harness.setup()
    await seed_admin(harness)
    app = create_app(engine=engine, registry=make_registry(), modules=[mod])
    with TestClient(app) as c:
        assert c.get("/diag/host").text == "HOST[FRAG]"
    await engine.dispose()


@pytest.mark.asyncio
async def test_slot_contributions_ordered(web, tmp_path):
    tdir = tmp_path / "templates"
    tdir.mkdir()
    (tdir / "a.html").write_text("A")
    (tdir / "b.html").write_text("B")
    from hearth import Anonymous
    from hearth_web.rendering import build_jinja_env

    mod = WebModule(
        name="diag",
        templates_dir="templates",
        package_dir=str(tmp_path),
        contributions=[
            SlotContribution(slot="s", template="diag/b.html", order=2),
            SlotContribution(slot="s", template="diag/a.html", order=1),
        ],
    )
    env = build_jinja_env([mod], override_dir=None)
    reg = build_slot_registry([mod])
    async with web.harness.transaction() as uow:
        from hearth_web.security import make_check

        rs = make_render_slot(reg, uow, Anonymous(), make_check(uow, Anonymous()), env)
        assert await rs("s") == "AB"


@pytest.mark.asyncio
async def test_slot_contribution_permission_gated(web, tmp_path):
    tdir = tmp_path / "templates"
    tdir.mkdir()
    (tdir / "a.html").write_text("SECRET")
    from hearth import Anonymous
    from hearth_web.rendering import build_jinja_env

    mod = WebModule(
        name="diag",
        templates_dir="templates",
        package_dir=str(tmp_path),
        contributions=[SlotContribution(slot="s", template="diag/a.html", permission="x:write")],
    )
    env = build_jinja_env([mod], override_dir=None)
    reg = build_slot_registry([mod])
    async with web.harness.transaction() as uow:
        from hearth_web.security import make_check

        rs = make_render_slot(reg, uow, Anonymous(), make_check(uow, Anonymous()), env)
        assert await rs("s") == ""  # anonymous lacks x:write


@pytest.mark.asyncio
async def test_htmx_slot_partial_render(web, tmp_path, monkeypatch):
    """Slot contributions work via HTMX (HX-Request header)."""
    from fastapi import APIRouter, Depends, Request

    from hearth_web.extensions import SlotContribution, WebModule
    from hearth_web.rendering import page_context, render

    tdir = tmp_path / "templates"
    tdir.mkdir()
    (tdir / "widget.html").write_text("<div class='widget'>WIDGET: {{ value }}</div>")

    r = APIRouter()

    @r.get("/htmx-widget")
    async def htmx_widget(request: Request, _: None = Depends(page_context)):
        return await render(request, "diag/widget.html", value="htmx-value")

    mod = WebModule(
        name="diag",
        router=r,
        templates_dir="templates",
        package_dir=str(tmp_path),
        contributions=[
            SlotContribution(slot="admin.dashboard.widgets", template="diag/widget.html")
        ],
    )

    monkeypatch.setenv("HEARTH_WEB_SECRET_KEY", "test-secret")
    from hearth.kernel._engine import make_async_engine  # pyright: ignore[reportPrivateUsage]
    from hearth.testing import BaseHarness
    from hearth_auth.testing import seed_admin
    from hearth_web.app import create_app

    from .conftest import make_registry

    engine = make_async_engine("sqlite:///:memory:")
    harness = BaseHarness(engine)
    await harness.setup()
    await seed_admin(harness)
    app = create_app(engine=engine, registry=make_registry(), modules=[mod])

    from starlette.testclient import TestClient

    with TestClient(app) as client:
        get_resp = client.get("/login")
        csrf_token = get_resp.cookies.get("csrf_token")
        if csrf_token is None:
            csrf_token = get_resp.text.split('name="csrf_token" value="')[1].split('"')[0]
        client.post(
            "/login",
            data={"email": "admin@x.com", "password": "adminpass", "csrf_token": csrf_token},
        )

        resp = client.get("/diag/htmx-widget", headers={"HX-Request": "true"})
        assert resp.status_code == 200
        assert "WIDGET: htmx-value" in resp.text
        assert "<div class='widget'>" in resp.text
        assert "<html" not in resp.text.lower()

    await engine.dispose()


@pytest.mark.asyncio
async def test_theme_toggle_localstorage_roundtrip(web):
    """Theme preference persists via localStorage (client-side, verified via template)."""
    from starlette.testclient import TestClient

    from hearth.kernel._engine import make_async_engine  # pyright: ignore[reportPrivateUsage]
    from hearth.testing import BaseHarness
    from hearth_auth.testing import seed_admin
    from hearth_web.app import create_app

    from .conftest import make_registry

    engine = make_async_engine("sqlite:///:memory:")
    harness = BaseHarness(engine)
    await harness.setup()
    await seed_admin(harness)
    app = create_app(engine=engine, registry=make_registry(), modules=[])

    with TestClient(app) as client:
        get_resp = client.get("/login")
        csrf_token = get_resp.cookies.get("csrf_token")
        if csrf_token is None:
            csrf_token = get_resp.text.split('name="csrf_token" value="')[1].split('"')[0]
        client.post(
            "/login",
            data={"email": "admin@x.com", "password": "adminpass", "csrf_token": csrf_token},
        )

        resp = client.get("/admin")
        assert 'data-theme="light"' in resp.text or 'data-theme="dark"' in resp.text
        assert "toggleTheme" in resp.text
        js = client.get("/static/theme-toggle.js")
        assert js.status_code == 200
        assert 'localStorage.setItem("theme"' in js.text

    await engine.dispose()
