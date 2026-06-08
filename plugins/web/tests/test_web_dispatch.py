"""Tests for admin action dispatch — GET detail + POST execute."""

from __future__ import annotations

import pytest


def _csrf_token(resp):
    return resp.text.split('name="csrf_token" value="')[1].split('"')[0]


def _login(client):
    get_resp = client.get("/login")
    csrf_token = _csrf_token(get_resp)
    client.post(
        "/login",
        data={"email": "admin@x.com", "password": "adminpass", "csrf_token": csrf_token},
        follow_redirects=False,
    )


@pytest.mark.asyncio
async def test_action_detail_page_renders(web):
    _login(web.client)
    resp = web.client.get("/admin/actions/auth/CreateRole")
    assert resp.status_code == 200
    assert "CreateRole" in resp.text


@pytest.mark.asyncio
async def test_action_detail_404_on_unknown_plugin(web):
    _login(web.client)
    resp = web.client.get("/admin/actions/nope/CreateRole")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_action_detail_404_on_unknown_action(web):
    _login(web.client)
    resp = web.client.get("/admin/actions/auth/NonExistent")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_dispatch_requires_write_permission(web):
    """Unauthenticated POST is rejected (redirects to login)."""
    resp = web.client.post(
        "/admin/actions/auth/CreateRole",
        data={"name": "editors", "csrf_token": "invalid"},
        follow_redirects=False,
    )
    # Unauthenticated → 401 or redirect to login
    assert resp.status_code in (401, 302, 303)


@pytest.mark.asyncio
async def test_dispatch_action_success(web):
    _login(web.client)
    # Get CSRF token from action detail page
    detail = web.client.get("/admin/actions/auth/CreateRole")
    csrf_token = _csrf_token(detail)

    resp = web.client.post(
        "/admin/actions/auth/CreateRole",
        data={"name": "editors", "csrf_token": csrf_token},
    )
    assert resp.status_code == 200
    # HX-Trigger header should contain showToast
    trigger_header = resp.headers.get("HX-Trigger", "")
    assert "showToast" in trigger_header
    assert "success" in trigger_header


@pytest.mark.asyncio
async def test_dispatch_action_validation_error(web):
    _login(web.client)
    detail = web.client.get("/admin/actions/auth/CreateRole")
    csrf_token = _csrf_token(detail)

    # Send request with missing required field — name is required
    resp = web.client.post(
        "/admin/actions/auth/CreateRole",
        data={"csrf_token": csrf_token},  # missing "name"
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_dispatch_csrf_rejected_without_token(web):
    _login(web.client)
    resp = web.client.post(
        "/admin/actions/auth/CreateRole",
        data={"name": "editors"},  # no csrf_token
        follow_redirects=False,
    )
    assert resp.status_code == 403


def test_dispatch_pydantic_coerces_string_form_values_to_int():
    """dispatch.py passes all form values as strings; Pydantic must coerce them.

    This is a unit test that mirrors what dispatch_action() does with
    action_cls(**data) where data values are always strings from form parsing.
    """
    from hearth.primitives.action import Action

    class _IntAction(Action):
        count: int

        async def handle(self, uow, actor):  # type: ignore[no-untyped-def]
            pass

    # Simulate form data: all values are strings
    action = _IntAction(**{"count": "42"})
    assert action.count == 42
    assert isinstance(action.count, int)


@pytest.mark.asyncio
async def test_actions_page_renders_buttons_and_modals(web):
    _login(web.client)
    resp = web.client.get("/admin/actions")
    assert resp.status_code == 200
    # Should contain form elements (not just wa-tag)
    assert "wa-button" in resp.text or "wa-dialog" in resp.text


@pytest.mark.asyncio
async def test_action_detail_page_has_form(web):
    _login(web.client)
    resp = web.client.get("/admin/actions/auth/CreateRole")
    assert resp.status_code == 200
    assert "wa-input" in resp.text or "wa-select" in resp.text or "name" in resp.text
