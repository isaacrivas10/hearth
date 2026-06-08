"""The hearth-web application factory."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy.ext.asyncio import AsyncEngine
from starlette.middleware.sessions import SessionMiddleware

from hearth.kernel._engine import make_async_engine  # pyright: ignore[reportPrivateUsage]
from hearth.kernel.registry import Registry
from hearth.kernel.transaction import transaction_factory
from hearth_web.admin.dispatch import dispatch_router
from hearth_web.admin.router import ADMIN_NAV, admin_router
from hearth_web.extensions import WebModule, discover_web_modules
from hearth_web.rendering import build_jinja_env, build_nav, build_render_registry
from hearth_web.security import Forbidden, forbidden_handler
from hearth_web.sessions import add_csrf_exception_handlers, init_csrf, session_router
from hearth_web.slots import build_slot_registry

logger = logging.getLogger("hearth_web")


def _module_package_dir(module: WebModule) -> str:
    if module.package_dir is None:
        raise RuntimeError(
            f"web module {module.name!r} sets templates_dir/static_dir but no "
            f"package_dir; pass package_dir=os.path.dirname(__file__)"
        )
    return module.package_dir


def create_app(
    *,
    engine: AsyncEngine | None = None,
    registry: Registry | None = None,
    modules: list[WebModule] | None = None,
) -> FastAPI:
    secret = os.environ.get("HEARTH_WEB_SECRET_KEY")
    if not secret:
        raise RuntimeError("HEARTH_WEB_SECRET_KEY is required to start hearth-web")

    if engine is None:
        url = os.environ.get("DATABASE_URL")
        if not url:
            raise RuntimeError("DATABASE_URL is required to start hearth-web")
        engine = make_async_engine(url)

    if registry is None:
        registry = Registry.build()

    resolved_modules = modules if modules is not None else discover_web_modules()
    app = FastAPI(title="Hearth")
    secure_cookies = os.environ.get("HEARTH_WEB_SECURE_COOKIES", "").lower() in ("1", "true", "yes")
    app.add_middleware(
        SessionMiddleware, secret_key=secret, same_site="lax", https_only=secure_cookies
    )
    app.state.engine = engine
    app.state.uow_factory = transaction_factory(engine)
    app.state.registry = registry
    app.state.secret = secret
    app.state.modules = resolved_modules

    init_csrf(secret)
    add_csrf_exception_handlers(app)

    app.add_exception_handler(Forbidden, forbidden_handler)  # type: ignore[arg-type]

    @app.exception_handler(Exception)
    async def _unexpected(request: Request, exc: Exception) -> Response:  # pyright: ignore[reportUnusedFunction]
        if os.environ.get("HEARTH_DEBUG"):
            raise exc
        logger.exception("Unhandled exception in request")
        return Response("ERROR: internal server error", status_code=500)

    for module in resolved_modules:
        if module.router is not None:
            app.include_router(module.router, prefix=f"/{module.name}")
        if module.static_dir is not None:
            pkg_root = Path(_module_package_dir(module))
            app.mount(
                f"/static/{module.name}",
                StaticFiles(directory=pkg_root / module.static_dir),
                name=f"static-{module.name}",
            )

    app.include_router(session_router)
    static_dir = str(Path(__file__).parent / "static")
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

    override_dir = os.environ.get("HEARTH_WEB_TEMPLATE_DIR")
    app.state.jinja_env = build_jinja_env(resolved_modules, override_dir)
    app.state.slots = build_slot_registry(resolved_modules)
    app.state.render_registry = build_render_registry(resolved_modules)
    app.state.nav = build_nav(resolved_modules, admin_nav=ADMIN_NAV)
    app.state.brand = {
        "name": os.environ.get("HEARTH_WEB_BRAND_NAME", "Hearth"),
        "primary_color": os.environ.get("HEARTH_WEB_PRIMARY_COLOR"),
        "logo_url": os.environ.get("HEARTH_WEB_LOGO_URL"),
    }

    @app.get("/")
    async def _root() -> RedirectResponse:  # pyright: ignore[reportUnusedFunction]
        return RedirectResponse("/admin", status_code=303)

    @app.get("/health")
    async def health() -> dict[str, str]:  # pyright: ignore[reportUnusedFunction]
        return {"status": "ok"}

    app.include_router(admin_router)
    app.include_router(dispatch_router)

    return app
