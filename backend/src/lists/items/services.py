from datetime import datetime, timedelta, timezone
from typing import Optional, Sequence, Union
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy import func, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy.orm.attributes import set_committed_value

from src.core.config import settings
from src.core.exceptions import ConflictError, UnknownReferenceError, ValidationError
from src.core.utils import LIKE_ESCAPE, coerce_uuid, like_pattern
from src.workspaces.models import Workspace

from src.auth.users.models import User, UserGroup

from ..columns.enums import (
    COMPUTED_TYPES,
    PRINCIPAL_TYPES,
    REFERENCE_TYPES,
    ColumnType,
)
from ..columns.formula import evaluate, parse
from ..columns.models import Column
from ..columns.validation import resolve_dynamic_default, validate_scalar
from ..models import List
from ..refs import get_by_ref
from ..views.models import View
from .filtering import apply_filters, apply_sorts
from .models import ListItem
from .schemas import ListItemCreate, ListItemUpdate

# Namespace for the per-list advisory lock that serializes unique-value writes.
_UNIQUE_LOCK_NS = 0x4C495354  # "LIST"
# How many principals/rows a search resolves a label to before it stops widening.
# A search matching thousands of target rows is not a search anyone meant to run.
_SEARCH_REF_MAX = 200


async def display_column_key(
    db: AsyncSession, target_list_id: int, config: dict
) -> Optional[str]:
    """Which column of a lookup's target list supplies a row's label.

    The lookup's configured `display_column_uuid`, else the target list's primary
    column, else its **first** column. That last fallback is load-bearing, not
    politeness: `primary_column_id` is only ever set by an explicit PATCH, and is
    nulled again when that column is deleted, so most lists have none. Without it
    a lookup at such a list has no label at all — every cell renders as a row stub,
    the picker's search matches nothing, and the export writes uuids.

    Shared by the item service (resolving a label on write), the export formatter
    (writing one out) and the options endpoint (the picker), so the three cannot
    disagree about what a label is — if they did, a file this app exported would
    not import back into it.
    """
    explicit = config.get("display_column_uuid")
    if explicit:
        return str(explicit)

    primary_id = await db.scalar(
        select(List.primary_column_id).where(List.id == target_list_id)
    )
    if primary_id is not None:
        key = await db.scalar(
            select(Column.uuid).where(
                Column.id == primary_id, Column.deleted_at.is_(None)
            )
        )
        if key is not None:
            return str(key)

    # Same order the grid shows, so the label is the column a person would read
    # first — matching what the UI did before this moved server-side.
    first = await db.scalar(
        select(Column.uuid)
        .where(Column.list_id == target_list_id, Column.deleted_at.is_(None))
        .order_by(Column.position, Column.id)
        .limit(1)
    )
    return str(first) if first is not None else None


def _as_set(value) -> set:
    """A cell's current value as a set of uuid strings — what it already points at."""
    if value is None:
        return set()
    if isinstance(value, list):
        return {str(v) for v in value if v is not None}
    return {str(value)}


def coerce_shape(column: Column, value):
    """A cell's value in the shape its column *currently* declares.

    Toggling a column's `multiple` does not rewrite its records — on a large list
    that would be a multi-minute UPDATE inside a web request — so records outlive
    the setting that shaped them, and a cell can hold `["a", "b"]` on a column that
    now allows one value. Rather than teach every reader about both shapes, the
    service coerces at its edges: here on the way out, and in `_validate_data` on
    the way in. Everything past those two points sees only the declared shape.

    Single column, list value  -> its first element (extras are dropped, which is
    what the user confirmed when they turned `multiple` off).
    Multiple column, bare value -> a one-element list.

    A scheduled reconcile converges the stored data on the same rule, after which
    this is a no-op — it is a migration aid, not a permanent translation layer.
    """
    multiple = bool((column.config or {}).get("multiple"))
    if value is None:
        return None
    if multiple and not isinstance(value, list):
        return [value]
    if not multiple and isinstance(value, list):
        return value[0] if value else None
    return value


