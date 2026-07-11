from typing import Optional, Sequence, Union
from uuid import UUID

from sqlalchemy import and_, or_, select, true
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy.sql.elements import ColumnElement

from src.auth.users.models import User, user_group_association
from src.auth.users.services import UserGroupService, UserService
from src.core.exceptions import PermissionDeniedError, UnknownReferenceError
from src.core.utils import coerce_uuid
from src.lists.forms.models import Form
from src.lists.items.models import ListItem
from src.lists.models import List
from src.lists.views.models import View
from src.workspaces.models import Workspace

from .enums import ALL_ACTIONS, Action, ResourceType, expand_actions
from .models import PermissionGrant
from .schemas import GrantCreate, GrantRead

# Maps a resource type to its ORM model (for id/uuid resolution).
_MODELS = {
    ResourceType.workspace: Workspace,
    ResourceType.list: List,
    ResourceType.view: View,
    ResourceType.form: Form,
    ResourceType.item: ListItem,
}
# Child resources that hang off a list and carry a permissions_inherit flag.
_LIST_CHILDREN = {ResourceType.item: ListItem, ResourceType.view: View, ResourceType.form: Form}


class PermissionService:
    # -- resolution ---------------------------------------------------------

    @classmethod
    async def _security_context(
        cls, db: AsyncSession, resource_type: ResourceType, resource_id: int
    ) -> Optional[tuple[list[tuple[ResourceType, int]], int]]:
        """Return ``(chain, owner_id)`` for a live resource, or ``None`` if it is
        missing/deleted.

        ``chain`` is the set of ``(type, id)`` whose grants apply, built by walking
        up the hierarchy and stopping wherever a node breaks inheritance.
        ``owner_id`` is the owning workspace's owner (always full access).
        """
        rt = ResourceType(resource_type)

        if rt == ResourceType.workspace:
            ws = await db.get(Workspace, resource_id)
            if ws is None or ws.deleted_at is not None:
                return None
            return ([(ResourceType.workspace, ws.id)], ws.owner_id)

        chain: list[tuple[ResourceType, int]] = []
        child_inherits = True

        if rt == ResourceType.list:
            list_id = resource_id
        else:
            model = _LIST_CHILDREN.get(rt)
            if model is None:
                return None
            obj = await db.get(model, resource_id)
            if obj is None or obj.deleted_at is not None:
                return None
            chain.append((rt, obj.id))
            child_inherits = obj.permissions_inherit
            list_id = obj.list_id

        lst = await db.get(List, list_id)
        if lst is None or lst.deleted_at is not None:
            return None
        ws = await db.get(Workspace, lst.workspace_id)
        if ws is None or ws.deleted_at is not None:
            return None

        # The list joins the chain if the target *is* the list, or the child inherits.
        if rt == ResourceType.list or child_inherits:
            chain.append((ResourceType.list, lst.id))
            if lst.permissions_inherit:
                chain.append((ResourceType.workspace, ws.id))

        return (chain, ws.owner_id)

    @staticmethod
    async def _group_ids(db: AsyncSession, user_id: int) -> list[int]:
        # Query membership directly rather than touching user.groups: under asyncpg
        # a lazy relationship load outside the greenlet raises MissingGreenlet, and
        # the DISABLE_AUTH user isn't loaded with groups eager-loaded.
        rows = await db.execute(
            select(user_group_association.c.group_id).where(
                user_group_association.c.user_id == user_id
            )
        )
        return [row[0] for row in rows.all()]

    @classmethod
    def _principal_condition(
        cls, user_id: int, group_ids: Sequence[int]
    ) -> ColumnElement[bool]:
        cond: ColumnElement[bool] = PermissionGrant.user_id == user_id
        if group_ids:
            cond = or_(cond, PermissionGrant.group_id.in_(group_ids))
        return cond

    @classmethod
    async def effective_actions(
        cls,
        db: AsyncSession,
        user: User,
        resource_type: ResourceType,
        resource_id: int,
    ) -> set[Action]:
        ctx = await cls._security_context(db, resource_type, resource_id)
        if ctx is None:
            return set()
        chain, owner_id = ctx
        if user.id == owner_id:
            return set(ALL_ACTIONS)
        if not chain:
            return set()

        group_ids = await cls._group_ids(db, user.id)
        node_conditions = [
            and_(
                PermissionGrant.resource_type == rt,
                PermissionGrant.resource_id == rid,
            )
            for rt, rid in chain
        ]
        stmt = select(PermissionGrant.actions).where(
            or_(*node_conditions),
            cls._principal_condition(user.id, group_ids),
        )
        rows = (await db.execute(stmt)).scalars().all()
        granted: set[Action] = set()
        for actions in rows:
            granted |= expand_actions(actions or [])
        return granted

    @classmethod
    async def effective_actions_for_uuid(
        cls,
        db: AsyncSession,
        user: User,
        resource_type: ResourceType,
        resource_uuid: Union[str, UUID],
    ) -> Optional[set[Action]]:
        """Like ``effective_actions`` but resolves the resource by uuid; returns
        ``None`` when the resource does not exist."""
        resource_id = await cls._resolve_resource_id(db, resource_type, resource_uuid)
        if resource_id is None:
            return None
        return await cls.effective_actions(db, user, resource_type, resource_id)

    @classmethod
    async def has(
        cls,
        db: AsyncSession,
        user: User,
        resource_type: ResourceType,
        resource_id: int,
        action: Action,
    ) -> bool:
        return Action(action) in await cls.effective_actions(
            db, user, resource_type, resource_id
        )

    @classmethod
    async def require(
        cls,
        db: AsyncSession,
        user: User,
        resource_type: ResourceType,
        resource_id: int,
        action: Action,
    ) -> None:
        if not await cls.has(db, user, resource_type, resource_id, action):
            rt = ResourceType(resource_type)
            raise PermissionDeniedError(
                f"You do not have '{Action(action).value}' permission on this {rt.value}"
            )

    # -- collection-visibility filters (SQLAlchemy conditions) --------------

    @classmethod
    async def workspace_view_filter(
        cls, db: AsyncSession, user: User
    ) -> ColumnElement[bool]:
        """Condition for a Workspace select: owner, or holds a workspace-level grant."""
        group_ids = await cls._group_ids(db, user.id)
        granted = select(PermissionGrant.resource_id).where(
            PermissionGrant.resource_type == ResourceType.workspace,
            cls._principal_condition(user.id, group_ids),
        )
        return or_(Workspace.owner_id == user.id, Workspace.id.in_(granted))

    @classmethod
    async def list_query_filter(
        cls, db: AsyncSession, user: User
    ) -> ColumnElement[bool]:
        """Condition for a List select (joined to its Workspace): a list is visible
        when the caller owns the workspace, has a direct list grant, or inherits a
        workspace grant (and the list still inherits)."""
        group_ids = await cls._group_ids(db, user.id)
        principal = cls._principal_condition(user.id, group_ids)
        granted_lists = select(PermissionGrant.resource_id).where(
            PermissionGrant.resource_type == ResourceType.list, principal
        )
        granted_workspaces = select(PermissionGrant.resource_id).where(
            PermissionGrant.resource_type == ResourceType.workspace, principal
        )
        return or_(
            Workspace.owner_id == user.id,
            List.id.in_(granted_lists),
            and_(
                List.permissions_inherit.is_(True),
                Workspace.id.in_(granted_workspaces),
            ),
        )

    @classmethod
    async def item_view_filter(
        cls, db: AsyncSession, user: User, lst: List
    ) -> ColumnElement[bool]:
        """Condition for a ListItem select inside ``lst``."""
        if user.id == lst.workspace.owner_id:
            return true()
        group_ids = await cls._group_ids(db, user.id)
        granted = select(PermissionGrant.resource_id).where(
            PermissionGrant.resource_type == ResourceType.item,
            cls._principal_condition(user.id, group_ids),
        )
        list_viewable = await cls.has(db, user, ResourceType.list, lst.id, Action.view)
        if list_viewable:
            return or_(ListItem.permissions_inherit.is_(True), ListItem.id.in_(granted))
        return ListItem.id.in_(granted)

    # -- grant management ---------------------------------------------------

    @classmethod
    async def _resolve_resource_id(
        cls, db: AsyncSession, resource_type: ResourceType, resource_uuid: Union[str, UUID]
    ) -> Optional[int]:
        model = _MODELS.get(ResourceType(resource_type))
        parsed = coerce_uuid(resource_uuid)
        if model is None or parsed is None:
            return None
        row = await db.execute(
            select(model.id).where(model.uuid == parsed, model.deleted_at.is_(None))
        )
        return row.scalars().first()

    @classmethod
    async def upsert_grant(
        cls, db: AsyncSession, actor: User, payload: GrantCreate
    ) -> GrantRead:
        resource_id = await cls._resolve_resource_id(
            db, payload.resource_type, payload.resource_uuid
        )
        if resource_id is None:
            raise UnknownReferenceError("resource_uuid does not reference a live resource")
        await cls.require(db, actor, payload.resource_type, resource_id, Action.share)

        user_id: Optional[int] = None
        group_id: Optional[int] = None
        if payload.user_uuid is not None:
            target = await UserService.get_by_uuid(db, payload.user_uuid)
            if target is None:
                raise UnknownReferenceError("user_uuid does not reference a user")
            user_id = target.id
        else:
            group = await UserGroupService.get_by_uuid(db, payload.group_uuid)
            if group is None:
                raise UnknownReferenceError("group_uuid does not reference a group")
            group_id = group.id

        actions = [action.value for action in payload.actions]

        if user_id is not None:
            principal = and_(
                PermissionGrant.user_id == user_id, PermissionGrant.group_id.is_(None)
            )
        else:
            principal = and_(
                PermissionGrant.group_id == group_id, PermissionGrant.user_id.is_(None)
            )
        existing = (
            await db.execute(
                select(PermissionGrant).where(
                    PermissionGrant.resource_type == payload.resource_type,
                    PermissionGrant.resource_id == resource_id,
                    principal,
                )
            )
        ).scalars().first()

        if existing is not None:
            existing.actions = actions
            grant_uuid = existing.uuid
        else:
            grant = PermissionGrant(
                resource_type=payload.resource_type,
                resource_id=resource_id,
                user_id=user_id,
                group_id=group_id,
                actions=actions,
            )
            db.add(grant)
            await db.flush()
            grant_uuid = grant.uuid
        await db.commit()
        return await cls._read_grant(db, grant_uuid, payload.resource_uuid)

    @classmethod
    async def list_grants(
        cls,
        db: AsyncSession,
        actor: User,
        resource_type: ResourceType,
        resource_uuid: Union[str, UUID],
    ) -> Optional[list[GrantRead]]:
        resource_id = await cls._resolve_resource_id(db, resource_type, resource_uuid)
        if resource_id is None:
            return None
        await cls.require(db, actor, resource_type, resource_id, Action.share)
        grants = (
            await db.execute(
                select(PermissionGrant)
                .where(
                    PermissionGrant.resource_type == resource_type,
                    PermissionGrant.resource_id == resource_id,
                )
                .options(
                    selectinload(PermissionGrant.user),
                    selectinload(PermissionGrant.group),
                )
                .order_by(PermissionGrant.id)
            )
        ).scalars().all()
        return [cls._to_read(grant, resource_uuid) for grant in grants]

    @classmethod
    async def revoke_grant(
        cls, db: AsyncSession, actor: User, grant_uuid: Union[str, UUID]
    ) -> bool:
        parsed = coerce_uuid(grant_uuid)
        if parsed is None:
            return False
        grant = (
            await db.execute(
                select(PermissionGrant).where(PermissionGrant.uuid == parsed)
            )
        ).scalars().first()
        if grant is None:
            return False
        await cls.require(
            db, actor, grant.resource_type, grant.resource_id, Action.share
        )
        await db.delete(grant)
        await db.commit()
        return True

    @classmethod
    async def _read_grant(
        cls, db: AsyncSession, grant_uuid: UUID, resource_uuid: Union[str, UUID]
    ) -> GrantRead:
        grant = (
            await db.execute(
                select(PermissionGrant)
                .where(PermissionGrant.uuid == grant_uuid)
                .options(
                    selectinload(PermissionGrant.user),
                    selectinload(PermissionGrant.group),
                )
            )
        ).scalars().first()
        return cls._to_read(grant, resource_uuid)

    @staticmethod
    def _to_read(grant: PermissionGrant, resource_uuid: Union[str, UUID]) -> GrantRead:
        return GrantRead(
            uuid=grant.uuid,
            resource_type=grant.resource_type,
            resource_uuid=coerce_uuid(resource_uuid),
            user_uuid=grant.user.uuid if grant.user else None,
            user_username=grant.user.username if grant.user else None,
            group_uuid=grant.group.uuid if grant.group else None,
            group_name=grant.group.name if grant.group else None,
            actions=[Action(a) for a in (grant.actions or [])],
        )
