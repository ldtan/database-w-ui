from datetime import datetime, timedelta, timezone
from typing import Optional, Sequence, Union
from uuid import UUID

from sqlalchemy import func, or_, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy.sql.elements import ColumnElement

from src.core.config import settings
from src.core.exceptions import ConflictError, UnknownReferenceError
from src.core.utils import LIKE_ESCAPE, coerce_uuid, like_pattern
from src.workspaces.models import Workspace
from src.workspaces.services import WorkspaceService

from .columns.enums import ColumnType
from .columns.models import Column
from .forms.enums import FormType
from .forms.models import Form
from .items.models import ListItem
from .models import List
from .schemas import ListCreate, ListRead, ListUpdate
from .views.models import View
from .views.schemas import ViewConfig

# Everything scoped under a list; soft-deleted together when the list goes.
_LIST_CHILD_MODELS = (Column, ListItem, View, Form)


class ListService:
    @staticmethod
    def _select(owner_id: int):
        # A list is owned transitively through its workspace.
        return (
            select(List)
            .join(Workspace, List.workspace_id == Workspace.id)
            .where(
                List.deleted_at.is_(None),
                Workspace.deleted_at.is_(None),
                Workspace.owner_id == owner_id,
            )
            .options(selectinload(List.workspace), selectinload(List.primary_column))
        )

    @classmethod
    async def get_by_uuid(
        cls, db: AsyncSession, owner_id: int, uuid: Union[str, UUID]
    ) -> Optional[List]:
        parsed = coerce_uuid(uuid)
        if parsed is None:
            return None
        result = await db.execute(cls._select(owner_id).where(List.uuid == parsed))
        return result.scalars().first()

    @classmethod
    async def get_any_by_uuid(
        cls, db: AsyncSession, uuid: Union[str, UUID]
    ) -> Optional[List]:
        """Resolve a live list by uuid regardless of owner. Callers enforce
        permissions separately (see src/permissions)."""
        parsed = coerce_uuid(uuid)
        if parsed is None:
            return None
        result = await db.execute(
            select(List)
            .where(List.uuid == parsed, List.deleted_at.is_(None))
            .options(selectinload(List.workspace), selectinload(List.primary_column))
        )
        return result.scalars().first()

    @classmethod
    async def _visible_conditions(
        cls,
        db: AsyncSession,
        user,
        workspace_uuid: Optional[Union[str, UUID]],
        shared_only: bool,
        q: Optional[str],
    ) -> list[ColumnElement[bool]]:
        """The WHERE conditions for a visible-lists query, shared by ``list`` and
        ``count`` so the page and its total can never describe different queries.
        Assumes the statement already joins ``Workspace``."""
        from src.permissions.services import PermissionService

        conds: list[ColumnElement[bool]] = [
            List.deleted_at.is_(None),
            Workspace.deleted_at.is_(None),
            await PermissionService.list_query_filter(db, user),
        ]
        if workspace_uuid is not None:
            conds.append(Workspace.uuid == coerce_uuid(workspace_uuid))
        if shared_only:
            conds.append(Workspace.owner_id != user.id)
        if q and q.strip():
            pattern = like_pattern(q.strip())
            conds.append(
                or_(
                    List.display_name.ilike(pattern, escape=LIKE_ESCAPE),
                    List.name.ilike(pattern, escape=LIKE_ESCAPE),
                )
            )
        return conds

    @classmethod
    async def list(
        cls,
        db: AsyncSession,
        user,
        workspace_uuid: Optional[Union[str, UUID]] = None,
        shared_only: bool = False,
        skip: int = 0,
        limit: int = 100,
        q: Optional[str] = None,
    ) -> Sequence[ListRead]:
        """Lists the user can view (owned, directly granted, or inherited from a
        workspace grant), optionally scoped to one workspace or filtered by ``q``.

        ``shared_only`` keeps just the lists the user does not own. A list can be
        granted without its workspace being visible, so those lists sit inside a
        workspace the user cannot open — this is what backs the "Shared with me"
        section, the only way to reach them.

        Each row is enriched with its workspace owner's username, and with the
        workspace's display name *only when the caller can view that workspace* —
        naming a workspace the caller was denied would leak it.
        """
        from src.permissions.services import PermissionService

        conds = await cls._visible_conditions(db, user, workspace_uuid, shared_only, q)
        stmt = (
            select(List)
            .join(Workspace, List.workspace_id == Workspace.id)
            .where(*conds)
            .options(
                selectinload(List.workspace).selectinload(Workspace.owner),
                selectinload(List.primary_column),
            )
            .order_by(List.id)
            .offset(skip)
            .limit(limit)
        )
        lists = (await db.execute(stmt)).scalars().all()

        # Which of these workspaces the caller may actually view — the gate on
        # exposing a workspace's name (see the docstring and known-gaps).
        ws_ids = {lst.workspace_id for lst in lists}
        viewable: set[int] = set()
        if ws_ids:
            ws_filter = await PermissionService.workspace_view_filter(db, user)
            rows = await db.execute(
                select(Workspace.id).where(Workspace.id.in_(ws_ids), ws_filter)
            )
            viewable = set(rows.scalars().all())

        reads: list[ListRead] = []
        for lst in lists:
            read = ListRead.model_validate(lst)
            read.owner_username = lst.workspace.owner.username
            read.workspace_display_name = (
                lst.workspace.display_name if lst.workspace_id in viewable else None
            )
            reads.append(read)
        return reads

    @classmethod
    async def count(
        cls,
        db: AsyncSession,
        user,
        workspace_uuid: Optional[Union[str, UUID]] = None,
        shared_only: bool = False,
        q: Optional[str] = None,
    ) -> int:
        """Total matching lists, ignoring skip/limit — what the paginator needs."""
        conds = await cls._visible_conditions(db, user, workspace_uuid, shared_only, q)
        stmt = (
            select(func.count(func.distinct(List.id)))
            .select_from(List)
            .join(Workspace, List.workspace_id == Workspace.id)
            .where(*conds)
        )
        return await db.scalar(stmt) or 0

    @classmethod
    async def create(cls, db: AsyncSession, payload: ListCreate) -> List:
        # Owner-agnostic: the router enforces 'configure' on the target workspace.
        workspace = await WorkspaceService.get_any_by_uuid(db, payload.workspace_uuid)
        if workspace is None:
            raise UnknownReferenceError(
                "workspace_uuid does not reference a workspace"
            )

        lst = List(
            workspace_id=workspace.id,
            name=payload.name,
            display_name=payload.display_name,
            description=payload.description,
            settings=payload.settings or {},
        )
        db.add(lst)
        try:
            await db.flush()
            cls._provision_defaults(db, lst)
            new_uuid = lst.uuid
            await db.commit()
        except IntegrityError as exc:
            await db.rollback()
            raise ConflictError("A list with this name already exists") from exc

        return await cls.get_any_by_uuid(db, new_uuid)

    @classmethod
    async def update(
        cls,
        db: AsyncSession,
        uuid: Union[str, UUID],
        payload: ListUpdate,
    ) -> Optional[List]:
        # Owner-agnostic: the router enforces 'configure' (and 'share' when
        # permissions_inherit is being changed) before calling this.
        lst = await cls.get_any_by_uuid(db, uuid)
        if lst is None:
            return None

        data = payload.model_dump(exclude_unset=True)

        if "primary_column_uuid" in data:
            column_uuid = data.pop("primary_column_uuid")
            lst.primary_column_id = await cls._resolve_primary_column_id(
                db, lst.id, column_uuid
            )

        if "workspace_uuid" in data:
            target_uuid = data.pop("workspace_uuid")
            if target_uuid is not None:
                workspace = await WorkspaceService.get_any_by_uuid(db, target_uuid)
                if workspace is None:
                    raise UnknownReferenceError(
                        "workspace_uuid does not reference a workspace"
                    )
                lst.workspace_id = workspace.id

        # Treat an explicit null name as "leave unchanged" rather than clearing it.
        if data.get("name") is None:
            data.pop("name", None)
        if data.get("display_name") is None:
            data.pop("display_name", None)
        if data.get("permissions_inherit") is None:
            data.pop("permissions_inherit", None)

        for field, value in data.items():
            setattr(lst, field, value)

        try:
            await db.flush()
            target_uuid = lst.uuid
            await db.commit()
        except IntegrityError as exc:
            await db.rollback()
            raise ConflictError("A list with this name already exists") from exc

        return await cls.get_any_by_uuid(db, target_uuid)

    @classmethod
    async def delete(cls, db: AsyncSession, uuid: Union[str, UUID]) -> bool:
        # Owner-agnostic: the router enforces 'delete' before calling this.
        lst = await cls.get_any_by_uuid(db, uuid)
        if lst is None:
            return False

        now = datetime.now(timezone.utc)
        await cls._cascade_descendants(db, [lst.id], now)
        lst.deleted_at = now
        # Deleting a *list* is the exception to "soft delete destroys nothing", and
        # deliberately so. Deleting a row or an account leaves the cells naming it
        # intact, because they can still be resolved and restored. A deleted list
        # cannot: every value in a lookup column pointing at it is stale at once, and
        # the permission resolver returns *no access* for a deleted resource, so those
        # labels could not even be authorised. Restoring a list would mean
        # un-cascading its columns, records, views and forms — a feature that does not
        # exist. Until it does, this stays a scrub.
        await cls._scrub_references_to_lists(db, [lst.uuid])
        await db.commit()
        return True

    @classmethod
    async def _cascade_descendants(
        cls, db: AsyncSession, list_ids: Sequence[int], when: datetime
    ) -> None:
        """Soft-delete every child (columns, records, views, forms) of the given
        lists in bulk. Does not touch the lists themselves and does not commit."""
        if not list_ids:
            return
        for model in _LIST_CHILD_MODELS:
            await db.execute(
                update(model)
                .where(model.list_id.in_(list_ids), model.deleted_at.is_(None))
                .values(deleted_at=when)
            )

    @classmethod
    async def soft_delete_for_workspace(
        cls, db: AsyncSession, workspace_id: int, when: datetime
    ) -> None:
        """Soft-delete all lists in a workspace and their descendants, in bulk.
        Does not commit — the caller (workspace delete) owns the transaction."""
        result = await db.execute(
            select(List.id, List.uuid).where(
                List.workspace_id == workspace_id, List.deleted_at.is_(None)
            )
        )
        rows = result.all()
        if not rows:
            return
        list_ids = [row.id for row in rows]
        list_uuids = [row.uuid for row in rows]
        await cls._cascade_descendants(db, list_ids, when)
        await db.execute(
            update(List).where(List.id.in_(list_ids)).values(deleted_at=when)
        )
        await cls._scrub_references_to_lists(db, list_uuids)

    @classmethod
    async def _scrub_references_to_lists(
        cls, db: AsyncSession, list_uuids: Sequence[UUID]
    ) -> None:
        """Remove dangling lookup references to rows in now-deleted lists.

        Lookup values are raw uuids stored in another row's JSONB, so deleting a
        target list leaves those pointers unresolvable but present. Since a lookup
        column targets exactly one list, deleting that list makes *every* value in
        the column stale — so drop the column's key from the referencing rows.
        Only columns on surviving lists matter (those on deleted lists are already
        soft-deleted and thus excluded)."""
        if not list_uuids:
            return
        targets = [str(u) for u in list_uuids]
        lookup_columns = (
            await db.execute(
                select(Column).where(
                    Column.type == ColumnType.lookup,
                    Column.deleted_at.is_(None),
                    Column.config["target_list_uuid"].astext.in_(targets),
                )
            )
        ).scalars().all()
        for column in lookup_columns:
            key = str(column.uuid)
            await db.execute(
                update(ListItem)
                .where(
                    ListItem.list_id == column.list_id,
                    ListItem.deleted_at.is_(None),
                    ListItem.data.has_key(key),
                )
                .values(data=ListItem.data.op("-")(key))
            )

    # -- the bin: view, restore, and purge soft-deleted lists ---------------

    @staticmethod
    def _select_deleted(workspace_id: int):
        """Lists in a workspace's bin: soft-deleted and still inside the retention
        window. Older ones are past ``PURGE_DELETED_AFTER_DAYS`` and on their way out,
        so they are no longer offered as restorable — exactly as the records bin does.

        Scoped to a live workspace, so these are lists deleted on their own; a list
        that went down with its workspace lives in that workspace's own bin instead.
        """
        cutoff = datetime.now(timezone.utc) - timedelta(
            days=settings.PURGE_DELETED_AFTER_DAYS
        )
        return select(List).where(
            List.workspace_id == workspace_id,
            List.deleted_at.is_not(None),
            List.deleted_at >= cutoff,
        )

    @classmethod
    async def list_deleted_for_workspace(
        cls, db: AsyncSession, workspace_id: int, skip: int = 0, limit: int = 100
    ) -> Sequence[List]:
        """A workspace's list bin, most recently deleted first."""
        stmt = (
            cls._select_deleted(workspace_id)
            .options(
                selectinload(List.workspace), selectinload(List.primary_column)
            )
            .order_by(List.deleted_at.desc(), List.id.desc())
            .offset(skip)
            .limit(limit)
        )
        return (await db.execute(stmt)).scalars().all()

    @classmethod
    async def count_deleted_for_workspace(
        cls, db: AsyncSession, workspace_id: int
    ) -> int:
        """How many lists are in the workspace's bin — the paginator's total, built
        off the same predicate as the listing so the two cannot drift."""
        stmt = select(func.count()).select_from(
            cls._select_deleted(workspace_id).subquery()
        )
        return await db.scalar(stmt) or 0

    @classmethod
    async def get_deleted_by_uuid(
        cls, db: AsyncSession, uuid: Union[str, UUID]
    ) -> Optional[List]:
        """A single list from the bin, regardless of owner — the router enforces the
        permission. ``get_any_by_uuid`` filters deleted rows out, so restore/purge
        need their own fetch. The workspace is eager-loaded so the caller can gate on
        it and render the row."""
        parsed = coerce_uuid(uuid)
        if parsed is None:
            return None
        cutoff = datetime.now(timezone.utc) - timedelta(
            days=settings.PURGE_DELETED_AFTER_DAYS
        )
        result = await db.execute(
            select(List)
            .where(
                List.uuid == parsed,
                List.deleted_at.is_not(None),
                List.deleted_at >= cutoff,
            )
            .options(
                selectinload(List.workspace), selectinload(List.primary_column)
            )
        )
        return result.scalars().first()

    @classmethod
    async def restore(
        cls, db: AsyncSession, uuid: Union[str, UUID]
    ) -> Optional[List]:
        """Bring a soft-deleted list back, along with the columns, records, views and
        forms that went down with it. Returns None if it is not in the bin.

        Delete stamped the list and every descendant with one identical ``deleted_at``
        (see ``_cascade_descendants``); restore reverses exactly that set by matching
        it, so a record the user had binned on its own — a different timestamp — stays
        in the records bin.

        A list name is unique per workspace among live rows, so restoring can collide
        with a name taken while this one sat deleted and raise ``ConflictError`` (409).

        Note: references *into* this list were scrubbed when it was deleted (see
        ``delete``), so cross-list lookups pointing at its rows are not brought back —
        the list, its columns, and its records are.
        """
        lst = await cls.get_deleted_by_uuid(db, uuid)
        if lst is None:
            return None

        when = lst.deleted_at
        for model in _LIST_CHILD_MODELS:
            await db.execute(
                update(model)
                .where(model.list_id == lst.id, model.deleted_at == when)
                .values(deleted_at=None)
            )
        lst.deleted_at = None
        try:
            await db.commit()
        except IntegrityError as exc:
            await db.rollback()
            raise ConflictError("A list with this name already exists") from exc
        return await cls.get_any_by_uuid(db, uuid)

    @classmethod
    async def restore_for_workspace(
        cls, db: AsyncSession, workspace_id: int, when: datetime
    ) -> None:
        """Un-delete the lists (and their descendants) a workspace took down when it
        was deleted — those stamped with the workspace's own ``deleted_at``. Lists the
        owner had binned independently carry a different timestamp and are left alone.
        Does not commit — the caller (workspace restore) owns the transaction."""
        rows = await db.execute(
            select(List.id).where(
                List.workspace_id == workspace_id, List.deleted_at == when
            )
        )
        list_ids = list(rows.scalars().all())
        if not list_ids:
            return
        for model in _LIST_CHILD_MODELS:
            await db.execute(
                update(model)
                .where(model.list_id.in_(list_ids), model.deleted_at == when)
                .values(deleted_at=None)
            )
        await db.execute(
            update(List).where(List.id.in_(list_ids)).values(deleted_at=None)
        )

    @classmethod
    async def purge(cls, db: AsyncSession, uuid: Union[str, UUID]) -> bool:
        """Permanently destroy a single soft-deleted list and everything under it.
        Returns False if it is not in the bin.

        Mirrors the list purge job's cascade, but for one list in a single
        transaction: break the circular ``primary_column_id`` foreign key, delete the
        polymorphic grants of the list and its descendants (nothing else does), then
        the records, views, forms, columns, and finally the list. Inbound references
        were already scrubbed when the list was soft-deleted, so there is nothing to
        clear here. Irreversible.
        """
        lst = await cls.get_deleted_by_uuid(db, uuid)
        if lst is None:
            return False

        ids = [lst.id]
        # Break the circular dependency (lists.primary_column_id -> columns.id) first,
        # or deleting the columns violates the foreign key.
        await db.execute(
            text("UPDATE lists SET primary_column_id = NULL WHERE id = ANY(:ids)"),
            {"ids": ids},
        )
        await db.execute(
            text(
                """
                DELETE FROM permission_grants
                 WHERE (resource_type = 'list' AND resource_id = ANY(:ids))
                    OR (resource_type = 'item' AND resource_id IN (SELECT id FROM list_items WHERE list_id = ANY(:ids)))
                    OR (resource_type = 'view' AND resource_id IN (SELECT id FROM views WHERE list_id = ANY(:ids)))
                    OR (resource_type = 'form' AND resource_id IN (SELECT id FROM forms WHERE list_id = ANY(:ids)))
                """
            ),
            {"ids": ids},
        )
        await db.execute(text("DELETE FROM list_items WHERE list_id = ANY(:ids)"), {"ids": ids})
        await db.execute(text("DELETE FROM views WHERE list_id = ANY(:ids)"), {"ids": ids})
        await db.execute(text("DELETE FROM forms WHERE list_id = ANY(:ids)"), {"ids": ids})
        await db.execute(text("DELETE FROM columns WHERE list_id = ANY(:ids)"), {"ids": ids})
        await db.execute(text("DELETE FROM lists WHERE id = ANY(:ids)"), {"ids": ids})
        await db.commit()
        return True

    @staticmethod
    def _provision_defaults(db: AsyncSession, lst: List) -> None:
        """Seed a new list with default audit columns, a grid view and forms.

        Forms start with an empty ``fields`` list, which the convention treats as
        "all columns" until the user customizes the layout.
        """
        for name, display_name, col_type, config, position in [
            (
                "created_at",
                "Created at",
                ColumnType.datetime,
                {"default": "now", "read_only": True},
                1000,
            ),
            (
                "updated_at",
                "Updated at",
                ColumnType.datetime,
                {"onupdate": "now", "read_only": True},
                1001,
            ),
            (
                "created_by",
                "Created by",
                ColumnType.user,
                {"default": "current_user", "read_only": True},
                1002,
            ),
            (
                "updated_by",
                "Updated by",
                ColumnType.user,
                {"onupdate": "current_user", "read_only": True},
                1003,
            ),
        ]:
            db.add(
                Column(
                    list_id=lst.id,
                    name=name,
                    display_name=display_name,
                    type=col_type,
                    position=position,
                    config=config,
                )
            )

        db.add(
            View(
                list_id=lst.id,
                name="grid",
                display_name="Grid",
                position=0,
                config=ViewConfig().model_dump(mode="json"),
            )
        )
        db.add(
            Form(
                list_id=lst.id,
                name="create",
                display_name="Create",
                type=FormType.create,
                is_default=True,
                config={"fields": []},
            )
        )
        db.add(
            Form(
                list_id=lst.id,
                name="update",
                display_name="Update",
                type=FormType.update,
                is_default=True,
                config={"fields": []},
            )
        )

    @staticmethod
    async def _resolve_primary_column_id(
        db: AsyncSession, list_id: int, column_uuid: Optional[UUID]
    ) -> Optional[int]:
        if column_uuid is None:
            return None
        result = await db.execute(
            select(Column.id).where(
                Column.uuid == column_uuid,
                Column.list_id == list_id,
                Column.deleted_at.is_(None),
            )
        )
        column_id = result.scalars().first()
        if column_id is None:
            raise UnknownReferenceError(
                "primary_column_uuid does not reference a column of this list"
            )
        return column_id
