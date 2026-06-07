"""Request-scoped auth/authz: uow, actor resolution, memoized permission check,
route gate, exception handling."""

from __future__ import annotations

from collections.abc import AsyncGenerator, Awaitable, Callable
from urllib.parse import quote

from fastapi import Depends, Request
from fastapi.responses import RedirectResponse, Response
from pydantic import SecretStr

from hearth import Actor, Anonymous, EntityId
from hearth.unit_of_work import UnitOfWork
from hearth_auth import AuthenticationFailed, User
from hearth_auth.actions.api_key import AuthenticateApiKey

Check = Callable[[str], Awaitable[bool]]


class Forbidden(Exception):  # noqa: N818
    def __init__(self, actor: Actor) -> None:
        self.actor = actor


def requires_permission(permission: str):
    async def dep(
        check: Check = Depends(get_check),  # noqa: B008
        actor: Actor = Depends(current_actor),  # noqa: B008
    ) -> None:
        if not await check(permission):
            raise Forbidden(actor)

    return dep


def _wants_html(request: Request) -> bool:
    if request.headers.get("HX-Request"):
        return False
    return "text/html" in request.headers.get("accept", "")


async def forbidden_handler(request: Request, exc: Forbidden):
    if isinstance(exc.actor, Anonymous):
        if _wants_html(request):
            return RedirectResponse(f"/login?next={quote(request.url.path)}", status_code=303)
        return Response(status_code=401)
    return Response(status_code=403)


async def request_uow(request: Request) -> AsyncGenerator[UnitOfWork]:
    """FastAPI YIELD dependency: one transaction per request, kept open until
    the response body is sent (template rendering — slot providers, the `can`
    filter — runs at ASGI send time, AFTER the route handler returns). The
    `yield` is what makes FastAPI drive the context manager to teardown then.
    Audit actor is Anonymous in v1 (read-only path); see plan caveat. Resolve
    everywhere via `Depends(request_uow)` so FastAPI caches one uow per request."""
    factory = request.app.state.uow_factory
    async with factory(actor=Anonymous()) as uow:
        yield uow


async def current_actor(
    request: Request,
    uow: UnitOfWork = Depends(request_uow),  # noqa: B008
) -> Actor:
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        token = auth[len("Bearer ") :].strip()
        try:
            return await AuthenticateApiKey(key_string=SecretStr(token)).handle(uow, Anonymous())
        except AuthenticationFailed:
            return Anonymous()
    raw = request.session.get("user_id")
    if raw:
        # EntityId is a str subclass; the login handler stored str(user.id).
        # A tampered/non-matching value simply matches no row (→ Anonymous),
        # so no validation/500 risk — query with the string directly.
        user = await uow.query(User).where(User.id == EntityId(raw)).one_or_none()
        if user is not None and user.is_active:
            return user
    return Anonymous()


def make_check(uow: UnitOfWork, actor: Actor) -> Check:
    cache: dict[str, bool] = {}

    async def check(permission: str) -> bool:
        if permission not in cache:
            cache[permission] = await actor.has_permission(uow, permission)
        return cache[permission]

    return check


async def get_check(
    uow: UnitOfWork = Depends(request_uow),  # noqa: B008
    actor: Actor = Depends(current_actor),  # noqa: B008
) -> Check:
    return make_check(uow, actor)
