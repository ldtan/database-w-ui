from datetime import datetime, timezone
from typing import Optional, Sequence, Union
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions import ConflictError, UnknownReferenceError, ValidationError
from src.core.utils import coerce_uuid
from src.workspaces.models import Workspace

from src.auth.users.models import User, UserGroup

from ..columns.enums import PRINCIPAL_TYPES, ColumnType
from ..columns.models import Column
from ..columns.validation import resolve_dynamic_default, validate_scalar
from ..models import List
from ..views.models import View
from .filtering import apply_filters, apply_sorts
from .models import ListItem
from .schemas import ListItemCreate, ListItemUpdate

# Namespace for the per-list advisory lock that serializes unique-value writes.
_UNIQUE_LOCK_NS = 0x4C495354  # "LIST"


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
    async def list(
        cls,
        db: AsyncSession,
        lst: List,
        user,
        view_uuid: Optional[Union[str, UUID]] = None,
        skip: int = 0,
        limit: int = 100,
    ) -> Sequence[ListItem]:
        from src.permissions.services import PermissionService

        columns = await cls._columns_by_key(db, lst.id)
        stmt = cls._select(lst.id).where(
            await PermissionService.item_view_filter(db, user, lst)
        )
        sorts = None
        group_by = None

        if view_uuid is not None:
            parsed = coerce_uuid(view_uuid)
            view = None
            if parsed is not None:
                view = (
                    await db.execute(
                        select(View).where(
                            View.list_id == lst.id,
                            View.uuid == parsed,
                            View.deleted_at.is_(None),
                        )
                    )
                ).scalars().first()
            if view is not None:
                config = view.config or {}
                stmt = apply_filters(stmt, config.get("filters"), columns)
                sorts = config.get("sorts")
                group_by = config.get("group_by")

        stmt = apply_sorts(stmt, sorts, columns, group_by).offset(skip).limit(limit)
        result = await db.execute(stmt)
        return result.scalars().all()

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
        result = await db.execute(stmt)
        return result.scalars().all()

    @classmethod
    async def create(
        cls, db: AsyncSession, lst: List, payload: ListItemCreate
    ) -> ListItem:
        columns = await cls._columns_by_key(db, lst.id)
        data = await cls._validate_data(db, lst, columns, payload.data, existing=None)
        await cls._enforce_unique(db, lst.id, columns, data, exclude_id=None)

        item = ListItem(list_id=lst.id, data=data)
        db.add(item)
        await db.flush()
        new_uuid = item.uuid
        await db.commit()
        return await cls.get_by_uuid(db, lst.id, new_uuid)

    @classmethod
    async def update(
        cls,
        db: AsyncSession,
        lst: List,
        uuid: Union[str, UUID],
        payload: ListItemUpdate,
    ) -> Optional[ListItem]:
        item = await cls.get_by_uuid(db, lst.id, uuid)
        if item is None:
            return None

        columns = await cls._columns_by_key(db, lst.id)
        merged = await cls._validate_data(
            db, lst, columns, payload.data, existing=item.data or {}
        )
        await cls._enforce_unique(db, lst.id, columns, merged, exclude_id=item.id)
        # Reassign a new dict so SQLAlchemy detects the JSONB change.
        item.data = merged
        if payload.permissions_inherit is not None:
            item.permissions_inherit = payload.permissions_inherit
        await db.commit()
        return await cls.get_by_uuid(db, lst.id, uuid)

    @classmethod
    async def delete(
        cls, db: AsyncSession, lst: List, uuid: Union[str, UUID]
    ) -> bool:
        item = await cls.get_by_uuid(db, lst.id, uuid)
        if item is None:
            return False

        item.deleted_at = datetime.now(timezone.utc)
        # Remove references to this row from any lookup column targeting this list.
        await cls._scrub_lookup_references(db, lst.uuid, item.uuid)
        await db.commit()
        return True

    @classmethod
    async def bulk_delete(
        cls, db: AsyncSession, lst: List, uuids: Sequence[Union[str, UUID]]
    ) -> int:
        """Soft-delete several records in one transaction, scrubbing lookup
        references for each. Unknown/already-deleted uuids are skipped. Returns
        the number actually deleted."""
        now = datetime.now(timezone.utc)
        deleted = 0
        for uuid in uuids:
            item = await cls.get_by_uuid(db, lst.id, uuid)
            if item is None:
                continue
            item.deleted_at = now
            await cls._scrub_lookup_references(db, lst.uuid, item.uuid)
            deleted += 1
        if deleted:
            await db.commit()
        return deleted

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

    @staticmethod
    async def _drop_reference(
        db: AsyncSession, columns: Sequence[Column], target: str
    ) -> None:
        """Remove a now-dangling uuid from every record held by these columns.

        One UPDATE per column, matched by a JSONB containment predicate, rather
        than hydrating every referencing record into Python to rewrite it: a
        column on a large list would otherwise pull the whole list into memory
        inside the delete's transaction. Does not commit.
        """
        # The caller has typically just set deleted_at on an ORM object. Flush it
        # first: these are Core UPDATEs, and a later flush would otherwise reorder
        # them after statements that assume the delete already landed.
        await db.flush()

        for column in columns:
            key = str(column.uuid)
            multiple = bool((column.config or {}).get("multiple"))
            params = {"list_id": column.list_id, "key": key, "target": target}

            if multiple:
                # Rebuild the array without the dangling uuid. `- to_jsonb(text)`
                # is not an array-element delete, so filter and re-aggregate.
                stmt = sa.text(
                    """
                    UPDATE list_items SET data = jsonb_set(
                        data,
                        ARRAY[:key],
                        COALESCE(
                            (SELECT jsonb_agg(elem)
                               FROM jsonb_array_elements(data -> :key) AS elem
                              WHERE elem <> to_jsonb(CAST(:target AS text))),
                            '[]'::jsonb
                        )
                    )
                    WHERE list_id = :list_id
                      AND deleted_at IS NULL
                      AND jsonb_typeof(data -> :key) = 'array'
                      AND data -> :key @> to_jsonb(ARRAY[CAST(:target AS text)])
                    """
                )
            else:
                stmt = sa.text(
                    """
                    UPDATE list_items
                       SET data = jsonb_set(data, ARRAY[:key], 'null'::jsonb)
                     WHERE list_id = :list_id
                       AND deleted_at IS NULL
                       AND data ->> :key = :target
                    """
                )
            await db.execute(stmt, params)

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
    ) -> dict:
        """Validate incoming values against the schema and return the record to
        store. When ``existing`` is given the update is a partial merge."""
        result = dict(existing) if existing is not None else {}

        for key, value in incoming.items():
            column = columns.get(key)
            if column is None:
                raise ValidationError(f"Unknown column '{key}' for this list")
            # Blank means empty, whatever the column type. Decided once, here, rather
            # than per type: otherwise "" clears a user cell but 400s a link cell
            # ("not a full URL"), and the same payload means different things
            # depending on which column it lands in.
            if value is None or (isinstance(value, str) and not value.strip()):
                result[key] = None
                continue
            # Validation may normalize (a user column accepts a username and
            # stores the uuid), so the checked value is what gets written.
            result[key] = await cls._validate_value(db, lst, column, value)

        # Apply defaults and enforce required, over the final merged record.
        for key, column in columns.items():
            config = column.config or {}
            if result.get(key) is None:
                default = config.get("default")
                if default is not None:
                    result[key] = resolve_dynamic_default(column.type, default)
            if config.get("required") and cls._is_empty(result.get(key)):
                raise ValidationError(f"Column '{column.name}' is required")

        return result

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
        cls, db: AsyncSession, lst: List, column: Column, value
    ):
        """Validate a value and return what should be stored (usually the value
        itself; reference types normalize to a uuid string)."""
        # References need the database; every other type is a pure scalar check.
        if column.type == ColumnType.lookup:
            return await cls._validate_lookup(db, lst, column, value)
        if column.type in PRINCIPAL_TYPES:
            return await cls._validate_principal(db, column, value)
        if column.type == ColumnType.link and isinstance(value, str):
            # Store what was validated: the check parses the trimmed URL, so
            # trimming here keeps the stored string and the checked string the
            # same one — otherwise `unique` and `max_length` see the padding.
            value = value.strip()
        validate_scalar(column.type, column.config or {}, column.name, value)
        return value

    @classmethod
    async def _validate_principal(cls, db: AsyncSession, column: Column, value):
        """Resolve a `user`/`group` cell to the referenced uuid(s).

        Accepts a uuid (what the UI sends) or a username / group name (what a CSV
        written by our own export contains, so an export round-trips). Stores the
        uuid either way, since names are mutable.

        Resolved in one query for the whole cell, not one per value: import runs
        this per row, so a query per value turns a bulk import into thousands of
        sequential round-trips.
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
                select(model.uuid, name_field).where(
                    model.deleted_at.is_(None),
                    sa.or_(
                        model.uuid.in_(uuids) if uuids else sa.false(),
                        name_field.in_(names) if names else sa.false(),
                    ),
                )
            )
        ).all()
        found_uuids = {str(uuid) for uuid, _ in rows}
        by_name = {name: str(uuid) for uuid, name in rows}

        resolved: list[str] = []
        for raw, uuid in parsed:
            hit = str(uuid) if uuid and str(uuid) in found_uuids else by_name.get(raw)
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
        cls, db: AsyncSession, lst: List, column: Column, value
    ):
        """Resolve a lookup cell to target-row uuid(s).

        Accepts a row uuid (what the UI sends) or the row's **label** — the value
        of the lookup's display column — which is what our own export writes, so a
        file round-trips. A label matching more than one row is an error rather
        than a guess: unlike a username, a row label is not unique.
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
                select(ListItem.uuid).where(
                    ListItem.list_id == target_list_id,
                    ListItem.uuid.in_(uuids),
                    ListItem.deleted_at.is_(None),
                )
            )
            found_uuids = {str(u) for u in rows.scalars().all()}

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
