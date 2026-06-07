"""Jinja environment, page rendering, nav assembly, page_context dependency."""

from __future__ import annotations

import logging
import os
from collections.abc import Iterable
from itertools import groupby
from operator import attrgetter
from pathlib import Path
from typing import Any

from fastapi import Depends, Request
from fastapi.responses import HTMLResponse
from jinja2 import (
    ChoiceLoader,
    Environment,
    FileSystemLoader,
    PrefixLoader,
    pass_context,
    select_autoescape,
)

from hearth import Actor, UnitOfWork
from hearth_web.extensions import NavItem, WebModule
from hearth_web.security import Check, current_actor, get_check, request_uow
from hearth_web.slots import make_render_slot

logger = logging.getLogger("hearth_web")

_ADMIN_TEMPLATES = Path(__file__).parent / "templates"

KNOWN_SECTIONS = ("overview", "operations", "system", "extensions")


def build_nav(modules: list[WebModule], admin_nav: list[NavItem]) -> list[NavItem]:
    """Merge admin + plugin nav, normalize unknown sections to 'extensions',
    sort by (section order, item order, label)."""
    items: list[NavItem] = list(admin_nav)
    for m in modules:
        items.extend(m.nav)
    normalized: list[NavItem] = []
    for it in items:
        section = it.section or "overview"
        if section not in KNOWN_SECTIONS:
            logger.warning(
                "unknown nav section %r on %r; placing under 'extensions'", section, it.label
            )
            section = "extensions"
        normalized.append(
            NavItem(
                label=it.label,
                path=it.path,
                permission=it.permission,
                section=section,
                order=it.order,
                icon=it.icon,
            )
        )
    section_rank = {s: i for i, s in enumerate(KNOWN_SECTIONS)}
    normalized.sort(key=lambda i: (section_rank[i.section or "overview"], i.order, i.label))
    return normalized


def build_jinja_env(modules: list[WebModule], override_dir: str | None) -> Environment:
    prefix_map: dict[str, FileSystemLoader] = {}
    for m in modules:
        if m.templates_dir and m.package_dir:
            prefix_map[m.name] = FileSystemLoader(os.path.join(m.package_dir, m.templates_dir))
    loaders: list[FileSystemLoader | PrefixLoader] = []
    if override_dir:
        loaders.append(FileSystemLoader(override_dir))
    loaders.append(FileSystemLoader(str(_ADMIN_TEMPLATES)))  # admin + base templates
    if prefix_map:
        loaders.append(PrefixLoader(prefix_map))
    env = Environment(
        loader=ChoiceLoader(loaders),
        autoescape=select_autoescape(["html"]),
        enable_async=True,
        undefined=__import__("jinja2").StrictUndefined,
    )

    # Register groupby filter for nav template
    @pass_context
    def groupby_filter(
        ctx: dict[str, Any], seq: Iterable[NavItem], attr: str
    ) -> list[tuple[Any, list[NavItem]]]:
        key_func = attrgetter(attr)
        sorted_seq = sorted(seq, key=key_func)
        return [(k, list(v)) for k, v in groupby(sorted_seq, key_func)]

    env.filters["groupby"] = groupby_filter  # type: ignore[assignment]

    # Register can filter for template-level authorization
    @pass_context
    async def can_filter(ctx: dict[str, Any], permission: str) -> bool:
        check = ctx.get("check")
        if check is None:
            return False
        return await check(permission)

    env.filters["can"] = can_filter  # type: ignore[assignment]
    return env


async def page_context(
    request: Request,
    check: Check = Depends(get_check),  # noqa: B008
    actor: Actor = Depends(current_actor),  # noqa: B008
    uow: UnitOfWork = Depends(request_uow),  # noqa: B008
) -> None:
    env = request.app.state.jinja_env
    render_slot = make_render_slot(request.app.state.slots, uow, actor, check, env)
    request.state.render_slot = render_slot
    request.state.check = check
    request.state.actor = actor
    request.state.uow = uow
    nav: list[NavItem] = []
    for item in request.app.state.nav:
        if item.permission is None or await check(item.permission):
            nav.append(item)
    request.state.nav = nav


async def render(
    request: Request, template: str, *, status_code: int = 200, **ctx: Any
) -> HTMLResponse:
    env = request.app.state.jinja_env
    base = {
        "request": request,
        "current_actor": request.state.actor,
        "check": request.state.check,
        "render_slot": request.state.render_slot,
        "nav": request.state.nav,
        "brand": request.app.state.brand,
    }
    html = await env.get_template(template).render_async({**base, **ctx})
    return HTMLResponse(html, status_code=status_code)
