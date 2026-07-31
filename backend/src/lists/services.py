from copy import deepcopy
from datetime import datetime, timedelta, timezone
from typing import Optional, Sequence, Union
from uuid import UUID, uuid7

from sqlalchemy import func, not_, or_, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy.sql.elements import ColumnElement

from src.core.config import settings
from src.core.exceptions import ConflictError, UnknownReferenceError
from src.core.utils import LIKE_ESCAPE, coerce_uuid, like_pattern
# The enums are a leaf module (no service imports), so unlike PermissionService —
# deferred throughout this file — they can be imported here without a cycle.
from src.permissions.enums import Action
from src.workspaces.models import Workspace
from src.workspaces.services import WorkspaceService

from .columns.enums import ColumnType
from .columns.models import Column
from .forms.enums import FormType
from .forms.models import Form
from .items.models import ListItem
from .models import List
from .purge import drain_records
from .schemas import ListCreate, ListRead, ListUpdate
from .metrics.models import Metric
from .views.models import View
from .views.schemas import ViewConfig

# Everything scoped under a list; soft-deleted together when the list goes.
_LIST_CHILD_MODELS = (Column, ListItem, View, Form, Metric)


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
        action: Action = Action.view,
        workspace_hidden: Optional[bool] = None,
    ) -> list[ColumnElement[bool]]:
        """The WHERE conditions for a visible-lists query, shared by ``list`` and
        ``count`` so the page and its total can never describe different queries.
        Assumes the statement already joins ``Workspace``."""
        from src.permissions.services import PermissionService

        conds: list[ColumnElement[bool]] = [
            List.deleted_at.is_(None),
            Workspace.deleted_at.is_(None),
            await PermissionService.list_query_filter(db, user, action),
        ]
        if workspace_uuid is not None:
            conds.append(Workspace.uuid == coerce_uuid(workspace_uuid))
        if shared_only:
            conds.append(Workspace.owner_id != user.id)
        if workspace_hidden is not None:
            # Whether the caller can open this list's *parent workspace* — a
            # different question from whether they own it, and the one that decides
            # if a shared list has a home in the nav tree or hangs off nothing.
            # Reuses the workspace listing's own membership test, so the two can
            # never disagree about what is reachable. Negating it is null-safe:
            # PermissionGrant.resource_id is non-nullable, so the inner IN yields no
            # NULL for NOT to swallow.
            visible = await PermissionService.workspace_view_filter(db, user)
            conds.append(not_(visible) if workspace_hidden else visible)
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
        lookup_targets: bool = False,
        workspace_hidden: Optional[bool] = None,
    ) -> Sequence[ListRead]:
        """Lists the user can view (owned, directly granted, or inherited from a
        workspace grant), optionally scoped to one workspace or filtered by ``q``.

        ``shared_only`` keeps just the lists the user does not own — every list
        reachable through someone else's grant, whether that grant sits on the list
        or on its workspace. It is the ownership axis, and the lookup target picker
        wants exactly it: any list you do not own is a legitimate lookup target.

        ``workspace_hidden`` is the *reachability* axis, and the two come apart. A
        list granted without its workspace sits inside a workspace the user cannot
        open, so it hangs off nothing in the nav tree — passing True keeps just
        those, which is what the "Shared with me" section needs. Lists that came in
        through a workspace grant are already reachable by browsing that workspace,
        and listing them again there would show them twice.

        ``lookup_targets`` widens the query from ``view`` to ``lookup``, adding the
        lists shared *only* as lookup targets. It exists for one caller — the target
        picker in the column dialog — because a list granted `lookup` is deliberately
        absent everywhere else, and a target you cannot name is a target you cannot
        point a column at. It names lists the caller cannot open; that is the minimum
        the grant has to disclose to be usable, and no more than the grantee already
        learns from the labels it lets them resolve.

        Each row is enriched with its workspace owner's username, and with the
        workspace's display name *only when the caller can view that workspace* —
        naming a workspace the caller was denied would leak it.
        """
        from src.permissions.services import PermissionService

        conds = await cls._visible_conditions(
            db,
            user,
            workspace_uuid,
            shared_only,
            q,
            Action.lookup if lookup_targets else Action.view,
            workspace_hidden,
        )
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
        lookup_targets: bool = False,
        workspace_hidden: Optional[bool] = None,
    ) -> int:
        """Total matching lists, ignoring skip/limit — what the paginator needs."""
        conds = await cls._visible_conditions(
            db,
            user,
            workspace_uuid,
            shared_only,
            q,
            Action.lookup if lookup_targets else Action.view,
            workspace_hidden,
        )
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
                # Assign the relationship, not the scalar FK: List.workspace is
                # eager-loaded, and a loaded many-to-one overwrites a bare
                # workspace_id change on flush, leaving the list in place.
                lst.workspace = workspace

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

    @staticmethod
    async def _free_name_in_workspace(
        db: AsyncSession, workspace_id: int, base: str, exclude_id: int
    ) -> str:
        """A list ``name`` unique among the live lists of ``workspace_id``.

        Returns ``base`` when it is free, else ``base_2``, ``base_3``, … — used when
        a transfer lands a list in an inbox that already holds one of that name, so a
        transfer never fails on a per-workspace name clash. The suffix keeps the
        NameSlug shape (lowercase letters/digits/underscores)."""
        rows = await db.execute(
            select(List.name).where(
                List.workspace_id == workspace_id,
                List.deleted_at.is_(None),
                List.id != exclude_id,
            )
        )
        taken = set(rows.scalars().all())
        if base not in taken:
            return base
        n = 2
        while f"{base}_{n}" in taken:
            n += 1
        return f"{base}_{n}"

    @classmethod
    async def transfer_to_user(
        cls, db: AsyncSession, uuid: Union[str, UUID], recipient_id: int
    ) -> Optional[List]:
        """Hand a list to another user by moving it into their "Incoming" workspace.

        Ownership here *is* workspace membership, so a transfer is a move into a
        workspace the recipient owns — their immutable inbox, minted on demand. The
        router enforces that the caller is the list's current owner before calling.

        Grants are deliberately left untouched: a grant points at the list by
        ``resource_id``, which does not change, so existing shares survive the move.
        The giver's access was the implicit workspace-owner short-circuit, and it ends
        the moment the list leaves their workspace — no grant to revoke. The recipient
        becomes the new implicit owner. (See docs/planned-features.md #3.)
        """
        lst = await cls.get_any_by_uuid(db, uuid)
        if lst is None:
            return None

        inbox = await WorkspaceService.get_or_create_inbox(db, recipient_id)
        # A list name is unique per workspace; auto-suffix so the landing never 409s.
        lst.name = await cls._free_name_in_workspace(
            db, inbox.id, lst.name, exclude_id=lst.id
        )
        # Assign the relationship, not the scalar FK: get_any_by_uuid eager-loads
        # List.workspace, and a loaded many-to-one overwrites a bare workspace_id
        # change on flush — so setting the FK alone silently leaves the list put.
        lst.workspace = inbox

        await db.flush()
        target_uuid = lst.uuid
        await db.commit()
        return await cls.get_any_by_uuid(db, target_uuid)

    @staticmethod
    async def _free_copy_name(
        db: AsyncSession, workspace_id: int, base: str
    ) -> str:
        """A free list ``name`` for a clone in ``workspace_id``: ``base_copy``, then
        ``base_copy_2``, ``base_copy_3``, … The suffix keeps the NameSlug shape
        (lowercase letters/digits/underscores) and the name unique per workspace."""
        rows = await db.execute(
            select(List.name).where(
                List.workspace_id == workspace_id, List.deleted_at.is_(None)
            )
        )
        taken = set(rows.scalars().all())
        candidate = f"{base}_copy"
        if candidate not in taken:
            return candidate
        n = 2
        while f"{candidate}_{n}" in taken:
            n += 1
        return f"{candidate}_{n}"

    @staticmethod
    def _remap_view_config(config: dict, col_map: dict[str, str]) -> dict:
        """Rewrite every column-uuid reference in a view's config through ``col_map``.

        A view names columns by uuid in its filters, sorts, group_by, hidden_columns
        and column_order (views/schemas.ViewConfig); a clone's columns are new rows
        with new uuids, so each reference has to be repointed or the cloned view
        would filter/sort/hide the *source's* columns. Operates on the raw dict (not
        the typed schema) so an unrecognised or legacy shape is copied through
        untouched rather than rejected. References with no mapping are left as-is."""
        cfg = deepcopy(config or {})

        def remap(value):
            return col_map.get(value, value)

        filters = cfg.get("filters")
        if isinstance(filters, dict):
            for cond in filters.get("conditions", []) or []:
                if isinstance(cond, dict) and "column_uuid" in cond:
                    cond["column_uuid"] = remap(cond["column_uuid"])
        for sort in cfg.get("sorts", []) or []:
            if isinstance(sort, dict) and "column_uuid" in sort:
                sort["column_uuid"] = remap(sort["column_uuid"])
        group_by = cfg.get("group_by")
        if isinstance(group_by, dict) and "column_uuid" in group_by:
            group_by["column_uuid"] = remap(group_by["column_uuid"])
        if isinstance(cfg.get("hidden_columns"), list):
            cfg["hidden_columns"] = [remap(c) for c in cfg["hidden_columns"]]
        if isinstance(cfg.get("column_order"), list):
            cfg["column_order"] = [remap(c) for c in cfg["column_order"]]
        return cfg

    @staticmethod
    def _remap_form_config(config: dict, col_map: dict[str, str]) -> dict:
        """Rewrite each ``fields[].column_uuid`` in a form's config through
        ``col_map`` (forms/schemas.FormConfig), for the same reason views are
        remapped. Unmapped references are left untouched."""
        cfg = deepcopy(config or {})
        for field in cfg.get("fields", []) or []:
            if isinstance(field, dict) and "column_uuid" in field:
                field["column_uuid"] = col_map.get(
                    field["column_uuid"], field["column_uuid"]
                )
        return cfg

    @classmethod
    async def clone(
        cls,
        db: AsyncSession,
        uuid: Union[str, UUID],
        *,
        with_records: bool,
    ) -> Optional[List]:
        """Duplicate a list into its own workspace, structure and (optionally) data.

        The integrity rule is that records are keyed by column **uuid**: the clone's
        columns are new rows with new uuids, so every uuid reference — the record
        ``data`` keys, ``primary_column_id``, and the column-uuid references inside
        view and form configs — is rewritten through an old→new column-uuid map. Get
        that wrong and the clone's records render blank (see docs/planned-features.md
        #5). Values inside cells are copied verbatim: ``choice`` labels and
        lookup/user/group target uuids stay valid, and a self-referencing lookup is
        deliberately left pointing at the *original* list (config and cells both), so
        no row-uuid remap is needed.

        Unlike ``create``, this does **not** call ``_provision_defaults`` — the clone
        copies the source's real columns/views/forms, so seeding defaults on top would
        duplicate them. Grants are not copied and no inherit flags are carried over:
        the clone is a fresh list the cloner owns, private like any newly created list.
        The router enforces 'view' on the source and 'configure' on its workspace.
        """
        source = await cls.get_any_by_uuid(db, uuid)
        if source is None:
            return None

        clone = List(
            workspace_id=source.workspace_id,
            name=await cls._free_copy_name(db, source.workspace_id, source.name),
            display_name=f"{source.display_name} (copy)",
            description=source.description,
            settings=deepcopy(source.settings or {}),
        )
        db.add(clone)
        await db.flush()  # assign clone.id / clone.uuid

        # Clone columns first, then flush so the new uuids exist to build the map.
        source_columns = (
            await db.execute(
                select(Column)
                .where(Column.list_id == source.id, Column.deleted_at.is_(None))
                .order_by(Column.position, Column.id)
            )
        ).scalars().all()
        pairs: list[tuple[Column, Column]] = []
        for col in source_columns:
            new_col = Column(
                list_id=clone.id,
                name=col.name,
                display_name=col.display_name,
                type=col.type,
                position=col.position,
                description=col.description,
                config=deepcopy(col.config or {}),
            )
            db.add(new_col)
            pairs.append((col, new_col))
        await db.flush()  # assign each new column's uuid

        # old column uuid (str) -> new column uuid (str), the map every reference
        # below is rewritten through. Keyed by str because record data keys and the
        # uuids stored inside view/form configs are JSON strings.
        col_map = {str(old.uuid): str(new.uuid) for old, new in pairs}

        # A nested lookup's parent is a *sibling* column, which was just cloned —
        # remap it so the clone nests under its own parent, not the original's.
        # `target_filter_column_uuid` is left alone: it names a column of the
        # target list, whose uuids are unchanged (even for a self-referencing
        # lookup, which keeps pointing at the original list — see above).
        for _, new_col in pairs:
            config = new_col.config or {}
            parent_uuid = config.get("parent_column_uuid")
            if parent_uuid is not None and str(parent_uuid) in col_map:
                config = dict(config)
                config["parent_column_uuid"] = col_map[str(parent_uuid)]
                new_col.config = config

        # Repoint the primary/display column at the cloned column.
        if source.primary_column_id is not None:
            for old, new in pairs:
                if old.id == source.primary_column_id:
                    clone.primary_column = new
                    break

        source_views = (
            await db.execute(
                select(View)
                .where(View.list_id == source.id, View.deleted_at.is_(None))
                .order_by(View.position, View.id)
            )
        ).scalars().all()
        for view in source_views:
            db.add(
                View(
                    list_id=clone.id,
                    name=view.name,
                    display_name=view.display_name,
                    position=view.position,
                    description=view.description,
                    config=cls._remap_view_config(view.config, col_map),
                )
            )

        source_forms = (
            await db.execute(
                select(Form)
                .where(Form.list_id == source.id, Form.deleted_at.is_(None))
                .order_by(Form.id)
            )
        ).scalars().all()
        for form in source_forms:
            db.add(
                Form(
                    list_id=clone.id,
                    name=form.name,
                    display_name=form.display_name,
                    type=form.type,
                    is_default=form.is_default,
                    config=cls._remap_form_config(form.config, col_map),
                )
            )

        source_metrics = (
            await db.execute(
                select(Metric)
                .where(Metric.list_id == source.id, Metric.deleted_at.is_(None))
                .order_by(Metric.position, Metric.id)
            )
        ).scalars().all()
        for metric in source_metrics:
            db.add(
                Metric(
                    list_id=clone.id,
                    name=metric.name,
                    display_name=metric.display_name,
                    position=metric.position,
                    description=metric.description,
                    # No remap, unlike a view or a form: a metric formula names
                    # its columns by *name*, and a clone keeps every name. The
                    # rows still have to be copied, or the clone loses its cards.
                    config=dict(metric.config or {}),
                )
            )

        if with_records:
            source_items = (
                await db.execute(
                    select(ListItem)
                    .where(
                        ListItem.list_id == source.id,
                        ListItem.deleted_at.is_(None),
                    )
                    .order_by(ListItem.id)
                )
            ).scalars().all()
            for item in source_items:
                data = {
                    col_map.get(key, key): value
                    for key, value in (item.data or {}).items()
                }
                db.add(ListItem(list_id=clone.id, data=data))

        try:
            await db.flush()
            new_uuid = clone.uuid
            await db.commit()
        except IntegrityError as exc:
            await db.rollback()
            raise ConflictError("A list with this name already exists") from exc

        return await cls.get_any_by_uuid(db, new_uuid)

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
        """Soft-delete every child (columns, records, views, forms, metrics) of
        the given lists in bulk. Does not touch the lists themselves and does not
        commit."""
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
        """Bring a soft-deleted list back, along with the columns, records, views,
        forms and metrics that went down with it. Returns None if it is not in
        the bin.

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
        transaction: drain the records, break the circular ``primary_column_id``
        foreign key, delete the polymorphic grants of the list and its descendants
        (nothing else does), then the views, forms, metrics, columns, and finally the
        list. Inbound references were already scrubbed when the list was soft-deleted,
        so there is nothing to clear here. Irreversible.

        The records go in batches (``drain_records``) because they are the one part of
        this a list can make arbitrarily large, but *without* committing between them:
        a list is only purgeable while it is still inside the retention window, which
        is exactly while ``restore`` still works on it. An interrupted purge here has
        to leave the list whole rather than restorable and short of records.
        """
        lst = await cls.get_deleted_by_uuid(db, uuid)
        if lst is None:
            return False

        ids = [lst.id]
        await drain_records(db, ids, commit=False)
        # Break the circular dependency (lists.primary_column_id -> columns.id) first,
        # or deleting the columns violates the foreign key.
        await db.execute(
            text("UPDATE lists SET primary_column_id = NULL WHERE id = ANY(:ids)"),
            {"ids": ids},
        )
        # The records' grants went with them above — once they are deleted nothing is
        # left to find their grants by.
        await db.execute(
            text(
                """
                DELETE FROM permission_grants
                 WHERE (resource_type = 'list' AND resource_id = ANY(:ids))
                    OR (resource_type = 'view' AND resource_id IN (SELECT id FROM views WHERE list_id = ANY(:ids)))
                    OR (resource_type = 'form' AND resource_id IN (SELECT id FROM forms WHERE list_id = ANY(:ids)))
                """
            ),
            {"ids": ids},
        )
        await db.execute(text("DELETE FROM views WHERE list_id = ANY(:ids)"), {"ids": ids})
        await db.execute(text("DELETE FROM forms WHERE list_id = ANY(:ids)"), {"ids": ids})
        # Metrics are never a grant target, so they appear here but not above.
        await db.execute(text("DELETE FROM metrics WHERE list_id = ANY(:ids)"), {"ids": ids})
        await db.execute(text("DELETE FROM columns WHERE list_id = ANY(:ids)"), {"ids": ids})
        await db.execute(text("DELETE FROM lists WHERE id = ANY(:ids)"), {"ids": ids})
        await db.commit()
        return True

    @staticmethod
    def _provision_defaults(db: AsyncSession, lst: List) -> None:
        """Seed a new list with default audit columns, a grid view and forms.

        Three forms: ``create`` and ``update`` for writing a record, and ``view`` for
        reading one. An empty ``fields`` list is the convention for "all columns", so
        the view form shows everything until someone customizes the layout; the two
        input forms instead name the audit columns to keep them off the form.
        """

        default_columns = []
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
            default_col = Column(
                uuid=uuid7(),
                list_id=lst.id,
                name=name,
                display_name=display_name,
                type=col_type,
                position=position,
                config=config,
            )
            db.add(default_col)
            default_columns.append(default_col)

        db.add(
            View(
                list_id=lst.id,
                name="grid",
                display_name="Grid",
                position=0,
                config=ViewConfig().model_dump(mode="json"),
            )
        )

        default_fields_config = [
            {
                "order": i,
                "enabled": False,
                "included": False,
                "column_uuid": str(col.uuid),
                "label_override": None,
            }
            for i, col in enumerate(default_columns)
        ]
        db.add(
            Form(
                list_id=lst.id,
                name="create",
                display_name="Create",
                type=FormType.create,
                is_default=True,
                config={"fields": default_fields_config},
            )
        )
        db.add(
            Form(
                list_id=lst.id,
                name="update",
                display_name="Update",
                type=FormType.update,
                is_default=True,
                config={"fields": default_fields_config},
            )
        )
        # The view form starts empty rather than with ``default_fields_config``:
        # create/update hide the audit columns because nobody types them, but "created
        # by" and "updated at" are exactly the detail a display-only form exists to
        # show. Empty means "all columns", which also picks up calculated ones without
        # having to name them.
        db.add(
            Form(
                list_id=lst.id,
                name="view",
                display_name="View",
                type=FormType.view,
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
