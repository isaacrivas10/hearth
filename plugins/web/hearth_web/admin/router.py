"""The built-in read-only admin console (/admin)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from hearth_web.admin.introspection import (
    counts,
    db_status,
    entity_fields,
    schema_mermaid,
)
from hearth_web.extensions import NavItem
from hearth_web.rendering import page_context, render
from hearth_web.security import requires_permission

ADMIN_NAV = [
    NavItem(label="Dashboard", path="/admin", section="overview", order=0),
    NavItem(label="Plugins", path="/admin/plugins", section="system", order=10),
    NavItem(label="Entities", path="/admin/entities", section="system", order=20),
    NavItem(label="Actions", path="/admin/actions", section="system", order=30),
    NavItem(label="Events", path="/admin/events", section="system", order=40),
    NavItem(label="Schema", path="/admin/schema", section="system", order=50),
    NavItem(label="Database", path="/admin/db", section="system", order=60),
]

admin_router = APIRouter(
    prefix="/admin",
    dependencies=[Depends(requires_permission("admin:read")), Depends(page_context)],
)


@admin_router.get("")
async def dashboard(request: Request):
    status = await db_status(request.app.state.engine, request.app.state.registry)
    return await render(
        request,
        "admin/dashboard.html",
        counts=counts(request.app.state.registry),
        db=status,
    )


@admin_router.get("/plugins")
async def plugins(request: Request):
    return await render(
        request, "admin/plugins.html", plugins=list(request.app.state.registry.plugins.values())
    )


@admin_router.get("/plugins/{alias}")
async def plugin_detail(request: Request, alias: str):
    info = request.app.state.registry.plugins.get(alias)
    if info is None:
        raise HTTPException(status_code=404, detail=f"Plugin {alias!r} not found")
    return await render(request, "admin/plugin_detail.html", plugin=info, alias=alias)


@admin_router.get("/entities")
async def entities(request: Request):
    return await render(
        request, "admin/entities.html", plugins=list(request.app.state.registry.plugins.values())
    )


@admin_router.get("/entities/{alias}/{name}")
async def entity_detail(request: Request, alias: str, name: str):
    info = request.app.state.registry.plugins.get(alias)
    if info is None:
        raise HTTPException(status_code=404, detail=f"Plugin {alias!r} not found")
    entity = next((e for e in info.entities if e.__name__ == name), None)
    if entity is None:
        raise HTTPException(
            status_code=404, detail=f"Entity {name!r} not found in plugin {alias!r}"
        )
    fields = entity_fields(entity)
    return await render(request, "admin/entity_detail.html", alias=alias, name=name, fields=fields)


@admin_router.get("/actions")
async def actions(request: Request):
    return await render(
        request, "admin/actions.html", plugins=list(request.app.state.registry.plugins.values())
    )


@admin_router.get("/events")
async def events(request: Request):
    return await render(
        request, "admin/events.html", plugins=list(request.app.state.registry.plugins.values())
    )


@admin_router.get("/schema")
async def schema(request: Request):
    return await render(
        request, "admin/schema.html", mermaid=schema_mermaid(request.app.state.registry)
    )


@admin_router.get("/db")
async def db(request: Request):
    status = await db_status(request.app.state.engine, request.app.state.registry)
    return await render(request, "admin/db.html", db=status)
