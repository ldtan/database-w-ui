from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
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
from .columns.models import Column, in_shape
# Also safe at module level, and checked: the column service's own imports reach the
# table *models* but never this module, so there is no cycle to defer around.
from .columns.services import ColumnService
from .events.broker import broker
from .forms.enums import FormType
from .forms.models import Form
from .records.models import TableRecord
from .models import Table
from .purge import drain_records
from .schemas import TableCreate, TableRead, TableUpdate
from .metrics.models import Metric
from .views.models import View
from .views.schemas import ViewConfig

# Everything scoped under a table; soft-deleted together when the table goes.
_TABLE_CHILD_MODELS = (Column, TableRecord, View, Form, Metric)

# Source records copied per statement by `clone`. What this bounds is the *statement* —
# its duration, and the size of the two array parameters — not the total, since the
# batches run one after another. Same distinction columns/backfill.py draws about its
# own CHUNK.
CLONE_CHUNK = 1_000

# The record copy, performed entirely inside Postgres: the rows never become Python
# objects, so cloning a large table costs the app neither the memory to hold it nor the
# CPU to decode and re-encode every cell. `COALESCE(map ->> key, key)` is the SQL
# spelling of `col_map.get(key, key)` — a data key the map does not mention (a leftover
# from a deleted column, or a hidden one left by the old staged conversion, which
# `in_shape` keeps out of the clone's schema) is carried across untouched, as it always
# has been.
#
# The outer COALESCE is for a record whose `data` is `{}`: `jsonb_object_agg` over no
# rows answers NULL, and the column is not nullable.
#
# uuids are minted per batch in Python and joined by position rather than generated in
# SQL, because there is no uuid7 to generate them with — the same call migration
# d2f6a8c41b93 made, for the same reason. `ORDER BY src.id` keeps the clone's rows in
# the source's order, so their ids and their uuids agree about it.
#
# `archived_at` is carried across rather than cleared. A clone with records is asked
# for to get the data, and dropping the archived rows — or bringing them back live —
# would both be the clone quietly disagreeing with the source about what it holds.
_COPY_RECORDS = text("""
    INSERT INTO table_records (uuid, table_id, data, permissions_inherit, archived_at, created_at, updated_at)
    SELECT b.new_uuid,
           :clone_id,
           COALESCE(
               (SELECT jsonb_object_agg(
                           COALESCE(CAST(:col_map AS jsonb) ->> kv.key, kv.key), kv.value)
                  FROM jsonb_each(src.data) AS kv),
               '{}'::jsonb
           ),
           true,
           src.archived_at,
           :now, :now
      FROM table_records AS src
      JOIN unnest(CAST(:src_ids AS bigint[]), CAST(:new_uuids AS uuid[]))
             AS b(src_id, new_uuid) ON b.src_id = src.id
     ORDER BY src.id
""")


