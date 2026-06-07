"""Login/logout routes with CSRF protection."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi_csrf_protect import CsrfProtect
from fastapi_csrf_protect.exceptions import (
    CsrfProtectError,
    InvalidHeaderError,
    MissingTokenError,
    TokenValidationError,
)
from pydantic import BaseModel, ConfigDict, SecretStr
from starlette.responses import Response
from starlette.status import HTTP_403_FORBIDDEN

from hearth import Anonymous
from hearth.unit_of_work import UnitOfWork
from hearth_auth import AuthenticationFailed
from hearth_auth.actions.user_auth import AuthenticateUser
from hearth_commons import EmailAddress
from hearth_web.security import request_uow

session_router = APIRouter()


class CsrfSettings(BaseModel):
    model_config = ConfigDict(extra="allow")

    secret_key: str
    cookie_samesite: str = "lax"
    cookie_secure: bool = False
    # Validate the token from the POST body field named "csrf_token"
    # (matches the hidden <input name="csrf_token"> in login.html), comparing
    # it against the HMAC-signed value stored in the cookie.
    token_location: str = "body"
    token_key: str = "csrf_token"


def init_csrf(secret: str) -> None:
    CsrfProtect.load_config(lambda: CsrfSettings(secret_key=secret))  # type: ignore[arg-type]


def validate_next(raw: str | None) -> str:
    """Only site-local paths are honored (open-redirect defense)."""
    if raw and raw.startswith("/") and not raw.startswith("//"):
        return raw
    return "/admin"


def add_csrf_exception_handlers(app: Any) -> None:
    """Add CSRF exception handlers to the FastAPI app."""

    @app.exception_handler(MissingTokenError)
    @app.exception_handler(TokenValidationError)
    @app.exception_handler(InvalidHeaderError)
    async def csrf_error_handler(request: Request, exc: CsrfProtectError) -> Response:  # pyright: ignore[reportUnusedFunction]
        return Response(content="CSRF validation failed", status_code=HTTP_403_FORBIDDEN)


@session_router.get("/login")
async def login_form(
    request: Request,
    next_url: Annotated[str, Query(alias="next")] = "/admin",
    csrf_protect: CsrfProtect = Depends(),  # noqa: B008
) -> HTMLResponse:
    env = request.app.state.jinja_env
    csrf_token, signed_token = csrf_protect.generate_csrf_tokens()
    response = HTMLResponse(
        await env.get_template("login.html").render_async(
            request=request,
            brand=request.app.state.brand,
            next=validate_next(next_url),
            error=None,
            csrf_token=csrf_token,
            email="",
        )
    )
    csrf_protect.set_csrf_cookie(signed_token, response)
    return response


@session_router.post("/login", response_model=None)
async def login_submit(
    request: Request,
    email: Annotated[str, Form()],
    password: Annotated[str, Form()],
    next_url: Annotated[str, Form(alias="next")] = "/admin",
    uow: UnitOfWork = Depends(request_uow),  # noqa: B008
    csrf_protect: CsrfProtect = Depends(),  # noqa: B008
) -> HTMLResponse | RedirectResponse:
    await csrf_protect.validate_csrf(request)
    target = validate_next(next_url)
    try:
        user = await AuthenticateUser(
            email=EmailAddress(raw=email), password=SecretStr(password)
        ).handle(uow, Anonymous())
    except AuthenticationFailed:
        env = request.app.state.jinja_env
        csrf_token, signed_token = csrf_protect.generate_csrf_tokens()
        html = await env.get_template("login.html").render_async(
            request=request,
            brand=request.app.state.brand,
            next=target,
            error="invalid credentials",
            email=email,
            csrf_token=csrf_token,
        )
        response = HTMLResponse(html, status_code=401)
        # Unset old CSRF cookie to avoid double-cookie bug on login failure
        csrf_protect.unset_csrf_cookie(response)
        csrf_protect.set_csrf_cookie(signed_token, response)
        return response
    request.session["user_id"] = str(user.id)
    response = RedirectResponse(target, status_code=303)
    csrf_protect.unset_csrf_cookie(response)
    return response


@session_router.post("/logout")
async def logout(
    request: Request,
    csrf_protect: CsrfProtect = Depends(),  # noqa: B008
) -> RedirectResponse:
    await csrf_protect.validate_csrf(request)
    request.session.clear()
    response = RedirectResponse("/login", status_code=303)
    csrf_protect.unset_csrf_cookie(response)
    return response
