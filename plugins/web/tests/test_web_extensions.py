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


from hearth_web.extensions import RenderConfig


def test_render_config_defaults():
    rc = RenderConfig()
    assert rc.template is None
    assert rc.fields is None
    assert rc.permission is None
    assert rc.submit_label is None


def test_render_config_is_frozen():
    rc = RenderConfig(template="foo.html")
    with pytest.raises(Exception):
        rc.template = "bar.html"  # type: ignore[misc]


def test_web_module_render_field_defaults():
    m = WebModule(name="test")
    assert m.render == {}


def test_web_module_render_field_accepts_mapping():
    from hearth.primitives.action import Action

    class _Act(Action):
        async def handle(self, uow, actor):  # type: ignore[no-untyped-def]
            pass

    m = WebModule(name="test", render={_Act: {"button": RenderConfig()}})
    assert "button" in m.render[_Act]


@pytest.mark.asyncio
async def test_render_primitive_raises_on_unknown_variant(web):
    """render_primitive raises KeyError for unregistered variant."""
    from hearth.primitives.action import Action

    class _TestAction(Action):
        async def handle(self, uow, actor):  # type: ignore[no-untyped-def]
            pass

    # render_registry is empty for the default web fixture
    # Access render_primitive via a request to a route that stores it on request.state
    # Simpler: call make_render_primitive directly with empty registry
    from hearth_web.rendering import make_render_primitive

    dummy_registry: dict = {}

    async def always_true(p: str) -> bool:
        return True

    import jinja2

    env = jinja2.Environment()

    rp = make_render_primitive(dummy_registry, always_true, env)
    with pytest.raises(KeyError):
        await rp(_TestAction, "form")


@pytest.mark.asyncio
async def test_render_primitive_permission_gate_returns_empty(web):
    """render_primitive returns empty Markup when permission check fails."""
    from hearth.primitives.action import Action
    from hearth_web.extensions import RenderConfig
    from hearth_web.rendering import make_render_primitive

    class _GatedAction(Action):
        async def handle(self, uow, actor):  # type: ignore[no-untyped-def]
            pass

    registry = {_GatedAction: {"button": RenderConfig(permission="admin:superuser")}}

    async def always_false(p: str) -> bool:
        return False

    import jinja2

    env = jinja2.Environment()

    rp = make_render_primitive(registry, always_false, env)
    result = await rp(_GatedAction, "button")
    from markupsafe import Markup

    assert result == Markup("")


@pytest.mark.asyncio
async def test_render_primitive_none_template_on_non_action_raises():
    """RenderConfig(template=None) is only valid for Action subclasses."""
    from hearth.primitives.entity import Entity
    from hearth_web.extensions import RenderConfig
    from hearth_web.rendering import make_render_primitive
    import jinja2

    class _NotAction(Entity):
        pass

    registry = {_NotAction: {"form": RenderConfig(template=None)}}

    async def always_true(p: str) -> bool:
        return True

    env = jinja2.Environment()
    rp = make_render_primitive(registry, always_true, env)
    with pytest.raises(ValueError):
        await rp(_NotAction, "form")


@pytest.mark.asyncio
async def test_render_primitive_instance_lookup_uses_type():
    """Passing an instance resolves the registry key via type(instance)."""
    from hearth.primitives.action import Action
    from hearth_web.extensions import RenderConfig
    from hearth_web.rendering import make_render_primitive

    class _TaggedAction(Action):
        async def handle(self, uow, actor):  # type: ignore[no-untyped-def]
            pass

    # Register by class, look up by instance
    registry = {_TaggedAction: {"button": RenderConfig(permission=None, template=None)}}

    async def always_true(p: str) -> bool:
        return True

    import jinja2

    env = jinja2.Environment(
        loader=jinja2.DictLoader({"admin/_autoform.html": "<!-- empty -->"}), enable_async=True
    )

    rp = make_render_primitive(registry, always_true, env)
    instance = _TaggedAction()
    # Should not raise KeyError — instance lookup falls back to type(instance)
    result = await rp(instance, "button")
    from markupsafe import Markup

    assert isinstance(result, Markup)
