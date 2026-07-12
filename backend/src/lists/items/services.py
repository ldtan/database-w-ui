from datetime import datetime, timezone
from typing import Optional, Sequence, Union
from uuid import UUID

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
        Does not commit."""
        for column in columns:
            key = str(column.uuid)
            multiple = bool((column.config or {}).get("multiple"))
            items = (
                await db.execute(
                    select(ListItem).where(
                        ListItem.list_id == column.list_id,
                        ListItem.deleted_at.is_(None),
                        ListItem.data.has_key(key),
                    )
                )
            ).scalars().all()

            for item in items:
                value = (item.data or {}).get(key)
                if multiple:
                    if isinstance(value, list) and target in value:
                        new_data = dict(item.data)
                        new_data[key] = [v for v in value if v != target]
                        item.data = new_data
                elif value == target:
                    new_data = dict(item.data)
                    new_data[key] = None
                    item.data = new_data

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
            if value is None:
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
    async def _validate_value(
        cls, db: AsyncSession, lst: List, column: Column, value
    ):
        """Validate a value and return what should be stored (usually the value
        itself; reference types normalize to a uuid string)."""
        # References need the database; every other type is a pure scalar check.
        if column.type == ColumnType.lookup:
            return await cls._validate_lookup(db, lst, column, value)
        elif column.type in PRINCIPAL_TYPES:
            return await cls._validate_principal(db, column, value)
        else:
            validate_scalar(column.type, column.config or {}, column.name, value)
        return value

    @classmethod
    async def _validate_principal(cls, db: AsyncSession, column: Column, value):
        """Resolve a `user`/`group` cell to the referenced uuid(s).

        Accepts a uuid (what the UI sends) or a username / group name (what a CSV
        written by our own export contains, so an export round-trips). Stores the
        uuid either way, since names are mutable.
        """
        model = User if column.type == ColumnType.user else UserGroup
        name_field = User.username if column.type == ColumnType.user else UserGroup.name
        noun = "user" if column.type == ColumnType.user else "group"

        multiple = bool((column.config or {}).get("multiple"))
        if multiple and not isinstance(value, list):
            cls._type_error(column, f"a list of {noun}s")
        raw_values = value if multiple else [value]
        if not raw_values:
            return [] if multiple else None

        resolved: list[str] = []
        for raw in raw_values:
            if not isinstance(raw, str):
                cls._type_error(column, f"a {noun} uuid or name")
            parsed = coerce_uuid(raw)
            stmt = select(model.uuid).where(model.deleted_at.is_(None))
            stmt = stmt.where(
                model.uuid == parsed if parsed is not None else name_field == raw
            )
            found = (await db.execute(stmt)).scalars().first()
            if found is None:
                raise UnknownReferenceError(
                    f"Column '{column.name}' references an unknown {noun}: {raw}"
                )
            resolved.append(str(found))

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
        multiple = bool(config.get("multiple"))
        if multiple and not isinstance(value, list):
            cls._type_error(column, "a list of rows")
        raw_values = value if multiple else [value]
        if not raw_values:
            return [] if multiple else None

        target_list_uuid = config.get("target_list_uuid")
        target = await db.execute(
            select(List.id)
            .join(Workspace, List.workspace_id == Workspace.id)
            .where(
                List.uuid == coerce_uuid(target_list_uuid),
                Workspace.owner_id == lst.workspace.owner_id,
                List.deleted_at.is_(None),
                Workspace.deleted_at.is_(None),
            )
        )
        target_list_id = target.scalars().first()
        if target_list_id is None:
            raise UnknownReferenceError(
                f"Lookup column '{column.name}' points at a missing target list"
            )

        resolved: list[str] = []
        for raw in raw_values:
            if not isinstance(raw, str):
                cls._type_error(column, "a row uuid or label")
            parsed = coerce_uuid(raw)
            if parsed is not None:
                found = (
                    await db.execute(
                        select(ListItem.uuid).where(
                            ListItem.list_id == target_list_id,
                            ListItem.uuid == parsed,
                            ListItem.deleted_at.is_(None),
                        )
                    )
                ).scalars().first()
                if found is None:
                    raise UnknownReferenceError(
                        f"Lookup column '{column.name}' references an unknown row: {raw}"
                    )
                resolved.append(str(found))
                continue

            resolved.append(
                str(await cls._row_by_label(db, target_list_id, column, config, raw))
            )

        return resolved if multiple else resolved[0]

    @classmethod
    async def _row_by_label(
        cls, db: AsyncSession, target_list_id: int, column: Column, config: dict, label: str
    ) -> UUID:
        key = await display_column_key(db, target_list_id, config)
        if key is None:
            raise UnknownReferenceError(
                f"Lookup column '{column.name}' can only be set by row uuid: its "
                f"target list has no display column to match '{label}' against"
            )

        matches = (
            await db.execute(
                select(ListItem.uuid)
                .where(
                    ListItem.list_id == target_list_id,
                    ListItem.deleted_at.is_(None),
                    ListItem.data[key].astext == label,
                )
                .limit(2)
            )
        ).scalars().all()

        if not matches:
            raise UnknownReferenceError(
                f"Lookup column '{column.name}' references an unknown row: {label}"
            )
        if len(matches) > 1:
            # Row labels are not unique (usernames are), so there is no defensible
            # way to pick one — say so instead of silently linking the wrong row.
            raise ValidationError(
                f"Lookup column '{column.name}': '{label}' matches more than one row "
                f"in the target list; use the row uuid instead"
            )
        return matches[0]

    @staticmethod
    def _type_error(column: Column, expected: str) -> None:
        raise ValidationError(f"Column '{column.name}' expects {expected}")
