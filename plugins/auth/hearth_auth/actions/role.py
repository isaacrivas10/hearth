"""Role-lifecycle actions: Create, AssignToUser, RevokeFromUser."""

from __future__ import annotations

from hearth import Actor, EntityId, UnitOfWork
from hearth_auth._base import Action
from hearth_auth.entities.role import Role
from hearth_auth.entities.user import User
from hearth_auth.entities.user_role import UserRole
from hearth_auth.events.role import (
    RoleAssignedToUser,
    RoleCreated,
    RoleRevokedFromUser,
)


class CreateRole(Action):
    name: str
    description: str | None = None

    async def handle(self, uow: UnitOfWork, actor: Actor) -> Role:
        role = Role(name=self.name, description=self.description)
        await uow.save(role)
        assert role.id is not None
        uow.emit(RoleCreated(role_id=role.id, name=role.name))
        return role


class AssignRoleToUser(Action):
    user_id: EntityId
    role_id: EntityId

    async def handle(self, uow: UnitOfWork, actor: Actor) -> UserRole:
        user = await uow.query(User).where(User.id == self.user_id).one()
        role = await uow.query(Role).where(Role.id == self.role_id).one()
        # If the actor is itself a User entity, capture them as granted_by.
        granted_by: User | None = None
        if isinstance(actor, User):
            granted_by = actor
        ur = UserRole(user=user, role=role, granted_by=granted_by)
        await uow.save(ur)
        uow.emit(
            RoleAssignedToUser(
                user_id=self.user_id,
                role_id=self.role_id,
                granted_by_id=granted_by.id if granted_by is not None else None,
            )
        )
        return ur


class RevokeRoleFromUser(Action):
    user_id: EntityId
    role_id: EntityId

    async def handle(self, uow: UnitOfWork, actor: Actor) -> None:
        existing = await (
            uow.query(UserRole)
            .where(UserRole.user_id == self.user_id)
            .where(UserRole.role_id == self.role_id)
            .one_or_none()
        )
        if existing is not None:
            await uow.delete(existing)
            uow.emit(RoleRevokedFromUser(user_id=self.user_id, role_id=self.role_id))
