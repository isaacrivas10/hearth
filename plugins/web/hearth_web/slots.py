"""Slot registry and async render_slot."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from jinja2 import Environment
from markupsafe import Markup

from hearth import Actor
from hearth.unit_of_work import UnitOfWork
from hearth_web.extensions import SlotContribution, WebModule

logger = logging.getLogger("hearth_web")

CheckFn = Callable[[str], Awaitable[bool]]
ProviderFn = Callable[..., Awaitable[dict[str, Any]]]
RenderSlotFn = Callable[..., Awaitable[Markup]]


def build_slot_registry(modules: list[WebModule]) -> dict[str, list[SlotContribution]]:
    reg: dict[str, list[SlotContribution]] = {}
    for m in modules:
        for c in m.contributions:
            reg.setdefault(c.slot, []).append(c)
    for slot in reg:
        reg[slot].sort(key=lambda c: c.order)
    return reg


def make_render_slot(
    registry: dict[str, list[SlotContribution]],
    uow: UnitOfWork,
    actor: Actor,
    check: CheckFn,
    env: Environment,
) -> RenderSlotFn:
    _render_depth: dict[str, int] = {}
    max_depth = 10

    async def render_slot(name: str, **ctx: Any) -> Markup:
        depth = _render_depth.get(name, 0)
        if depth >= max_depth:
            logger.warning("slot %r exceeded max render depth (%d); skipping", name, max_depth)
            return Markup("")
        _render_depth[name] = depth + 1
        try:
            parts: list[str] = []
            for contrib in registry.get(name, []):
                if contrib.permission is not None and not await check(contrib.permission):
                    continue
                try:
                    data: dict[str, Any] = (
                        await contrib.provider(uow, actor, **ctx) if contrib.provider else {}
                    )
                    fragment_ctx: dict[str, Any] = {
                        **ctx,
                        **data,
                        "check": check,
                        "current_actor": actor,
                        "render_slot": render_slot,
                    }
                    html = await env.get_template(contrib.template).render_async(fragment_ctx)
                except Exception as exc:
                    logger.warning(
                        "slot %r contribution %r failed: %s: %s",
                        name,
                        contrib.template,
                        type(exc).__name__,
                        exc,
                    )
                    continue
                parts.append(html)
            return Markup("".join(parts))
        finally:
            _render_depth[name] = depth

    return render_slot
