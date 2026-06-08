"""Tests for OutboxHistory and the admin events route."""

from __future__ import annotations

import pytest

from hearth_auth.actions import CreateRole
from hearth.primitives.actor import System


def _csrf_token(resp):
    return resp.text.split('name="csrf_token" value="')[1].split('"')[0]


def _login(client):
    get_resp = client.get("/login")
    csrf = _csrf_token(get_resp)
    client.post(
        "/login",
        data={"email": "admin@x.com", "password": "adminpass", "csrf_token": csrf},
        follow_redirects=False,
    )


@pytest.mark.asyncio
async def test_events_page_renders(web):
    _login(web.client)
    resp = web.client.get("/admin/events")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_events_page_shows_outbox_history_section(web):
    _login(web.client)
    resp = web.client.get("/admin/events")
    assert resp.status_code == 200
    assert "Outbox History" in resp.text or "outbox" in resp.text.lower()


@pytest.mark.asyncio
async def test_outbox_history_action_directly(web):
    """OutboxHistory returns rows populated by an action run via dispatch."""
    from hearth.views.outbox import OutboxHistory
    from hearth.primitives.actor import System as Sys

    # Populate outbox via dispatch endpoint
    _login(web.client)
    detail = web.client.get("/admin/actions/auth/CreateRole")
    csrf = _csrf_token(detail)
    web.client.post(
        "/admin/actions/auth/CreateRole",
        data={"name": "outbox-history-test-role-abc123", "csrf_token": csrf},
    )

    async with web.harness.transaction() as uow:
        page = await OutboxHistory(limit=10, offset=0).handle(uow, Sys())  # type: ignore[arg-type]

    assert page.total >= 1
    assert len(page.rows) >= 1


@pytest.mark.asyncio
async def test_outbox_history_event_type_filter(web):
    from hearth.views.outbox import OutboxHistory
    from hearth.primitives.actor import System as Sys

    _login(web.client)
    detail = web.client.get("/admin/actions/auth/CreateRole")
    csrf = _csrf_token(detail)
    web.client.post(
        "/admin/actions/auth/CreateRole",
        data={"name": "filter-test-role-xyz789", "csrf_token": csrf},
    )

    async with web.harness.transaction() as uow:
        page_all = await OutboxHistory(limit=10).handle(uow, Sys())  # type: ignore[arg-type]
        # Filter using the actual event type from the first row
        if page_all.rows:
            et = page_all.rows[0].event_type
            page_filtered = await OutboxHistory(limit=10, event_type=et).handle(uow, Sys())  # type: ignore[arg-type]
            assert page_filtered.total <= page_all.total
            assert all(r.event_type == et for r in page_filtered.rows)
        page_none = await OutboxHistory(limit=10, event_type="NonExistentEvent__XYZ").handle(
            uow, Sys()
        )  # type: ignore[arg-type]
        assert page_none.total == 0


@pytest.mark.asyncio
async def test_outbox_history_pagination_flags(web):
    from hearth.views.outbox import OutboxHistory
    from hearth.primitives.actor import System as Sys

    _login(web.client)
    # Run 3 actions to populate at least 3 outbox rows
    for i in range(3):
        detail = web.client.get("/admin/actions/auth/CreateRole")
        csrf = _csrf_token(detail)
        web.client.post(
            "/admin/actions/auth/CreateRole",
            data={"name": f"pag-test-role-{i}-uvw456", "csrf_token": csrf},
        )

    async with web.harness.transaction() as uow:
        page1 = await OutboxHistory(limit=2, offset=0).handle(uow, Sys())  # type: ignore[arg-type]
        page2 = await OutboxHistory(limit=2, offset=2).handle(uow, Sys())  # type: ignore[arg-type]

    assert page1.has_next is True
    assert page1.has_prev is False
    assert page2.has_prev is True


@pytest.mark.asyncio
async def test_events_page_with_filter(web):
    _login(web.client)
    resp = web.client.get("/admin/events?event_type=RoleCreated")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_events_page_unknown_filter_treated_as_all(web):
    _login(web.client)
    resp = web.client.get("/admin/events?event_type=NotARealEvent")
    assert resp.status_code == 200
