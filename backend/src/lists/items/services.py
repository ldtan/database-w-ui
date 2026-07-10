from datetime import datetime, timezone
from typing import Optional, Sequence, Union
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions import UnknownReferenceError, ValidationError
from src.core.utils import coerce_uuid

from ..columns.enums import ColumnType
from ..columns.models import Column
from ..models import List
from .models import ListItem
from .schemas import ListItemCreate, ListItemUpdate


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
        cls, db: AsyncSession, list_id: int, skip: int = 0, limit: int = 100
    ) -> Sequence[ListItem]:
        result = await db.execute(
            cls._select(list_id).order_by(ListItem.id).offset(skip).limit(limit)
        )
        return result.scalars().all()

    @classmethod
    async def create(
        cls, db: AsyncSession, lst: List, payload: ListItemCreate
    ) -> ListItem:
        columns = await cls._columns_by_key(db, lst.id)
        data = await cls._validate_data(db, lst, columns, payload.data, existing=None)

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
        # Reassign a new dict so SQLAlchemy detects the JSONB change.
        item.data = merged
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
                    result[key] = default
            if config.get("required") and cls._is_empty(result.get(key)):
                raise ValidationError(f"Column '{column.name}' is required")

        return result

    @staticmethod
    def _is_empty(value) -> bool:
        return value is None or value == "" or value == []

    @classmethod
    async def _validate_value(
        cls, db: AsyncSession, lst: List, column: Column, value
    ) -> None:
        type_ = column.type
        config = column.config or {}

        if type_ in (ColumnType.text, ColumnType.long_text, ColumnType.date):
            if not isinstance(value, str):
                cls._type_error(column, "a string")

        elif type_ == ColumnType.number:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                cls._type_error(column, "a number")

        elif type_ == ColumnType.checkbox:
            if not isinstance(value, bool):
                cls._type_error(column, "a boolean")

        elif type_ == ColumnType.choice:
            valid_ids = {opt["id"] for opt in config.get("options", [])}
            values = value if config.get("multiple") else [value]
            if config.get("multiple") and not isinstance(value, list):
                cls._type_error(column, "a list of option ids")
            for item in values:
                if item not in valid_ids:
                    raise ValidationError(
                        f"'{item}' is not a valid option for column '{column.name}'"
                    )

        elif type_ == ColumnType.lookup:
            await cls._validate_lookup(db, lst, column, value)

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
            select(List.id).where(
                List.uuid == coerce_uuid(target_list_uuid),
                List.owner_id == lst.owner_id,
                List.deleted_at.is_(None),
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
