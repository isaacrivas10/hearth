"""Permission-lifecycle actions: Create, AssignToRole, RevokeFromRole."""

from __future__ import annotations

from hearth import Actor, EntityId, UnitOfWork
from hearth_auth._base import Action
from hearth_auth.entities.permission import Permission
from hearth_auth.entities.role import Role
from hearth_auth.entities.role_permission import RolePermission
from hearth_auth.events.permission import (
    PermissionAssignedToRole,
    PermissionCreated,
    PermissionRevokedFromRole,
)
from hearth_auth.values import PermissionName


class CreatePermission(Action):
    name: PermissionName
    description: str | None = None

    async def handle(self, uow: UnitOfWork, actor: Actor) -> Permission:
        perm = Permission(name=self.name, description=self.description)
        await uow.save(perm)
        assert perm.id is not None
        uow.emit(PermissionCreated(permission_id=perm.id, name=self.name))
        return perm


class AssignPermissionToRole(Action):
    role_id: EntityId
    permission_id: EntityId

    async def handle(self, uow: UnitOfWork, actor: Actor) -> RolePermission:
        role = await uow.query(Role).where(Role.id == self.role_id).one()
        perm = await uow.query(Permission).where(Permission.id == self.permission_id).one()
        rp = RolePermission(role=role, permission=perm)
        await uow.save(rp)
        uow.emit(PermissionAssignedToRole(role_id=self.role_id, permission_id=self.permission_id))
        return rp


class RevokePermissionFromRole(Action):
    role_id: EntityId
    permission_id: EntityId

    async def handle(self, uow: UnitOfWork, actor: Actor) -> None:
        existing = await (
            uow.query(RolePermission)
            .where(RolePermission.role_id == self.role_id)
            .where(RolePermission.permission_id == self.permission_id)
            .one_or_none()
        )
        if existing is not None:
            await uow.delete(existing)
            uow.emit(
                PermissionRevokedFromRole(role_id=self.role_id, permission_id=self.permission_id),
            )
