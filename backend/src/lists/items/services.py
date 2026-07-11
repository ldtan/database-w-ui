from datetime import datetime, timezone
from typing import Optional, Sequence, Union
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions import ConflictError, UnknownReferenceError, ValidationError
from src.core.utils import coerce_uuid
from src.workspaces.models import Workspace

from ..columns.enums import ColumnType
from ..columns.models import Column
from ..columns.validation import resolve_dynamic_default, validate_scalar
from ..models import List
from ..views.models import View
from .filtering import apply_filters, apply_sorts
from .models import ListItem
from .schemas import ListItemCreate, ListItemUpdate

# Namespace for the per-list advisory lock that serializes unique-value writes.
_UNIQUE_LOCK_NS = 0x4C495354  # "LIST"


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

        target = str(deleted_item_uuid)
        for column in lookup_columns:
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
            await cls._validate_value(db, lst, column, value)
            result[key] = value

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
    ) -> None:
        # Lookups need the database; every other type is a pure scalar check.
        if column.type == ColumnType.lookup:
            await cls._validate_lookup(db, lst, column, value)
        else:
            validate_scalar(column.type, column.config or {}, column.name, value)

    @classmethod
    async def _validate_lookup(
        cls, db: AsyncSession, lst: List, column: Column, value
    ) -> None:
        config = column.config or {}
        multiple = bool(config.get("multiple"))
        if multiple and not isinstance(value, list):
            cls._type_error(column, "a list of row uuids")
        raw_values = value if multiple else [value]

        parsed: list[UUID] = []
        for raw in raw_values:
            parsed_uuid = coerce_uuid(raw)
            if parsed_uuid is None:
                cls._type_error(column, "a row uuid")
            parsed.append(parsed_uuid)
        if not parsed:
            return

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

        found = await db.execute(
            select(ListItem.uuid).where(
                ListItem.list_id == target_list_id,
                ListItem.uuid.in_(parsed),
                ListItem.deleted_at.is_(None),
            )
        )
        found_uuids = set(found.scalars().all())
        missing = [str(u) for u in parsed if u not in found_uuids]
        if missing:
            raise UnknownReferenceError(
                f"Lookup column '{column.name}' references unknown row(s): "
                f"{', '.join(missing)}"
            )

    @staticmethod
    def _type_error(column: Column, expected: str) -> None:
        raise ValidationError(f"Column '{column.name}' expects {expected}")
