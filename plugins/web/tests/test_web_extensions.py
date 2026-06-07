import pytest
import pytest_asyncio
from fastapi import APIRouter
from starlette.testclient import TestClient

from hearth.kernel._engine import make_async_engine  # pyright: ignore[reportPrivateUsage]
from hearth.testing import BaseHarness
from hearth_auth.testing import seed_admin
from hearth_web.extensions import NavItem, SlotContribution, WebModule, load_web_modules

from .conftest import make_registry


def test_webmodule_defaults():
    m = WebModule(name="inventory", router=APIRouter())
    assert m.name == "inventory"
    assert m.nav == []
    assert m.contributions == []
    assert m.templates_dir is None


def test_navitem_fields():
    n = NavItem(
        label="Stock",
        path="/inventory/stock",
        permission="inventory:read",
        section="operations",
        order=10,
    )
    assert n.section == "operations"
    assert n.icon is None


def test_slotcontribution_fields():
    s = SlotContribution(
        slot="admin.dashboard.widgets",
        template="inventory/w.html",
        permission="inventory:read",
        order=5,
    )
    assert s.provider is None


class _FakeEP:
    def __init__(self, name, obj):
        self.name = name
        self._obj = obj

    def load(self):
        if isinstance(self._obj, Exception):
            raise self._obj
        return self._obj


def test_load_skips_reserved(caplog):
    good = WebModule(name="inventory", router=APIRouter())
    mods = load_web_modules([_FakeEP("login", good), _FakeEP("inventory", good)])
    assert [m.name for m in mods] == ["inventory"]
    assert "reserved" in caplog.text


def test_load_skips_duplicates(caplog):
    a = WebModule(name="inventory", router=APIRouter())
    b = WebModule(name="inventory", router=APIRouter())
    mods = load_web_modules([_FakeEP("inventory", a), _FakeEP("inventory", b)])
    assert mods == [a]
    assert "already registered" in caplog.text


def test_load_isolates_broken(caplog):
    good = WebModule(name="inventory", router=APIRouter())
    mods = load_web_modules([_FakeEP("boom", RuntimeError("nope")), _FakeEP("inventory", good)])
    assert [m.name for m in mods] == ["inventory"]
    assert "failed to load" in caplog.text


def _app_with(modules, monkeypatch, engine):
    monkeypatch.setenv("HEARTH_WEB_SECRET_KEY", "k")
    from hearth_web.app import create_app

    return create_app(engine=engine, registry=make_registry(), modules=modules)


@pytest_asyncio.fixture
async def engine():
    eng = make_async_engine("sqlite:///:memory:")
    h = BaseHarness(eng)
    await h.setup()
    await seed_admin(h)
    yield eng
    await eng.dispose()


def test_web_module_mounts_router(monkeypatch, engine):
    from fastapi import APIRouter

    from hearth_web.extensions import WebModule

    router = APIRouter()

    @router.get("/ping")
    async def ping():
        return {"pong": True}

    app = _app_with([WebModule(name="inventory", router=router)], monkeypatch, engine)
    with TestClient(app) as c:
        assert c.get("/inventory/ping").json() == {"pong": True}


def test_web_module_static_mounted(tmp_path, monkeypatch, engine):
    from hearth_web.extensions import WebModule

    static = tmp_path / "static"
    static.mkdir()
    (static / "app.css").write_text("body{}")
    mod = WebModule(name="inventory", static_dir="static", package_dir=str(tmp_path))
    app = _app_with([mod], monkeypatch, engine)
    with TestClient(app) as c:
        assert c.get("/static/inventory/app.css").status_code == 200


@pytest.mark.asyncio
async def test_web_module_templates_namespaced(tmp_path, monkeypatch, engine):
    from hearth_web.extensions import WebModule

    tdir = tmp_path / "templates"
    tdir.mkdir()
    (tdir / "hello.html").write_text("INVENTORY HELLO")
    mod = WebModule(name="inventory", templates_dir="templates", package_dir=str(tmp_path))
    app = _app_with([mod], monkeypatch, engine)
    html = await app.state.jinja_env.get_template("inventory/hello.html").render_async()
    assert html == "INVENTORY HELLO"
