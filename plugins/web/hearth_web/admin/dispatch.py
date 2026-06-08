"""Action dispatch — GET detail page + POST execute handler."""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from fastapi_csrf_protect import CsrfProtect
from pydantic import ValidationError

from hearth.errors import EntityNotFoundError, IntegrityError
from hearth.primitives.actor import Actor
from hearth_web.admin.autoform import fields_for_action
from hearth_web.rendering import render
from hearth_web.security import Check, current_actor, get_check, requires_permission

logger = logging.getLogger("hearth_web")

# Included into admin_router — inherits admin:read + page_context dependencies.
detail_router = APIRouter()

# Included directly into app — no inherited deps; applies admin:write per route.
dispatch_router = APIRouter(prefix="/admin")


@detail_router.get("/actions/{plugin_alias}/{action_name}")
async def action_detail(
    request: Request,
    plugin_alias: str,
    action_name: str,
    csrf_protect: CsrfProtect = Depends(),  # noqa: B008
) -> HTMLResponse:
    plugin_info = request.app.state.registry.plugins.get(plugin_alias)
    if plugin_info is None:
        raise HTTPException(status_code=404, detail=f"Plugin {plugin_alias!r} not found")
    action_cls = next((a for a in plugin_info.actions if a.__name__ == action_name), None)
    if action_cls is None:
        raise HTTPException(
            status_code=404,
            detail=f"Action {action_name!r} not found in plugin {plugin_alias!r}",
        )
    fields = fields_for_action(action_cls)
    csrf_token, signed_token = csrf_protect.generate_csrf_tokens()
    response = await render(
        request,
        "admin/action_detail.html",
        plugin=plugin_info,
        plugin_alias=plugin_alias,
        action_cls=action_cls,
        fields=fields,
        csrf_token=csrf_token,
    )
    csrf_protect.set_csrf_cookie(signed_token, response)
    return response  # type: ignore[return-value]


@dispatch_router.post(
    "/actions/{plugin_alias}/{action_name}",
    dependencies=[Depends(requires_permission("admin:write"))],
)
async def dispatch_action(
    request: Request,
    plugin_alias: str,
    action_name: str,
    csrf_protect: CsrfProtect = Depends(),  # noqa: B008
    actor: Actor = Depends(current_actor),  # noqa: B008
    check: Check = Depends(get_check),  # noqa: B008
) -> Response:
    plugin_info = request.app.state.registry.plugins.get(plugin_alias)
    if plugin_info is None:
        raise HTTPException(status_code=404)
    action_cls = next((a for a in plugin_info.actions if a.__name__ == action_name), None)
    if action_cls is None:
        raise HTTPException(status_code=404)

    await csrf_protect.validate_csrf(request)

    form_data = await request.form()
    data = {k: v for k, v in form_data.items() if k != "csrf_token"}

    try:
        action = action_cls(**data)
    except ValidationError as exc:
        errors: dict[str, str] = {}
        for err in exc.errors():
            field = err["loc"][0] if err["loc"] else "__root__"
            errors[str(field)] = err["msg"]
        fields = fields_for_action(action_cls)
        env = request.app.state.jinja_env
        fragment = await env.get_template("admin/_autoform.html").render_async(
            fields=fields,
            errors=errors,
        )
        return HTMLResponse(
            content=fragment,
            status_code=422,
        )

    factory = request.app.state.uow_factory
    try:
        async with factory(actor=actor) as write_uow:
            await action.handle(write_uow, actor)  # type: ignore[arg-type]
            emitted = list(write_uow.emitted_events)
    except EntityNotFoundError as exc:
        return _error_toast(str(exc))
    except IntegrityError as exc:
        return _error_toast(str(exc))

    if emitted:
        message = ", ".join(type(e).__name__ for e in emitted)
    else:
        message = f"{action_name} completed"

    trigger = json.dumps(
        {"showToast": {"message": message, "variant": "success"}, "closeModal": True}
    )
    return Response(
        content="",
        status_code=200,
        headers={"HX-Trigger": trigger},
    )


def _error_toast(message: str) -> Response:
    trigger = json.dumps({"showToast": {"message": message, "variant": "danger"}})
    return Response(content="", status_code=200, headers={"HX-Trigger": trigger})
