from typing import Optional, Sequence, Union
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy import and_, func, or_, select, true
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy.sql.elements import ColumnElement

from src.auth.users.models import User, UserGroup, user_group_association
from src.auth.users.services import UserGroupService, UserService
from src.core.exceptions import PermissionDeniedError, UnknownReferenceError
from src.core.utils import coerce_uuid
from src.lists.forms.models import Form
from src.lists.items.models import ListItem
from src.lists.models import List
from src.lists.views.models import View
from src.workspaces.models import Workspace

from .enums import ALL_ACTIONS, CONFERRED_BY, Action, ResourceType, expand_actions
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
    async def _system_group_ids(db: AsyncSession) -> list[int]:
        """The virtual groups every authenticated caller belongs to — currently just
        "Authenticated Users", whose membership is implicit (no rows in
        user_group_association). Cached per session: it is stable for the life of a
        request and read on every authorization check."""
        cached = db.info.get("system_group_ids")
        if cached is None:
            rows = await db.execute(
                select(UserGroup.id).where(
                    UserGroup.is_system.is_(True), UserGroup.deleted_at.is_(None)
                )
            )
            cached = [row[0] for row in rows.all()]
            db.info["system_group_ids"] = cached
        return cached

    @classmethod
    async def _group_ids(cls, db: AsyncSession, user_id: int) -> list[int]:
        # Query membership directly rather than touching user.groups: under asyncpg
        # a lazy relationship load outside the greenlet raises MissingGreenlet, and
        # the DISABLE_AUTH user isn't loaded with groups eager-loaded.
        rows = await db.execute(
            select(user_group_association.c.group_id).where(
                user_group_association.c.user_id == user_id
            )
        )
        ids = [row[0] for row in rows.all()]
        # Every authenticated user is implicitly in the system group(s); _group_ids is
        # only ever called for a resolved user, so this is exactly "Authenticated
        # Users". A grant to that group therefore reaches everyone, here and through
        # every collection filter that shares this helper.
        ids += await cls._system_group_ids(db)
        return ids

    @staticmethod
    def _confers(action: Action) -> ColumnElement[bool]:
        """Matches grants whose stored actions confer ``action``.

        The collection filters below are SQL and cannot call ``expand_actions``, so
        they test the action list against ``CONFERRED_BY`` with jsonb ``?|`` — true
        when the array holds any of those strings. Matching on the existence of a
        grant row instead, as they used to, would put a lookup-only list straight
        back into "Shared with me" and defeat the grant.
        """
        return PermissionGrant.actions.has_any(
            sa.literal(
                [granted.value for granted in CONFERRED_BY[action]],
                postgresql.ARRAY(sa.Text),
            )
        )

    @staticmethod
    def _not_expired() -> ColumnElement[bool]:
        """A grant confers access only while unexpired. Compared against the DB clock
        (`now()`), so it is consistent within a transaction and timezone-correct."""
        return or_(
            PermissionGrant.expires_at.is_(None),
            PermissionGrant.expires_at > func.now(),
        )

    @classmethod
    def _principal_condition(
        cls, user_id: int, group_ids: Sequence[int]
    ) -> ColumnElement[bool]:
        """Matches grants held by this user (or one of their groups) that are still
        live. Every authorization read funnels through here — the per-resource checks
        (`effective_actions`, `effective_actions_bulk`) and the collection-visibility
        filters (`workspace_view_filter`, `list_query_filter`, `item_view_filter`) —
        so folding expiry in here makes an expired grant invisible to all of them at
        once. Grant *management* builds its own principal clause and is unaffected, so
        a re-share can still find and update an expired row."""
        cond: ColumnElement[bool] = PermissionGrant.user_id == user_id
        if group_ids:
            cond = or_(cond, PermissionGrant.group_id.in_(group_ids))
        return and_(cond, cls._not_expired())

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
    async def effective_actions_bulk(
        cls,
        db: AsyncSession,
        user: User,
        refs: Sequence[tuple[ResourceType, UUID]],
    ) -> dict[tuple[ResourceType, UUID], set[Action]]:
        """Resolve effective actions for many resources at once.

        Resources are loaded in one query per type, which also warms the identity
        map so the per-resource chain walk below is served from memory; group
        membership and the grant rows are then fetched once for the whole batch.
        Unknown/deleted resources are absent from the result.
        """
        by_type: dict[ResourceType, set[UUID]] = {}
        for resource_type, resource_uuid in refs:
            parsed = coerce_uuid(resource_uuid)
            if parsed is not None:
                by_type.setdefault(ResourceType(resource_type), set()).add(parsed)

        ids: dict[tuple[ResourceType, UUID], int] = {}
        for rt, uuids in by_type.items():
            model = _MODELS.get(rt)
            if model is None:
                continue
            rows = await db.execute(
                select(model).where(
                    model.uuid.in_(uuids), model.deleted_at.is_(None)
                )
            )
            for obj in rows.scalars().all():
                ids[(rt, obj.uuid)] = obj.id

        contexts: dict[tuple[ResourceType, UUID], tuple[list, int]] = {}
        for ref, resource_id in ids.items():
            ctx = await cls._security_context(db, ref[0], resource_id)
            if ctx is not None:
                contexts[ref] = ctx

        out: dict[tuple[ResourceType, UUID], set[Action]] = {}
        pending: dict[tuple[ResourceType, UUID], list] = {}
        for ref, (chain, owner_id) in contexts.items():
            if user.id == owner_id:
                out[ref] = set(ALL_ACTIONS)
            else:
                out[ref] = set()
                pending[ref] = chain

        nodes = {node for chain in pending.values() for node in chain}
        if not nodes:
            return out

        group_ids = await cls._group_ids(db, user.id)
        rows = await db.execute(
            select(
                PermissionGrant.resource_type,
                PermissionGrant.resource_id,
                PermissionGrant.actions,
            ).where(
                or_(
                    *[
                        and_(
                            PermissionGrant.resource_type == rt,
                            PermissionGrant.resource_id == rid,
                        )
                        for rt, rid in nodes
                    ]
                ),
                cls._principal_condition(user.id, group_ids),
            )
        )
        by_node: dict[tuple[ResourceType, int], set[Action]] = {}
        for rt, rid, actions in rows.all():
            node = (ResourceType(rt), rid)
            by_node.setdefault(node, set()).update(expand_actions(actions or []))

        for ref, chain in pending.items():
            granted: set[Action] = set()
            for node in chain:
                granted |= by_node.get(node, set())
            out[ref] = granted
        return out

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
        """Condition for a Workspace select: owner, or holds a workspace-level grant
        that confers ``view`` (see ``_confers``)."""
        group_ids = await cls._group_ids(db, user.id)
        granted = select(PermissionGrant.resource_id).where(
            PermissionGrant.resource_type == ResourceType.workspace,
            cls._principal_condition(user.id, group_ids),
            cls._confers(Action.view),
        )
        return or_(Workspace.owner_id == user.id, Workspace.id.in_(granted))

    @classmethod
    async def list_query_filter(
        cls, db: AsyncSession, user: User, action: Action = Action.view
    ) -> ColumnElement[bool]:
        """Condition for a List select (joined to its Workspace): a list is visible
        when the caller owns the workspace, has a direct list grant conferring
        ``action``, or inherits a workspace grant conferring it (and the list still
        inherits).

        ``action`` defaults to ``view``, so a lookup-only grant — a list whose
        *values* may be read through someone else's lookup column while the list
        itself stays unlisted — does not reach "Shared with me", the only surface
        those lists would otherwise appear on. The lookup target picker passes
        ``lookup`` to get the wider set it must offer; nothing else should."""
        group_ids = await cls._group_ids(db, user.id)
        principal = and_(
            cls._principal_condition(user.id, group_ids), cls._confers(action)
        )
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
        cls, db: AsyncSession, user: User, lst: List, action: Action = Action.view
    ) -> ColumnElement[bool]:
        """Condition for a ListItem select inside ``lst``.

        ``action`` is the list-level access being exercised. It is ``view`` for every
        read of the records themselves, and ``lookup`` for the paths that only
        resolve a row's *label* (the options endpoint, the export formatter, search
        by linked label). A lookup-only grantee holds no ``view``, so leaving this at
        the default would drop them to "items granted individually" — an empty picker
        rather than the reference list they were given. Per-record grants still apply
        either way: ``lookup`` relaxes the list-level gate, not row-level ones.
        """
        if user.id == lst.workspace.owner_id:
            return true()
        group_ids = await cls._group_ids(db, user.id)
        granted = select(PermissionGrant.resource_id).where(
            PermissionGrant.resource_type == ResourceType.item,
            cls._principal_condition(user.id, group_ids),
        )
        list_viewable = await cls.has(db, user, ResourceType.list, lst.id, action)
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
            # Written verbatim: re-sharing with no expiry clears a previous one, so
            # the caller must resend an expiry it means to keep (the Share panel does).
            existing.expires_at = payload.expires_at
            grant_uuid = existing.uuid
        else:
            grant = PermissionGrant(
                resource_type=payload.resource_type,
                resource_id=resource_id,
                user_id=user_id,
                group_id=group_id,
                actions=actions,
                expires_at=payload.expires_at,
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
        # A grant whose principal has been deleted is inert — the account cannot log
        # in, and a deleted group has no members — but it lingers until the purge
        # finalizes them. Listing it would answer "who has access to this?" with the
        # names of people who don't, which is worse than useless to someone auditing.
        live = [
            grant
            for grant in grants
            if not (
                (grant.user is not None and grant.user.deleted_at is not None)
                or (grant.group is not None and grant.group.deleted_at is not None)
            )
        ]
        return [cls._to_read(grant, resource_uuid) for grant in live]

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
            expires_at=grant.expires_at,
        )