class ListItemService:
    @staticmethod
    def _select(list_id: int):
        return select(ListItem).where(
            ListItem.deleted_at.is_(None), ListItem.list_id == list_id
        )

    @classmethod
    async def get_by_uuid(
        cls, db: AsyncSession, list_id: int, uuid: Union[str, UUID]
    ) -> Optional[ListItem]:
        parsed = coerce_uuid(uuid)
        if parsed is None:
            return None
        result = await db.execute(cls._select(list_id).where(ListItem.uuid == parsed))
        return result.scalars().first()

    @classmethod
    async def get_for_read(
        cls, db: AsyncSession, list_id: int, uuid: Union[str, UUID]
    ) -> Optional[ListItem]:
        """`get_by_uuid` for a caller that will *render* the record, so its values
        come back in the shape their columns declare (see `coerce_shape`)."""
        item = await cls.get_by_uuid(db, list_id, uuid)
        if item is None:
            return None
        cls._shape_for_read(await cls._columns_by_key(db, list_id), [item])
        return item

    @staticmethod
    def _shape_for_read(columns: dict[str, Column], items: Sequence[ListItem]) -> None:
        """Present each record in its columns' declared shape.

        Two things happen here: stored values are coerced to the shape their column
        currently declares (see `coerce_shape`), and calculated columns — which
        store nothing — have their value computed and injected under their own key.

        `set_committed_value`, not a plain assignment: this is a *presentation* fix,
        and marking the instance dirty would have the next flush write the coercion
        (and the computed values, which must never be persisted) back to the
        database — turning every read into a silent migration, inside whatever
        transaction happened to be open.
        """
        # Parse each formula once for the whole batch, and map column name -> key so
        # a formula written in names can read the values stored under uuids.
        computed = [
            (key, column, parse(column.config["formula"]))
            for key, column in columns.items()
            if column.type in COMPUTED_TYPES and (column.config or {}).get("formula")
        ]
        key_of_name = {column.name: key for key, column in columns.items()}

        for item in items:
            data = item.data or {}
            shaped = {
                key: coerce_shape(columns[key], value) if key in columns else value
                for key, value in data.items()
            }
            for key, _column, formula in computed:
                inputs = {
                    name: shaped.get(key_of_name[name])
                    for name in formula.refs
                    if name in key_of_name
                }
                shaped[key] = evaluate(formula, inputs)
            if shaped != data:
                set_committed_value(item, "data", shaped)

    @classmethod
    async def _reference_matches(
        cls, db: AsyncSession, user, column: Column, pattern: str
    ) -> list[str]:
        """The uuids a reference column points at whose *label* matches the search.

        A `lookup`, `user` or `group` cell stores a uuid; the thing a person types is
        the label, and it lives in another table. So resolve the label to uuids here,
        then match cells against them.

        A lookup obeys the target list's permissions — `lookup` on it, then the
        per-record filter — exactly as the picker and the export do: searching must
        not become a way to learn what is in a list you cannot read.
        """
        if column.type in PRINCIPAL_TYPES:
            model = User if column.type == ColumnType.user else UserGroup
            label = (
                User.username if column.type == ColumnType.user else UserGroup.name
            )
            rows = await db.execute(
                select(model.uuid)
                .where(
                    model.deleted_at.is_(None),
                    label.ilike(pattern, escape=LIKE_ESCAPE),
                )
                .limit(_SEARCH_REF_MAX)
            )
            return [str(u) for u in rows.scalars().all()]

        from src.permissions.enums import Action, ResourceType
        from src.permissions.services import PermissionService

        config = column.config or {}
        target = (
            await db.execute(
                select(List)
                .options(selectinload(List.workspace))
                .where(
                    List.uuid == coerce_uuid(config.get("target_list_uuid")),
                    List.deleted_at.is_(None),
                )
            )
        ).scalars().first()
        if target is None or not await PermissionService.has(
            db, user, ResourceType.list, target.id, Action.lookup
        ):
            return []

        key = await display_column_key(db, target.id, config)
        if key is None:
            return []

        rows = await db.execute(
            select(ListItem.uuid)
            .where(
                ListItem.list_id == target.id,
                ListItem.deleted_at.is_(None),
                await PermissionService.item_view_filter(
                    db, user, target, Action.lookup
                ),
                ListItem.data[key].astext.ilike(pattern, escape=LIKE_ESCAPE),
            )
            .limit(_SEARCH_REF_MAX)
        )
        return [str(u) for u in rows.scalars().all()]

    @classmethod
    async def _apply_search(
        cls,
        db: AsyncSession,
        lst: List,
        user,
        stmt,
        q: Optional[str],
        columns: dict[str, Column],
    ):
        """Match ``q`` against any of the list's columns.

        Server-side, like every other search here: filtering a fetched page in the
        browser would silently fail to find records past it. Matched per column
        rather than over the whole JSONB blob, so a query cannot match a column
        *uuid* (the keys) instead of a value someone can actually see.

        Reference columns are matched by the label a person can actually read — the
        linked row's, the user's, the group's — not by the uuid the cell stores. The
        label resolves to uuids first (`_reference_matches`), and the cell is then
        matched with `?|`, which is true for a single value *and* for one element of
        a multi-valued cell.
        """
        if not (q and q.strip()) or not columns:
            return stmt
        pattern = like_pattern(q.strip())

        conditions = []
        for key, column in columns.items():
            if column.type in REFERENCE_TYPES:
                uuids = await cls._reference_matches(db, user, column, pattern)
                if uuids:
                    # Typed as text[]: `?|` takes an array of strings, and an
                    # untyped literal list is inferred as jsonb.
                    conditions.append(
                        ListItem.data[key].has_any(
                            sa.literal(uuids, postgresql.ARRAY(sa.Text))
                        )
                    )
                continue
            conditions.append(
                ListItem.data[key].astext.ilike(pattern, escape=LIKE_ESCAPE)
            )

        if not conditions:
            return stmt.where(sa.false())  # a search that can match nothing, matches nothing
        return stmt.where(sa.or_(*conditions))

    @classmethod
    async def list(
        cls,
        db: AsyncSession,
        lst: List,
        user,
        view_uuid: Optional[Union[str, UUID]] = None,
        skip: int = 0,
        limit: int = 100,
        q: Optional[str] = None,
    ) -> Sequence[ListItem]:
        from src.permissions.services import PermissionService

        columns = await cls._columns_by_key(db, lst.id)
        stmt = cls._select(lst.id).where(
            await PermissionService.item_view_filter(db, user, lst)
        )
        # A search narrows whatever the view already shows, rather than replacing it.
        stmt = await cls._apply_search(db, lst, user, stmt, q, columns)
        sorts = None
        group_by = None

        if view_uuid is not None:
            view = await get_by_ref(db, View, lst.id, view_uuid)
            if view is not None:
                config = view.config or {}
                stmt = apply_filters(stmt, config.get("filters"), columns)
                sorts = config.get("sorts")
                group_by = config.get("group_by")

        stmt = apply_sorts(stmt, sorts, columns, group_by).offset(skip).limit(limit)
        items = (await db.execute(stmt)).scalars().all()
        cls._shape_for_read(columns, items)
        return items

    @classmethod
    async def query(
        cls,
        db: AsyncSession,
        lst: List,
        user,
        filters: Optional[dict] = None,
        sorts: Optional[list] = None,
        skip: int = 0,
        limit: int = 100,
    ) -> Sequence[ListItem]:
        """Read records with ad-hoc filters/sorts (no saved view). Unknown
        columns are ignored; empty sorts fall back to id-ascending order."""
        from src.permissions.services import PermissionService

        columns = await cls._columns_by_key(db, lst.id)
        base = cls._select(lst.id).where(
            await PermissionService.item_view_filter(db, user, lst)
        )
        stmt = apply_filters(base, filters, columns)
        stmt = apply_sorts(stmt, sorts, columns).offset(skip).limit(limit)
        items = (await db.execute(stmt)).scalars().all()
        cls._shape_for_read(columns, items)
        return items

    @classmethod
    async def create(
        cls, db: AsyncSession, lst: List, payload: ListItemCreate, user=None
    ) -> ListItem:
        columns = await cls._columns_by_key(db, lst.id)
        data = await cls._validate_data(
            db, lst, columns, payload.data, existing=None, user=user
        )
        await cls._enforce_unique(db, lst.id, columns, data, exclude_id=None)

        item = ListItem(list_id=lst.id, data=data)
        db.add(item)
        await db.flush()
        new_uuid = item.uuid
        await db.commit()
        # get_for_read, not get_by_uuid: the response renders the record, so it
        # carries coerced values and any calculated column's computed value.
        return await cls.get_for_read(db, lst.id, new_uuid)

    @classmethod
    async def update(
        cls,
        db: AsyncSession,
        lst: List,
        uuid: Union[str, UUID],
        payload: ListItemUpdate,
        user=None,
    ) -> Optional[ListItem]:
        item = await cls.get_by_uuid(db, lst.id, uuid)
        if item is None:
            return None

        columns = await cls._columns_by_key(db, lst.id)
        merged = await cls._validate_data(
            db, lst, columns, payload.data, existing=item.data or {}, user=user
        )
        await cls._enforce_unique(db, lst.id, columns, merged, exclude_id=item.id)
        # Reassign a new dict so SQLAlchemy detects the JSONB change.
        item.data = merged
        if payload.permissions_inherit is not None:
            item.permissions_inherit = payload.permissions_inherit
        await db.commit()
        # get_for_read: see the note in create() — the response is a rendered record.
        return await cls.get_for_read(db, lst.id, uuid)

    @classmethod
    async def delete(
        cls, db: AsyncSession, lst: List, uuid: Union[str, UUID]
    ) -> bool:
        item = await cls.get_by_uuid(db, lst.id, uuid)
        if item is None:
            return False

        # Only this. References to the row are deliberately left intact: scrubbing
        # them here is what used to make a soft delete irreversible — the pointers
        # were destroyed and nothing remembered what they were, so restoring the row
        # brought it back stripped of every inbound relationship. They are cleared
        # when the record is *purged*, at which point it is unrecoverable anyway.
        item.deleted_at = datetime.now(timezone.utc)
        await db.commit()
        return True

    @classmethod
    async def bulk_delete(
        cls, db: AsyncSession, lst: List, uuids: Sequence[Union[str, UUID]]
    ) -> int:
        """Soft-delete several records in one transaction. Unknown/already-deleted
        uuids are skipped. Returns the number actually deleted. References to these
        rows survive, as for a single delete."""
        now = datetime.now(timezone.utc)
        deleted = 0
        for uuid in uuids:
            item = await cls.get_by_uuid(db, lst.id, uuid)
            if item is None:
                continue
            item.deleted_at = now
            deleted += 1
        if deleted:
            await db.commit()
        return deleted

    # -- the bin: view and restore soft-deleted records ---------------------

    @staticmethod
    def _select_deleted(list_id: int):
        """Records in the bin: soft-deleted and still inside the retention window.

        Older ones are past `PURGE_DELETED_AFTER_DAYS` and on their way out — the
        purge job may destroy them at any time — so they are no longer offered as
        restorable (see gap #6 in docs/known-gaps.md). With a 0-day window the bin
        is always empty, which is exactly "purge as soon as the job runs".
        """
        cutoff = datetime.now(timezone.utc) - timedelta(
            days=settings.PURGE_DELETED_AFTER_DAYS
        )
        return select(ListItem).where(
            ListItem.list_id == list_id,
            ListItem.deleted_at.is_not(None),
            ListItem.deleted_at >= cutoff,
        )

    @classmethod
    async def get_deleted_by_uuid(
        cls, db: AsyncSession, list_id: int, uuid: Union[str, UUID]
    ) -> Optional[ListItem]:
        """A single record from the bin. `get_by_uuid` filters deleted rows out, so
        restore needs its own fetch."""
        parsed = coerce_uuid(uuid)
        if parsed is None:
            return None
        result = await db.execute(
            cls._select_deleted(list_id).where(ListItem.uuid == parsed)
        )
        return result.scalars().first()

    @classmethod
    async def list_deleted(
        cls, db: AsyncSession, lst: List, skip: int = 0, limit: int = 100
    ) -> Sequence[ListItem]:
        """The list's bin, most recently deleted first, shaped for rendering so its
        reference and calculated cells read the same as in the live grid."""
        columns = await cls._columns_by_key(db, lst.id)
        stmt = (
            cls._select_deleted(lst.id)
            .order_by(ListItem.deleted_at.desc(), ListItem.id.desc())
            .offset(skip)
            .limit(limit)
        )
        items = (await db.execute(stmt)).scalars().all()
        cls._shape_for_read(columns, items)
        return items

    @classmethod
    async def count_deleted(cls, db: AsyncSession, lst: List) -> int:
        """How many records are in the bin — the paginator's total. Built off the
        same predicate as the listing so the two cannot drift."""
        stmt = select(func.count()).select_from(cls._select_deleted(lst.id).subquery())
        return await db.scalar(stmt) or 0

    @classmethod
    async def restore(
        cls, db: AsyncSession, lst: List, uuid: Union[str, UUID]
    ) -> Optional[ListItem]:
        """Bring a soft-deleted record back. Returns None if it is not in the bin.

        A `unique` value may have been taken while the row sat deleted, so the
        restore runs through `_enforce_unique` and can legitimately raise
        `ConflictError`. Nothing else is needed: inbound references were kept intact
        at delete time, so they simply resolve again, and a stale `multiple` shape is
        absorbed by `coerce_shape` on read.
        """
        item = await cls.get_deleted_by_uuid(db, lst.id, uuid)
        if item is None:
            return None

        columns = await cls._columns_by_key(db, lst.id)
        # exclude_id guards against the row matching itself; it is still deleted at
        # this point anyway, so the uniqueness scan (live rows only) would skip it.
        await cls._enforce_unique(db, lst.id, columns, item.data or {}, exclude_id=item.id)

        item.deleted_at = None
        await db.commit()
        return await cls.get_for_read(db, lst.id, uuid)

    @classmethod
    async def purge(
        cls, db: AsyncSession, lst: List, uuid: Union[str, UUID]
    ) -> bool:
        """Permanently destroy a single record from the bin. Returns False if it is
        not in the bin.

        Irreversible, and it does the two things soft-delete deliberately left alone,
        because now the row really is going: it scrubs the inbound lookup references
        that were kept intact so a restore could resolve them, and deletes the record's
        permission grants — polymorphic with no foreign key, so nothing else would —
        before deleting the row. One transaction, mirroring the item purge job.
        """
        item = await cls.get_deleted_by_uuid(db, lst.id, uuid)
        if item is None:
            return False

        item_id = item.id
        # References into this row go first: once it is gone the cells pointing at it
        # would be unresolvable, and nothing else would clean them.
        await cls._scrub_lookup_references(db, lst.uuid, item.uuid)
        # Grants before the row: they point at it polymorphically, so hard-deleting
        # the row would leave them orphaned at an id that no longer exists.
        await db.execute(
            sa.text(
                "DELETE FROM permission_grants "
                "WHERE resource_type = 'item' AND resource_id = :id"
            ),
            {"id": item_id},
        )
        await db.execute(
            sa.text("DELETE FROM list_items WHERE id = :id"), {"id": item_id}
        )
        await db.commit()
        return True

    @classmethod
    async def _scrub_lookup_references(
        cls, db: AsyncSession, list_uuid: UUID, deleted_item_uuid: UUID
    ) -> None:
        lookup_columns = (
            await db.execute(
                select(Column).where(
                    Column.type == ColumnType.lookup,
                    Column.deleted_at.is_(None),
                    Column.config["target_list_uuid"].astext == str(list_uuid),
                )
            )
        ).scalars().all()
        await cls._drop_reference(db, lookup_columns, str(deleted_item_uuid))

    # One statement, and it never asks the column what shape its values are.
    #
    # That question is exactly what used to make this wrong: the scrub picked its
    # SQL from the column's *current* `multiple`, so after that flag was toggled it
    # ran the statement for the wrong shape, matched nothing, and quietly left the
    # deleted user's uuid sitting in the record. Now the predicate finds the rows
    # whatever shape they are in — `?|` is true for a scalar string *and* for an
    # element of an array — and the CASE rewrites each row according to the shape it
    # actually has. Nothing to keep in step, so nothing to get out of step.
    _SCRUB_SQL = sa.text(
        """
        UPDATE list_items
           SET data = jsonb_set(
                   data,
                   ARRAY[:key],
                   CASE WHEN jsonb_typeof(data -> :key) = 'array'
                        THEN COALESCE(
                                 (SELECT jsonb_agg(elem)
                                    FROM jsonb_array_elements(data -> :key) AS elem
                                   WHERE elem <> to_jsonb(CAST(:target AS text))),
                                 '[]'::jsonb)
                        ELSE 'null'::jsonb
                   END)
         WHERE list_id = :list_id
           AND deleted_at IS NULL
           AND data -> :key ?| ARRAY[CAST(:target AS text)]
        """
    )

    @classmethod
    async def _drop_reference(
        cls, db: AsyncSession, columns: Sequence[Column], target: str
    ) -> None:
        """Remove a now-dangling uuid from every record held by these columns.

        One UPDATE per column, matched by a JSONB predicate, rather than hydrating
        every referencing record into Python to rewrite it: a column on a large list
        would otherwise pull the whole list into memory inside the delete's
        transaction. Does not commit.
        """
        # The caller has typically just set deleted_at on an ORM object. Flush it
        # first: these are Core UPDATEs, and a later flush would otherwise reorder
        # them after statements that assume the delete already landed.
        await db.flush()

        for column in columns:
            await db.execute(
                cls._SCRUB_SQL,
                {
                    "list_id": column.list_id,
                    "key": str(column.uuid),
                    "target": target,
                },
            )

    # -- validation ---------------------------------------------------------

    @staticmethod
    async def _columns_by_key(db: AsyncSession, list_id: int) -> dict[str, Column]:
        result = await db.execute(
            select(Column).where(
                Column.list_id == list_id, Column.deleted_at.is_(None)
            )
        )
        return {str(column.uuid): column for column in result.scalars().all()}

    @classmethod
    async def _validate_data(
        cls,
        db: AsyncSession,
        lst: List,
        columns: dict[str, Column],
        incoming: dict,
        existing: Optional[dict],
        user=None,
    ) -> dict:
        """Validate incoming values against the schema and return the record to
        store. When ``existing`` is given the update is a partial merge.

        ``user`` is the account making the write, needed to resolve a `current_user`
        default/onupdate; the temporal tokens do not need it."""
        result = dict(existing) if existing is not None else {}

        for key, value in incoming.items():
            column = columns.get(key)
            if column is None:
                raise ValidationError(f"Unknown column '{key}' for this list")
            if column.type in COMPUTED_TYPES:
                # A calculated cell is derived on read and stores nothing, so there
                # is nothing to set. Reject rather than silently drop it, so a client
                # sending one learns it is not editable instead of wondering why its
                # value vanished.
                raise ValidationError(
                    f"Column '{column.name}' is calculated and cannot be set"
                )
            # Blank means empty, whatever the column type. Decided once, here, rather
            # than per type: otherwise "" clears a user cell but 400s a link cell
            # ("not a full URL"), and the same payload means different things
            # depending on which column it lands in.
            if value is None or (isinstance(value, str) and not value.strip()):
                result[key] = None
                continue
            # The other edge of the shape tolerance (see `coerce_shape`): a client
            # echoing back a legacy array to a now-single column writes the first
            # value, rather than being told its own record is invalid.
            value = coerce_shape(column, value)
            if value is None:
                result[key] = None
                continue
            # Validation may normalize (a user column accepts a username and
            # stores the uuid), so the checked value is what gets written.
            #
            # `kept` is what this cell already holds. A reference to something that
            # has since been deleted stays legal *if it is already there* — otherwise
            # binning one row would make every record pointing at it unsaveable,
            # which is a fine way to lock a list. A *new* reference to a deleted
            # target is still refused.
            kept = _as_set((existing or {}).get(key))
            result[key] = await cls._validate_value(db, lst, column, value, kept)

        # Apply defaults / on-update stamps and enforce required, over the merged
        # record. `coerce_shape` so a resolved token lands in the column's declared
        # shape (a `multiple` user column stores ["<uuid>"], not "<uuid>").
        for key, column in columns.items():
            if column.type in COMPUTED_TYPES:
                # No stored value, so no default to apply and nothing to require.
                continue
            config = column.config or {}
            onupdate = config.get("onupdate")
            if onupdate is not None:
                # Auto-managed: re-stamped on create and every edit, overwriting
                # whatever is there — an editor cannot set it by hand.
                result[key] = coerce_shape(
                    column, resolve_dynamic_default(column.type, onupdate, user=user)
                )
            elif result.get(key) is None:
                default = config.get("default")
                if default is not None:
                    result[key] = coerce_shape(
                        column, resolve_dynamic_default(column.type, default, user=user)
                    )
            if config.get("required") and cls._is_empty(result.get(key)):
                raise ValidationError(f"Column '{column.name}' is required")

        # Nested lookups are a cross-cell rule, so they are checked over the
        # merged record rather than per column — whichever order the cells
        # arrived in, parent and child are both settled by now.
        await cls._enforce_nested_lookups(db, lst, columns, result, incoming, existing)

        return result

    @classmethod
    async def _enforce_nested_lookups(
        cls,
        db: AsyncSession,
        lst: List,
        columns: dict[str, Column],
        result: dict,
        incoming: dict,
        existing: Optional[dict],
    ) -> None:
        """Reject a nested lookup value that does not belong to the parent's.

        Only values being *added* are checked — the same `kept` rule as
        `_validate_lookup`: a stale pairing already stored must not make the
        record unsaveable, it is surfaced when the cell itself is edited. A
        dangling pairing (parent or filter column since deleted) is skipped
        rather than enforced, matching how the picker degrades; the cascade in
        ColumnService normally clears those.
        """
        for key, column in columns.items():
            if column.type != ColumnType.lookup:
                continue
            config = column.config or {}
            parent_key = config.get("parent_column_uuid")
            filter_key = config.get("target_filter_column_uuid")
            if parent_key is None or filter_key is None:
                continue
            if key not in incoming:
                # An untouched cell keeps whatever it holds, however stale.
                continue
            new = _as_set(result.get(key)) - _as_set((existing or {}).get(key))
            if not new:
                continue  # kept or cleared — nothing added to check
            parent_key, filter_key = str(parent_key), str(filter_key)
            parent = columns.get(parent_key)
            if parent is None or parent.type != ColumnType.lookup:
                continue
            target_list_id = await cls._lookup_target_id(db, lst, column, config)
            if not await cls._nested_filter_live(db, target_list_id, filter_key):
                continue

            parent_set = _as_set(result.get(parent_key))
            if not parent_set:
                raise ValidationError(
                    f"Column '{column.name}' requires '{parent.name}' to be "
                    "set first"
                )
            # `_validate_lookup` already resolved labels to uuids and refused
            # unknown/deleted rows, so every uuid in `new` names a real target
            # row; this only asks whether those rows belong to the parent's.
            rows = await db.execute(
                select(ListItem.uuid, ListItem.data[filter_key]).where(
                    ListItem.list_id == target_list_id,
                    ListItem.uuid.in_(new),
                )
            )
            for _, cell in rows.all():
                if not (_as_set(cell) & parent_set):
                    raise ValidationError(
                        f"Column '{column.name}': the selected row does not "
                        f"belong to the selected '{parent.name}'"
                    )

    @classmethod
    async def _nested_filter_live(
        cls, db: AsyncSession, target_list_id: int, filter_key: str
    ) -> bool:
        """Whether a nesting's filter column is still a live column of the
        target list. Cached per request, like `_lookup_target_id` — import
        calls the write path once per row."""
        cache = db.info.setdefault("nested_filter_columns", {})
        cache_key = (target_list_id, filter_key)
        if cache_key not in cache:
            cache[cache_key] = (
                (
                    await db.execute(
                        select(Column.id).where(
                            Column.list_id == target_list_id,
                            Column.uuid == coerce_uuid(filter_key),
                            Column.deleted_at.is_(None),
                        )
                    )
                ).scalars().first()
                is not None
            )
        return cache[cache_key]

    @classmethod
    async def _enforce_unique(
        cls,
        db: AsyncSession,
        list_id: int,
        columns: dict[str, Column],
        data: dict,
        exclude_id: Optional[int],
    ) -> None:
        """Reject records that duplicate a value on a column marked unique."""
        unique_columns = {
            key: column
            for key, column in columns.items()
            if (column.config or {}).get("unique")
        }
        if not unique_columns:
            return

        # Serialize writes to this list so the check-and-insert is atomic: the
        # lock is held until this transaction commits, closing the TOCTOU window
        # where two concurrent writers both see "no duplicate" and both insert.
        await db.execute(
            select(func.pg_advisory_xact_lock(_UNIQUE_LOCK_NS, list_id))
        )

        for key, column in unique_columns.items():
            value = data.get(key)
            if cls._is_empty(value) or isinstance(value, list):
                continue
            stmt = select(ListItem.id).where(
                ListItem.list_id == list_id,
                ListItem.deleted_at.is_(None),
                ListItem.data.contains({key: value}),
            )
            if exclude_id is not None:
                stmt = stmt.where(ListItem.id != exclude_id)
            existing = (await db.execute(stmt.limit(1))).scalars().first()
            if existing is not None:
                raise ConflictError(
                    f"Column '{column.name}' must be unique; that value already exists"
                )

    @staticmethod
    def _is_empty(value) -> bool:
        return value is None or value == "" or value == []

    @classmethod
    def _reference_inputs(cls, column: Column, value, noun: str):
        """The raw values of a reference cell, as (multiple?, [non-empty values]).

        Blank strings are dropped rather than resolved: an empty cell means "no
        reference", and passing "" through to a label match would silently link the
        record to whichever target row happens to have a blank label.

        Values are otherwise passed through **verbatim**. Trimming them here would
        be a silent rewrite of the thing we then match on: a target row labelled
        " Springfield High" exports with its leading space, and a trimmed import
        would fail to find it — breaking the round-trip on a file this app wrote.
        """
        multiple = bool((column.config or {}).get("multiple"))
        if multiple and not isinstance(value, list):
            cls._type_error(column, f"a list of {noun}s")

        raws = []
        for raw in value if multiple else [value]:
            if raw is None or (isinstance(raw, str) and not raw.strip()):
                continue
            if not isinstance(raw, str):
                cls._type_error(column, f"a {noun} uuid or name")
            raws.append(raw)
        return multiple, raws

    @staticmethod
    def _as_uuid(raw: str) -> Optional[UUID]:
        """A reference value read as a uuid, or None if it is a name/label.

        Trimmed only for the *parse*: surrounding space around a uuid is noise, but
        around a label it is part of the label (see `_reference_inputs`).
        """
        return coerce_uuid(raw.strip())

    @classmethod
    async def _validate_value(
        cls, db: AsyncSession, lst: List, column: Column, value, kept: set = frozenset()
    ):
        """Validate a value and return what should be stored (usually the value
        itself; reference types normalize to a uuid string).

        ``kept`` is the set of uuids this cell already holds; those may point at a
        deleted target, because they were valid when they were written.
        """
        # References need the database; every other type is a pure scalar check.
        if column.type == ColumnType.lookup:
            return await cls._validate_lookup(db, lst, column, value, kept)
        if column.type in PRINCIPAL_TYPES:
            return await cls._validate_principal(db, column, value, kept)
        if column.type == ColumnType.link and isinstance(value, str):
            # Store what was validated: the check parses the trimmed URL, so
            # trimming here keeps the stored string and the checked string the
            # same one — otherwise `unique` and `max_length` see the padding.
            value = value.strip()
        validate_scalar(column.type, column.config or {}, column.name, value)
        return value

    @classmethod
    async def _validate_principal(
        cls, db: AsyncSession, column: Column, value, kept: set = frozenset()
    ):
        """Resolve a `user`/`group` cell to the referenced uuid(s).

        Accepts a uuid (what the UI sends) or a username / group name (what a CSV
        written by our own export contains, so an export round-trips). Stores the
        uuid either way, since names are mutable.

        Resolved in one query for the whole cell, not one per value: import runs
        this per row, so a query per value turns a bulk import into thousands of
        sequential round-trips.

        A uuid in ``kept`` — already stored in this cell — is allowed even if the
        account has since been deleted. Anything else must point at a live one.
        """
        model = User if column.type == ColumnType.user else UserGroup
        name_field = User.username if column.type == ColumnType.user else UserGroup.name
        noun = "user" if column.type == ColumnType.user else "group"

        multiple, raws = cls._reference_inputs(column, value, noun)
        if not raws:
            return [] if multiple else None

        # Parsed once per value, not once per use.
        parsed = [(raw, cls._as_uuid(raw)) for raw in raws]
        uuids = [uuid for _, uuid in parsed if uuid is not None]
        names = [raw for raw, uuid in parsed if uuid is None]

        rows = (
            await db.execute(
                select(model.uuid, name_field, model.deleted_at).where(
                    sa.or_(
                        model.uuid.in_(uuids) if uuids else sa.false(),
                        name_field.in_(names) if names else sa.false(),
                    ),
                )
            )
        ).all()
        live_uuids = {str(u) for u, _, gone in rows if gone is None}
        deleted_uuids = {str(u) for u, _, gone in rows if gone is not None}
        by_name = {name: str(u) for u, name, gone in rows if gone is None}

        resolved: list[str] = []
        for raw, uuid in parsed:
            as_str = str(uuid) if uuid else None
            if as_str and as_str in live_uuids:
                resolved.append(as_str)
                continue
            # Already in the record, and the account has since gone: keep it. The
            # alternative is that binning an account makes every record naming them
            # unsaveable until someone edits the cell.
            if as_str and as_str in kept and as_str in deleted_uuids:
                resolved.append(as_str)
                continue
            hit = by_name.get(raw)
            if hit is None:
                raise UnknownReferenceError(
                    f"Column '{column.name}' references an unknown {noun}: {raw}"
                )
            resolved.append(hit)

        return resolved if multiple else resolved[0]

    @classmethod
    async def scrub_principal_references(
        cls, db: AsyncSession, column_type: ColumnType, principal_uuid: UUID
    ) -> None:
        """Drop references to a deleted user/group from every record that holds one.

        These are soft references (a uuid in JSONB, no FK), so nothing cleans them
        up automatically — the same trade lookup makes, and the same fix. Called
        from UserService.delete / UserGroupService.delete; does not commit.
        """
        columns = (
            await db.execute(
                select(Column).where(
                    Column.type == column_type, Column.deleted_at.is_(None)
                )
            )
        ).scalars().all()
        await cls._drop_reference(db, columns, str(principal_uuid))

    @classmethod
    async def _validate_lookup(
        cls, db: AsyncSession, lst: List, column: Column, value, kept: set = frozenset()
    ):
        """Resolve a lookup cell to target-row uuid(s).

        Accepts a row uuid (what the UI sends) or the row's **label** — the value
        of the lookup's display column — which is what our own export writes, so a
        file round-trips. A label matching more than one row is an error rather
        than a guess: unlike a username, a row label is not unique.

        A uuid in ``kept`` — already stored in this cell — is allowed even if the
        target row has since been deleted. Anything else must point at a live row:
        nothing new may be linked to a record on its way out.
        """
        config = column.config or {}
        multiple, raws = cls._reference_inputs(column, value, "row")
        if not raws:
            return [] if multiple else None

        target_list_id = await cls._lookup_target_id(db, lst, column, config)

        parsed = [(raw, cls._as_uuid(raw)) for raw in raws]
        uuids = [uuid for _, uuid in parsed if uuid is not None]
        labels = [raw for raw, uuid in parsed if uuid is None]

        # Two queries for the whole cell — one for the uuids, one for the labels —
        # rather than one per value. Import calls this per row, so per-value queries
        # turn a bulk import into thousands of sequential round-trips.
        found_uuids: set[str] = set()
        if uuids:
            rows = await db.execute(
                select(ListItem.uuid, ListItem.deleted_at).where(
                    ListItem.list_id == target_list_id,
                    ListItem.uuid.in_(uuids),
                )
            )
            for row_uuid, gone in rows.all():
                # Live, or already in this cell and since deleted.
                if gone is None or str(row_uuid) in kept:
                    found_uuids.add(str(row_uuid))

        by_label: dict[str, tuple[str, int]] = {}
        if labels:
            key = await cls._lookup_display_key(db, target_list_id, column, config)
            label_col = ListItem.data[key].astext
            # Grouped, so a label shared by a million rows costs one row of result
            # rather than a million: all we need is *one* match and whether there
            # was more than one.
            rows = await db.execute(
                # Postgres has no min(uuid); the text form is what gets stored anyway.
                select(
                    label_col,
                    func.min(sa.cast(ListItem.uuid, sa.Text)),
                    func.count(),
                )
                .where(
                    ListItem.list_id == target_list_id,
                    # Live rows only: a *label* is how something new gets picked, and
                    # nothing new may point at a row in the bin. A uuid already in the
                    # cell is handled above.
                    ListItem.deleted_at.is_(None),
                    label_col.in_(labels),
                )
                .group_by(label_col)
            )
            by_label = {
                label: (row_uuid, count) for label, row_uuid, count in rows.all()
            }

        resolved: list[str] = []
        for raw, uuid in parsed:
            if uuid is not None:
                if str(uuid) not in found_uuids:
                    raise UnknownReferenceError(
                        f"Lookup column '{column.name}' references an unknown row: {raw}"
                    )
                resolved.append(str(uuid))
                continue

            match = by_label.get(raw)
            if match is None:
                raise UnknownReferenceError(
                    f"Lookup column '{column.name}' references an unknown row: {raw}"
                )
            row_uuid, count = match
            if count > 1:
                # Row labels are not unique (usernames are), so there is no defensible
                # way to pick one — say so instead of silently linking the wrong row.
                raise ValidationError(
                    f"Lookup column '{column.name}': '{raw}' matches more than one row "
                    f"in the target list; use the row uuid instead"
                )
            resolved.append(row_uuid)

        return resolved if multiple else resolved[0]

    # A lookup's target list and display column are fixed by its config, but a row
    # write resolves them — so an import would re-run these lookups for every row.
    # Cache them on the session, which is scoped to exactly one request.
    @classmethod
    async def _lookup_target_id(
        cls, db: AsyncSession, lst: List, column: Column, config: dict
    ) -> int:
        cache = db.info.setdefault("lookup_target_ids", {})
        cache_key = (lst.workspace.owner_id, str(config.get("target_list_uuid")))
        if cache_key not in cache:
            cache[cache_key] = (
                await db.execute(
                    select(List.id)
                    .join(Workspace, List.workspace_id == Workspace.id)
                    .where(
                        List.uuid == coerce_uuid(config.get("target_list_uuid")),
                        Workspace.owner_id == lst.workspace.owner_id,
                        List.deleted_at.is_(None),
                        Workspace.deleted_at.is_(None),
                    )
                )
            ).scalars().first()

        target_list_id = cache[cache_key]
        if target_list_id is None:
            raise UnknownReferenceError(
                f"Lookup column '{column.name}' points at a missing target list"
            )
        return target_list_id

    @classmethod
    async def _lookup_display_key(
        cls, db: AsyncSession, target_list_id: int, column: Column, config: dict
    ) -> str:
        cache = db.info.setdefault("lookup_display_keys", {})
        cache_key = (target_list_id, str(config.get("display_column_uuid")))
        if cache_key not in cache:
            cache[cache_key] = await display_column_key(db, target_list_id, config)

        key = cache[cache_key]
        if key is None:
            raise UnknownReferenceError(
                f"Lookup column '{column.name}' can only be set by row uuid: its "
                f"target list has no column to match a label against"
            )
        return key

    @staticmethod
    def _type_error(column: Column, expected: str) -> None:
        raise ValidationError(f"Column '{column.name}' expects {expected}")
