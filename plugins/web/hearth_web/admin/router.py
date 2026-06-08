"""The built-in read-only admin console (/admin)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi_csrf_protect import CsrfProtect
from hearth.views.outbox import OutboxHistory

from hearth_web.admin.autoform import fields_for_action
from hearth_web.admin.introspection import (
    counts,
    db_status,
    entity_fields,
    schema_graph,
)
from hearth_web.admin.dispatch import detail_router
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
async def actions(request: Request, csrf_protect: CsrfProtect = Depends()):  # noqa: B008
    plugins_data = [
        {
            "plugin": p,
            "alias": alias,
            "actions": [{"cls": a, "fields": fields_for_action(a)} for a in p.actions],
        }
        for alias, p in request.app.state.registry.plugins.items()
    ]
    csrf_token, signed_token = csrf_protect.generate_csrf_tokens()
    response = await render(
        request, "admin/actions.html", plugins_data=plugins_data, csrf_token=csrf_token
    )
    csrf_protect.set_csrf_cookie(signed_token, response)
    return response


@admin_router.get("/events")
async def events(request: Request):
    try:
        page = max(1, int(request.query_params.get("page", "1")))
    except (ValueError, TypeError):
        page = 1
    raw_event_type = request.query_params.get("event_type") or None

    registry = request.app.state.registry
    all_event_types = sorted({e.__name__ for p in registry.plugins.values() for e in p.events})

    # Validate filter — unrecognized values treated as None (all types)
    event_type = raw_event_type if raw_event_type in all_event_types else None

    actor = request.state.actor
    history = await OutboxHistory(
        limit=50,
        offset=(page - 1) * 50,
        event_type=event_type,
    ).handle(request.state.uow, actor)

    return await render(
        request,
        "admin/events.html",
        plugins=list(registry.plugins.values()),
        history=history,
        all_event_types=all_event_types,
        current_event_type=event_type,
        current_page=page,
    )


@admin_router.get("/schema")
async def schema(request: Request):
    return await render(
        request,
        "admin/schema.html",
        graph=schema_graph(request.app.state.registry),
    )


@admin_router.get("/db")
async def db(request: Request):
    status = await db_status(request.app.state.engine, request.app.state.registry)
    return await render(request, "admin/db.html", db=status)


@admin_router.get("/preferences")
async def preferences(request: Request):
    return await render(request, "admin/preferences.html")


admin_router.include_router(detail_router)