class TableService:
    @staticmethod
    def _select(owner_id: int):
        # A table is owned transitively through its workspace.
        return (
            select(Table)
            .join(Workspace, Table.workspace_id == Workspace.id)
            .where(
                Table.deleted_at.is_(None),
                Workspace.deleted_at.is_(None),
                Workspace.owner_id == owner_id,
            )
            .options(selectinload(Table.workspace), selectinload(Table.primary_column))
        )

    @classmethod
    async def get_by_uuid(
        cls, db: AsyncSession, owner_id: int, uuid: Union[str, UUID]
    ) -> Optional[Table]:
        parsed = coerce_uuid(uuid)
        if parsed is None:
            return None
        result = await db.execute(cls._select(owner_id).where(Table.uuid == parsed))
        return result.scalars().first()

    @classmethod
    async def get_any_by_uuid(
        cls, db: AsyncSession, uuid: Union[str, UUID]
    ) -> Optional[Table]:
        """Resolve a live table by uuid regardless of owner. Callers enforce
        permissions separately (see src/permissions)."""
        parsed = coerce_uuid(uuid)
        if parsed is None:
            return None
        result = await db.execute(
            select(Table)
            .where(Table.uuid == parsed, Table.deleted_at.is_(None))
            .options(selectinload(Table.workspace), selectinload(Table.primary_column))
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
        """The WHERE conditions for a visible-tables query, shared by ``list`` and
        ``count`` so the page and its total can never describe different queries.
        Assumes the statement already joins ``Workspace``."""
        from src.permissions.services import PermissionService

        conds: list[ColumnElement[bool]] = [
            Table.deleted_at.is_(None),
            Workspace.deleted_at.is_(None),
            await PermissionService.table_query_filter(db, user, action),
        ]
        if workspace_uuid is not None:
            conds.append(Workspace.uuid == coerce_uuid(workspace_uuid))
        if shared_only:
            conds.append(Workspace.owner_id != user.id)
        if workspace_hidden is not None:
            # Whether the caller can open this table's *parent workspace* — a
            # different question from whether they own it, and the one that decides
            # if a shared table has a home in the nav tree or hangs off nothing.
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
                    Table.display_name.ilike(pattern, escape=LIKE_ESCAPE),
                    Table.name.ilike(pattern, escape=LIKE_ESCAPE),
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
    ) -> Sequence[TableRead]:
        """Tables the user can view (owned, directly granted, or inherited from a
        workspace grant), optionally scoped to one workspace or filtered by ``q``.

        ``shared_only`` keeps just the tables the user does not own — every table
        reachable through someone else's grant, whether that grant sits on the table
        or on its workspace. It is the ownership axis, and the lookup target picker
        wants exactly it: any table you do not own is a legitimate lookup target.

        ``workspace_hidden`` is the *reachability* axis, and the two come apart. A
        table granted without its workspace sits inside a workspace the user cannot
        open, so it hangs off nothing in the nav tree — passing True keeps just
        those, which is what the "Shared with me" section needs. Tables that came in
        through a workspace grant are already reachable by browsing that workspace,
        and listing them again there would show them twice.

        ``lookup_targets`` widens the query from ``view`` to ``lookup``, adding the
        tables shared *only* as lookup targets. It exists for one caller — the target
        picker in the column dialog — because a table granted `lookup` is deliberately
        absent everywhere else, and a target you cannot name is a target you cannot
        point a column at. It names tables the caller cannot open; that is the minimum
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
            select(Table)
            .join(Workspace, Table.workspace_id == Workspace.id)
            .where(*conds)
            .options(
                selectinload(Table.workspace).selectinload(Workspace.owner),
                selectinload(Table.primary_column),
            )
            .order_by(Table.id)
            .offset(skip)
            .limit(limit)
        )
        tables = (await db.execute(stmt)).scalars().all()

        # Which of these workspaces the caller may actually view — the gate on
        # exposing a workspace's name (see the docstring and known-gaps).
        ws_ids = {table.workspace_id for table in tables}
        viewable: set[int] = set()
        if ws_ids:
            ws_filter = await PermissionService.workspace_view_filter(db, user)
            rows = await db.execute(
                select(Workspace.id).where(Workspace.id.in_(ws_ids), ws_filter)
            )
            viewable = set(rows.scalars().all())

        reads: list[TableRead] = []
        for table in tables:
            read = TableRead.model_validate(table)
            read.owner_username = table.workspace.owner.username
            read.workspace_display_name = (
                table.workspace.display_name if table.workspace_id in viewable else None
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
        """Total matching tables, ignoring skip/limit — what the paginator needs."""
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
            select(func.count(func.distinct(Table.id)))
            .select_from(Table)
            .join(Workspace, Table.workspace_id == Workspace.id)
            .where(*conds)
        )
        return await db.scalar(stmt) or 0

    @classmethod
    async def create(cls, db: AsyncSession, payload: TableCreate) -> Table:
        # Owner-agnostic: the router enforces 'configure' on the target workspace.
        workspace = await WorkspaceService.get_any_by_uuid(db, payload.workspace_uuid)
        if workspace is None:
            raise UnknownReferenceError(
                "workspace_uuid does not reference a workspace"
            )

        table = Table(
            workspace_id=workspace.id,
            name=payload.name,
            display_name=payload.display_name,
            description=payload.description,
            settings=payload.settings or {},
        )
        db.add(table)
        try:
            await db.flush()
            cls._provision_defaults(db, table)
            new_uuid = table.uuid
            await db.commit()
        except IntegrityError as exc:
            await db.rollback()
            raise ConflictError("A table with this name already exists") from exc

        return await cls.get_any_by_uuid(db, new_uuid)

    @classmethod
    async def update(
        cls,
        db: AsyncSession,
        uuid: Union[str, UUID],
        payload: TableUpdate,
    ) -> Optional[Table]:
        # Owner-agnostic: the router enforces 'configure' (and 'share' when
        # permissions_inherit is being changed) before calling this.
        table = await cls.get_any_by_uuid(db, uuid)
        if table is None:
            return None

        data = payload.model_dump(exclude_unset=True)

        if "primary_column_uuid" in data:
            column_uuid = data.pop("primary_column_uuid")
            table.primary_column_id = await cls._resolve_primary_column_id(
                db, table.id, column_uuid
            )

        if "workspace_uuid" in data:
            target_uuid = data.pop("workspace_uuid")
            if target_uuid is not None:
                workspace = await WorkspaceService.get_any_by_uuid(db, target_uuid)
                if workspace is None:
                    raise UnknownReferenceError(
                        "workspace_uuid does not reference a workspace"
                    )
                # Assign the relationship, not the scalar FK: Table.workspace is
                # eager-loaded, and a loaded many-to-one overwrites a bare
                # workspace_id change on flush, leaving the table in place.
                table.workspace = workspace

        if "settings" in data:
            # **Merged, not replaced.** `settings` is a shared bag of misc table-level
            # settings, so a `setattr` here — which is what this used to be — means the
            # first writer of one key silently drops every other. Nothing wrote it at all
            # until `settings.qr` (the QR payload's column selection), so this is the
            # moment to fix it rather than the moment after something is lost: the rule
            # `UserSettingsUpdate` already states, that only the keys sent are touched and
            # a client that has never heard of a key cannot drop it.
            #
            # Shallow, at the top level: sending `qr` replaces the whole `qr` object rather
            # than merging into it. That is deliberate — `columns` and `include_uuid` are
            # one answer to "what is on the badge", and a half-sent one has no meaning.
            #
            # A new dict, not a mutation, so SQLAlchemy sees the JSONB change (the same
            # reason `TableRecordService.update` reassigns `record.data`).
            data["settings"] = {**(table.settings or {}), **(data["settings"] or {})}

        # Treat an explicit null name as "leave unchanged" rather than clearing it.
        if data.get("name") is None:
            data.pop("name", None)
        if data.get("display_name") is None:
            data.pop("display_name", None)
        if data.get("permissions_inherit") is None:
            data.pop("permissions_inherit", None)

        for field, value in data.items():
            setattr(table, field, value)

        try:
            await db.flush()
            target_uuid = table.uuid
            await db.commit()
        except IntegrityError as exc:
            await db.rollback()
            raise ConflictError("A table with this name already exists") from exc

        return await cls.get_any_by_uuid(db, target_uuid)

    @staticmethod
    async def _free_name_in_workspace(
        db: AsyncSession, workspace_id: int, base: str, exclude_id: int
    ) -> str:
        """A table ``name`` unique among the live tables of ``workspace_id``.

        Returns ``base`` when it is free, else ``base_2``, ``base_3``, … — used when
        a transfer lands a table in an inbox that already holds one of that name, so a
        transfer never fails on a per-workspace name clash. The suffix keeps the
        NameSlug shape (lowercase letters/digits/underscores)."""
        rows = await db.execute(
            select(Table.name).where(
                Table.workspace_id == workspace_id,
                Table.deleted_at.is_(None),
                Table.id != exclude_id,
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
    ) -> Optional[Table]:
        """Hand a table to another user by moving it into their "Incoming" workspace.

        Ownership here *is* workspace membership, so a transfer is a move into a
        workspace the recipient owns — their immutable inbox, minted on demand. The
        router enforces that the caller is the table's current owner before calling.

        Grants are deliberately left untouched: a grant points at the table by
        ``resource_id``, which does not change, so existing shares survive the move.
        The giver's access was the implicit workspace-owner short-circuit, and it ends
        the moment the table leaves their workspace — no grant to revoke. The recipient
        becomes the new implicit owner. (See docs/planned-features.md #3.)
        """
        table = await cls.get_any_by_uuid(db, uuid)
        if table is None:
            return None

        inbox = await WorkspaceService.get_or_create_inbox(db, recipient_id)
        # A table name is unique per workspace; auto-suffix so the landing never 409s.
        table.name = await cls._free_name_in_workspace(
            db, inbox.id, table.name, exclude_id=table.id
        )
        # Assign the relationship, not the scalar FK: get_any_by_uuid eager-loads
        # Table.workspace, and a loaded many-to-one overwrites a bare workspace_id
        # change on flush — so setting the FK alone silently leaves the table put.
        table.workspace = inbox

        await db.flush()
        target_uuid = table.uuid
        await db.commit()
        return await cls.get_any_by_uuid(db, target_uuid)

    @staticmethod
    async def _free_copy_name(
        db: AsyncSession, workspace_id: int, base: str
    ) -> str:
        """A free table ``name`` for a clone in ``workspace_id``: ``base_copy``, then
        ``base_copy_2``, ``base_copy_3``, … The suffix keeps the NameSlug shape
        (lowercase letters/digits/underscores) and the name unique per workspace."""
        rows = await db.execute(
            select(Table.name).where(
                Table.workspace_id == workspace_id, Table.deleted_at.is_(None)
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
        """Rewrite every column reference in a form's config through ``col_map``
        (forms/schemas.FormConfig), for the same reason views are remapped. Unmapped
        references are left untouched.

        Two references per field, not one: the field's own column, and the columns its
        ``condition`` compares against — the same conditions a view's ``filters`` holds,
        and remapped here exactly as ``_remap_view_config`` remaps those. Missed, a
        cloned form's conditions quietly go on testing the *source* table's columns, so
        the fields they govern show or hide according to a table nobody is looking at.
        """
        cfg = deepcopy(config or {})

        def remap(value):
            return col_map.get(value, value)

        for field in cfg.get("fields", []) or []:
            if not isinstance(field, dict):
                continue
            if "column_uuid" in field:
                field["column_uuid"] = remap(field["column_uuid"])
            condition = field.get("condition")
            if not isinstance(condition, dict):
                continue
            when = condition.get("when")
            if not isinstance(when, dict):
                continue
            for cond in when.get("conditions", []) or []:
                if isinstance(cond, dict) and "column_uuid" in cond:
                    cond["column_uuid"] = remap(cond["column_uuid"])
        return cfg

    @classmethod
    async def clone(
        cls,
        db: AsyncSession,
        uuid: Union[str, UUID],
        *,
        with_records: bool,
        user,
    ) -> Optional[Table]:
        """Duplicate a table into its own workspace, structure and (optionally) data.

        The integrity rule is that records are keyed by column **uuid**: the clone's
        columns are new rows with new uuids, so every uuid reference — the record
        ``data`` keys, ``primary_column_id``, and the column-uuid references inside
        view and form configs — is rewritten through an old→new column-uuid map. Get
        that wrong and the clone's records render blank (see docs/planned-features.md
        #5). Values inside cells are copied verbatim: ``choice`` labels and
        lookup/user/group target uuids stay valid, and a self-referencing lookup is
        deliberately left pointing at the *original* table (config and cells both), so
        no row-uuid remap is needed.

        Unlike ``create``, this does **not** call ``_provision_defaults`` — the clone
        copies the source's real columns/views/forms, so seeding defaults on top would
        duplicate them. Grants are not copied and no inherit flags are carried over:
        the clone is a fresh table the cloner owns, private like any newly created table.
        The router enforces 'view' on the source and 'configure' on its workspace.

        ``user`` is the caller, and copying records needs it: a clone is a *read* of
        the source's records, so it honours the same per-record visibility the grid
        and the export do. Table-level 'view' is not sufficient — a record whose
        ``permissions_inherit`` is false and which was never granted to the caller
        must not arrive in a table they own. This is the same rule, and the same
        reason, as ``export_rows``.
        """
        source = await cls.get_any_by_uuid(db, uuid)
        if source is None:
            return None

        clone = Table(
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
                # `in_shape`: a conversion in flight on the source is not part of
                # what the table *is*, so a clone does not inherit half of it.
                .where(Column.table_id == source.id, in_shape())
                .order_by(Column.position, Column.id)
            )
        ).scalars().all()
        pairs: list[tuple[Column, Column]] = []
        for col in source_columns:
            new_col = Column(
                table_id=clone.id,
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
        # target table, whose uuids are unchanged (even for a self-referencing
        # lookup, which keeps pointing at the original table — see above).
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
                .where(View.table_id == source.id, View.deleted_at.is_(None))
                .order_by(View.position, View.id)
            )
        ).scalars().all()
        for view in source_views:
            db.add(
                View(
                    table_id=clone.id,
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
                .where(Form.table_id == source.id, Form.deleted_at.is_(None))
                .order_by(Form.id)
            )
        ).scalars().all()
        for form in source_forms:
            db.add(
                Form(
                    table_id=clone.id,
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
                .where(Metric.table_id == source.id, Metric.deleted_at.is_(None))
                .order_by(Metric.position, Metric.id)
            )
        ).scalars().all()
        for metric in source_metrics:
            db.add(
                Metric(
                    table_id=clone.id,
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
            from src.permissions.services import PermissionService

            # A clone copies records, so it is a read of them and takes the same
            # per-record filter every other read applies. Without it, table-level
            # 'view' plus workspace 'configure' — which the router already requires,
            # and which `configure` confers `view` for — would be a way to lift rows
            # whose inheritance was broken and which were never granted to the
            # caller, into a table where they inherit from a table the caller owns.
            # `_COPY_RECORDS` writes `permissions_inherit = true`, which is correct
            # *because* of this filter: everything copied was visible to the cloner,
            # and the clone itself starts private with no grants.
            visible = await PermissionService.record_view_filter(db, user, source)

            # Keyset-paged, not loaded whole: the source table is the one thing here
            # whose size a caller controls, and holding all of it — twice, once as the
            # source instances and once as the copies — is how a single clone request
            # takes the process down. Only the ids come back to Python; the cells are
            # copied by `_COPY_RECORDS` without ever leaving the database.
            #
            # Deliberately *not* committed per batch, unlike the maintenance jobs that
            # share this shape. They can be interrupted because their half-done state is
            # a state the data was already in; a half-populated clone is not. Keeping
            # the batches inside the caller's transaction is what leaves a failed clone
            # with nothing behind, and keeps the IntegrityError below meaningful.
            after = 0
            while True:
                src_ids = (
                    await db.execute(
                        select(TableRecord.id)
                        # Archived rows are copied too — see `_COPY_RECORDS`, which
                        # carries `archived_at` over so they arrive archived.
                        .where(
                            TableRecord.table_id == source.id,
                            TableRecord.deleted_at.is_(None),
                            TableRecord.id > after,
                            visible,
                        )
                        .order_by(TableRecord.id)
                        .limit(CLONE_CHUNK)
                    )
                ).scalars().all()
                if not src_ids:
                    break
                after = src_ids[-1]
                # One timestamp for the batch rather than one per row: the records were
                # created by a single act, and staggering them by microseconds would
                # claim otherwise.
                await db.execute(
                    _COPY_RECORDS,
                    {
                        "clone_id": clone.id,
                        "col_map": json.dumps(col_map),
                        "src_ids": list(src_ids),
                        "new_uuids": [uuid7() for _ in src_ids],
                        "now": datetime.now(timezone.utc),
                    },
                )

        try:
            await db.flush()
            new_uuid = clone.uuid
            await db.commit()
        except IntegrityError as exc:
            await db.rollback()
            raise ConflictError("A table with this name already exists") from exc

        return await cls.get_any_by_uuid(db, new_uuid)

    @classmethod
    async def delete(cls, db: AsyncSession, uuid: Union[str, UUID]) -> bool:
        # Owner-agnostic: the router enforces 'delete' before calling this.
        table = await cls.get_any_by_uuid(db, uuid)
        if table is None:
            return False

        now = datetime.now(timezone.utc)
        await cls._cascade_descendants(db, [table.id], now)
        table.deleted_at = now
        # Deleting a *table* is the exception to "soft delete destroys nothing", and
        # deliberately so. Deleting a row or an account leaves the cells naming it
        # intact, because they can still be resolved and restored. A deleted table
        # cannot: every value in a lookup column pointing at it is stale at once, and
        # the permission resolver returns *no access* for a deleted resource, so those
        # labels could not even be authorised.
        #
        # The old justification — that restoring a table would mean un-cascading its
        # children, a feature that did not exist — has expired: `restore` below does
        # exactly that. What keeps this a scrub is the asymmetry it cannot fix. A
        # restore brings back the table's own contents and cannot bring back what was
        # cleared on *other* tables' records, because nothing recorded which cells those
        # were. Since the values are unrecoverable either way, the referencing column's
        # config goes with them rather than being left pointing at a table that may
        # never come back (see `_scrub_references_to_tables`).
        scrubbed = await cls._scrub_references_to_tables(db, [table.uuid])
        await db.commit()
        # These are the tables whose *cells* the scrub emptied — other people's, which
        # are still live and may well be on a screen. Somebody with the deleted table
        # itself open is not signalled and does not need to be: their stream ends at
        # its own deadline and the reconnect 404s, which is the table going away rather
        # than its rows changing. (A self-referencing lookup puts that table in this set
        # anyway; notifying a table nobody can still watch is a no-op.)
        for table_id in scrubbed:
            broker.notify(table_id)
        return True

    @classmethod
    async def _cascade_descendants(
        cls, db: AsyncSession, table_ids: Sequence[int], when: datetime
    ) -> None:
        """Soft-delete every child (columns, records, views, forms, metrics) of
        the given tables in bulk. Does not touch the tables themselves and does not
        commit."""
        if not table_ids:
            return
        for model in _TABLE_CHILD_MODELS:
            await db.execute(
                update(model)
                .where(model.table_id.in_(table_ids), model.deleted_at.is_(None))
                .values(deleted_at=when)
            )

    @classmethod
    async def soft_delete_for_workspace(
        cls, db: AsyncSession, workspace_id: int, when: datetime
    ) -> set[int]:
        """Soft-delete all tables in a workspace and their descendants, in bulk.
        Does not commit — the caller (workspace delete) owns the transaction, and
        owns telling `_scrub_references_to_tables`' returned tables to look again once
        it has."""
        result = await db.execute(
            select(Table.id, Table.uuid).where(
                Table.workspace_id == workspace_id, Table.deleted_at.is_(None)
            )
        )
        rows = result.all()
        if not rows:
            return set()
        table_ids = [row.id for row in rows]
        table_uuids = [row.uuid for row in rows]
        await cls._cascade_descendants(db, table_ids, when)
        await db.execute(
            update(Table).where(Table.id.in_(table_ids)).values(deleted_at=when)
        )
        return await cls._scrub_references_to_tables(db, table_uuids)

    @classmethod
    async def _scrub_references_to_tables(
        cls, db: AsyncSession, table_uuids: Sequence[UUID]
    ) -> set[int]:
        """Remove dangling lookup references to a now-deleted table — values *and* config.

        Returns the ids of the tables whose **records** this touched, which is never the
        table being deleted and is often somebody else's. They are handed back rather
        than signalled from here because this does not own the transaction — both
        callers commit after it, and a live event sent before a commit tells watchers to
        re-read rows that have not changed yet.

        Lookup values are raw uuids stored in another row's JSONB, so deleting a
        target table leaves those pointers unresolvable but present. Since a lookup
        column targets exactly one table, deleting that table makes *every* value in
        the column stale — so drop the column's key from the referencing rows.
        Only columns on surviving tables matter (those on deleted tables are already
        soft-deleted and thus excluded) — which makes the callers' ordering
        load-bearing now that config goes too. `_cascade_descendants` runs *first*,
        so a lookup on a table being deleted in the same breath — one pointing at
        itself, or at a sibling in a workspace going down together — is out of scope
        here and comes back from the bin still configured. Scrubbing before the
        cascade would blank it instead, and nothing records what it had pointed at.

        **The column's own config is cleared with them, and for a long time it was
        not.** Emptying the cells while leaving `target_table_uuid` naming a table that
        no longer exists produced a column nothing could use and nothing could fix:
        `lookup_options` raised, so the picker and every label resolution 404'd, and
        `_validate_config` refused any PATCH that resent the config — which the column
        dialog always does — so the only way out was to retarget in the same edit or
        delete the column. The values were already gone by then, so there was nothing
        left for the config to be pointing *at*; keeping it only preserved the
        breakage. Cleared, the column degrades to an unconfigured lookup, which every
        surface already knows how to render and which can simply be given a new
        target.

        `display_column_uuid`, `target_filter_column_uuid` and `option_filters` go
        too: each names a column *of the dead target*, so each is as meaningless as
        the target itself. `parent_column_uuid` names a sibling on this column's
        **own** table, which is still very much alive — it goes because the pairing is
        set and unset as a unit and its other half has just been blanked, not because
        what it points at is gone.

        `required` goes as well; see the comment at the assignment for why a
        constraint that cannot be satisfied is worse than no constraint.

        Then `ColumnService._clear_dependent_nesting` runs on each column cleared,
        which is a different direction to the clearing above: it walks *outward*, to
        the columns that paired against this one and are not themselves in scope here
        because their own targets survive.

        One asymmetry to know: a table restore no longer silently re-points these
        columns. It never restored anything usable — the cells were scrubbed at delete
        time and the restore cannot bring them back (known-gaps gap 6, "What is still
        missing", which now records this) — so what is lost is a column that pointed
        at a live table and held nothing, plus a `required` tick and a nesting pairing
        that have to be set again by hand.
        """
        if not table_uuids:
            return set()
        targets = [str(u) for u in table_uuids]
        lookup_columns = (
            await db.execute(
                select(Column).where(
                    Column.type == ColumnType.lookup,
                    Column.deleted_at.is_(None),
                    Column.config["target_table_uuid"].astext.in_(targets),
                )
            )
        ).scalars().all()
        for column in lookup_columns:
            key = str(column.uuid)
            await db.execute(
                update(TableRecord)
                # Archived rows are scrubbed too, deliberately: the target is being
                # destroyed, so a pointer left on an archived row would be dangling
                # the moment anyone unarchived it.
                .where(
                    TableRecord.table_id == column.table_id,
                    TableRecord.deleted_at.is_(None),
                    TableRecord.data.has_key(key),
                )
                .values(data=TableRecord.data.op("-")(key))
            )
            # Reassigned rather than mutated in place, so SQLAlchemy sees the JSONB
            # change — the same rule `_clear_dependent_nesting` follows.
            config = dict(column.config or {})
            for field in (
                "target_table_uuid",
                "display_column_uuid",
                "parent_column_uuid",
                "target_filter_column_uuid",
                "option_filters",
            ):
                config[field] = None
            # `required` goes too — as `False` rather than `None`, because the schema
            # types it as a plain bool. A required cell that cannot be filled is not
            # a constraint, it is a lock: `_validate_data` enforces `required` over
            # the *merged* record on every create and every edit, and the cell has
            # just been emptied and cannot be refilled, so one deleted table would
            # freeze writes across every table that referred to it. Nor could the
            # setting be turned off on its own — a config PATCH merges onto what is
            # stored, so `target_table_uuid: None` comes back and `_validate_config`
            # refuses it. Dropping it follows the rule the `unique` + `multiple`
            # refusal set: a column must not claim a guarantee it is in no position
            # to enforce. Re-ticking it is part of retargeting.
            #
            # Borrow that rule's *principle* and not its mechanism, which is the
            # opposite one: `unique` + `multiple` refuses the next save and leaves
            # existing columns alone precisely because there is no retro-validation.
            # This rewrites a stored config instead, and it is the only place that
            # does. What earns the difference is that the refusal there is something
            # the column's owner sees and can act on, where this freeze is triggered
            # by somebody else deleting a different table — possibly in a workspace
            # they own and this owner cannot see — and refuses writes for a reason
            # nobody on this table can do anything about. Nothing announces the
            # dropped constraint, which is the cost, and it is smaller than a table
            # nobody can write to.
            config["required"] = False
            column.config = config
            # Un-nest whatever paired against this column. A nested child names its
            # parent — a sibling on its *own* table — and a filter column on its own
            # target table, and either of those can be a column just blanked here
            # while the child survives untouched, because the child's own target is
            # still alive. Left alone the child keeps a pairing whose halves resolve
            # to nothing: the picker offers no rows, `_enforce_nested_lookups`
            # refuses every selection as not belonging to the parent, and
            # `_validate_nesting` refuses to save the column at all until someone
            # clears "Nest under" by hand.
            await ColumnService._clear_dependent_nesting(db, column)
        return {column.table_id for column in lookup_columns}

    # -- the bin: view, restore, and purge soft-deleted tables ---------------

    @staticmethod
    def _select_deleted(workspace_id: int):
        """Tables in a workspace's bin: soft-deleted and still inside the retention
        window. Older ones are past ``PURGE_DELETED_AFTER_DAYS`` and on their way out,
        so they are no longer offered as restorable — exactly as the records bin does.

        Scoped to a live workspace, so these are tables deleted on their own; a table
        that went down with its workspace lives in that workspace's own bin instead.
        """
        cutoff = datetime.now(timezone.utc) - timedelta(
            days=settings.PURGE_DELETED_AFTER_DAYS
        )
        return select(Table).where(
            Table.workspace_id == workspace_id,
            Table.deleted_at.is_not(None),
            Table.deleted_at >= cutoff,
        )

    @classmethod
    async def list_deleted_for_workspace(
        cls, db: AsyncSession, workspace_id: int, skip: int = 0, limit: int = 100
    ) -> Sequence[Table]:
        """A workspace's table bin, most recently deleted first."""
        stmt = (
            cls._select_deleted(workspace_id)
            .options(
                selectinload(Table.workspace), selectinload(Table.primary_column)
            )
            .order_by(Table.deleted_at.desc(), Table.id.desc())
            .offset(skip)
            .limit(limit)
        )
        return (await db.execute(stmt)).scalars().all()

    @classmethod
    async def count_deleted_for_workspace(
        cls, db: AsyncSession, workspace_id: int
    ) -> int:
        """How many tables are in the workspace's bin — the paginator's total, built
        off the same predicate as the listing so the two cannot drift."""
        stmt = select(func.count()).select_from(
            cls._select_deleted(workspace_id).subquery()
        )
        return await db.scalar(stmt) or 0

    @classmethod
    async def get_deleted_by_uuid(
        cls, db: AsyncSession, uuid: Union[str, UUID]
    ) -> Optional[Table]:
        """A single table from the bin, regardless of owner — the router enforces the
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
            select(Table)
            .where(
                Table.uuid == parsed,
                Table.deleted_at.is_not(None),
                Table.deleted_at >= cutoff,
            )
            .options(
                selectinload(Table.workspace), selectinload(Table.primary_column)
            )
        )
        return result.scalars().first()

    @classmethod
    async def restore(
        cls, db: AsyncSession, uuid: Union[str, UUID]
    ) -> Optional[Table]:
        """Bring a soft-deleted table back, along with the columns, records, views,
        forms and metrics that went down with it. Returns None if it is not in
        the bin.

        Delete stamped the table and every descendant with one identical ``deleted_at``
        (see ``_cascade_descendants``); restore reverses exactly that set by matching
        it, so a record the user had binned on its own — a different timestamp — stays
        in the records bin.

        A table name is unique per workspace among live rows, so restoring can collide
        with a name taken while this one sat deleted and raise ``ConflictError`` (409).

        Note: references *into* this table were scrubbed when it was deleted (see
        ``delete``), so cross-table lookups pointing at its rows are not brought back —
        the table, its columns, and its records are. That scrub also cleared those
        columns' *config* — target, display column, nesting pairing, option filters
        and `required` — so what comes back here is a live table that nothing points
        at any more. Retargeting is an ordinary PATCH, but somebody has to do it.
        """
        table = await cls.get_deleted_by_uuid(db, uuid)
        if table is None:
            return None

        when = table.deleted_at
        for model in _TABLE_CHILD_MODELS:
            await db.execute(
                update(model)
                .where(model.table_id == table.id, model.deleted_at == when)
                .values(deleted_at=None)
            )
        table.deleted_at = None
        try:
            await db.commit()
        except IntegrityError as exc:
            await db.rollback()
            raise ConflictError("A table with this name already exists") from exc
        return await cls.get_any_by_uuid(db, uuid)

    @classmethod
    async def restore_for_workspace(
        cls, db: AsyncSession, workspace_id: int, when: datetime
    ) -> None:
        """Un-delete the tables (and their descendants) a workspace took down when it
        was deleted — those stamped with the workspace's own ``deleted_at``. Tables the
        owner had binned independently carry a different timestamp and are left alone.
        Does not commit — the caller (workspace restore) owns the transaction."""
        rows = await db.execute(
            select(Table.id).where(
                Table.workspace_id == workspace_id, Table.deleted_at == when
            )
        )
        table_ids = list(rows.scalars().all())
        if not table_ids:
            return
        for model in _TABLE_CHILD_MODELS:
            await db.execute(
                update(model)
                .where(model.table_id.in_(table_ids), model.deleted_at == when)
                .values(deleted_at=None)
            )
        await db.execute(
            update(Table).where(Table.id.in_(table_ids)).values(deleted_at=None)
        )

    @classmethod
    async def purge(cls, db: AsyncSession, uuid: Union[str, UUID]) -> bool:
        """Permanently destroy a single soft-deleted table and everything under it.
        Returns False if it is not in the bin.

        Mirrors the table purge job's cascade, but for one table in a single
        transaction: drain the records, break the circular ``primary_column_id``
        foreign key, delete the polymorphic grants of the table and its descendants
        (nothing else does), then the views, forms, metrics, columns, and finally the
        table. Inbound references were already scrubbed when the table was soft-deleted,
        so there is nothing to clear here. Irreversible.

        The records go in batches (``drain_records``) because they are the one part of
        this a table can make arbitrarily large, but *without* committing between them:
        a table is only purgeable while it is still inside the retention window, which
        is exactly while ``restore`` still works on it. An interrupted purge here has
        to leave the table whole rather than restorable and short of records.
        """
        table = await cls.get_deleted_by_uuid(db, uuid)
        if table is None:
            return False

        ids = [table.id]
        await drain_records(db, ids, commit=False)
        # Break the circular dependency (tables.primary_column_id -> columns.id) first,
        # or deleting the columns violates the foreign key.
        await db.execute(
            text("UPDATE tables SET primary_column_id = NULL WHERE id = ANY(:ids)"),
            {"ids": ids},
        )
        # The records' grants went with them above — once they are deleted nothing is
        # left to find their grants by.
        await db.execute(
            text(
                """
                DELETE FROM permission_grants
                 WHERE (resource_type = 'table' AND resource_id = ANY(:ids))
                    OR (resource_type = 'view' AND resource_id IN (SELECT id FROM views WHERE table_id = ANY(:ids)))
                    OR (resource_type = 'form' AND resource_id IN (SELECT id FROM forms WHERE table_id = ANY(:ids)))
                """
            ),
            {"ids": ids},
        )
        await db.execute(text("DELETE FROM views WHERE table_id = ANY(:ids)"), {"ids": ids})
        await db.execute(text("DELETE FROM forms WHERE table_id = ANY(:ids)"), {"ids": ids})
        # Metrics are never a grant target, so they appear here but not above.
        await db.execute(text("DELETE FROM metrics WHERE table_id = ANY(:ids)"), {"ids": ids})
        await db.execute(text("DELETE FROM columns WHERE table_id = ANY(:ids)"), {"ids": ids})
        await db.execute(text("DELETE FROM tables WHERE id = ANY(:ids)"), {"ids": ids})
        await db.commit()
        return True

    @staticmethod
    def _provision_defaults(db: AsyncSession, table: Table) -> None:
        """Seed a new table with default audit columns, a grid view and forms.

        Three forms: ``create`` and ``update`` for writing a record, and ``view`` for
        reading one. An empty ``fields`` table is the convention for "all columns", so
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
                table_id=table.id,
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
                table_id=table.id,
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
                table_id=table.id,
                name="create",
                display_name="Create",
                type=FormType.create,
                is_default=True,
                config={"fields": default_fields_config},
            )
        )
        db.add(
            Form(
                table_id=table.id,
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
                table_id=table.id,
                name="view",
                display_name="View",
                type=FormType.view,
                is_default=True,
                config={"fields": []},
            )
        )

    @staticmethod
    async def _resolve_primary_column_id(
        db: AsyncSession, table_id: int, column_uuid: Optional[UUID]
    ) -> Optional[int]:
        if column_uuid is None:
            return None
        result = await db.execute(
            select(Column.id).where(
                Column.uuid == column_uuid,
                Column.table_id == table_id,
                # A hidden column is not a field of the table, so it cannot be chosen
                # as the one that labels a row.
                in_shape(),
            )
        )
        column_id = result.scalars().first()
        if column_id is None:
            raise UnknownReferenceError(
                "primary_column_uuid does not reference a column of this table"
            )
        return column_id
