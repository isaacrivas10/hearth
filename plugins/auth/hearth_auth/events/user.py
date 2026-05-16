"""Events emitted by user-lifecycle actions."""

from __future__ import annotations

from hearth import EntityId
from hearth_auth._base import Event
from hearth_commons import EmailAddress


class UserCreated(Event):
    user_id: EntityId
    email: EmailAddress


class PasswordChanged(Event):
    user_id: EntityId


class UserDisabled(Event):
    user_id: EntityId


class UserEnabled(Event):
    user_id: EntityId


class UserAuthenticated(Event):
    """Emitted by AuthenticateUser — defined here so events
    are grouped by entity domain rather than by action."""

    user_id: EntityId
