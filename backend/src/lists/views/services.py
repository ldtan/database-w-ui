from datetime import datetime, timezone
from typing import Optional, Sequence, Union
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions import ConflictError, UnknownReferenceError
from src.core.utils import coerce_uuid

from ..columns.models import Column
from ..models import List
from .models import View
from .schemas import ViewConfig, ViewCreate, ViewUpdate


class ViewService:
    @staticmethod
    def _select(list_id: int):
        return select(View).where(View.deleted_at.is_(None), View.list_id == list_id)

    @classmethod
    async def get_by_uuid(
        cls, db: AsyncSession, list_id: int, uuid: Union[str, UUID]
    ) -> Optional[View]:
        parsed = coerce_uuid(uuid)
        if parsed is None:
            return None
        result = await db.execute(cls._select(list_id).where(View.uuid == parsed))
        return result.scalars().first()

    @classmethod
    async def list(cls, db: AsyncSession, list_id: int) -> Sequence[View]:
        result = await db.execute(
            cls._select(list_id).order_by(View.position, View.id)
        )
        return result.scalars().all()

    @classmethod
    async def create(cls, db: AsyncSession, lst: List, payload: ViewCreate) -> View:
        await cls._validate_config(db, lst.id, payload.config)
        view = View(
            list_id=lst.id,
            name=payload.name,
            display_name=payload.display_name,
            position=payload.position,
            config=payload.config.model_dump(mode="json"),
        )
        db.add(view)
        try:
            await db.flush()
            new_uuid = view.uuid
            await db.commit()
        except IntegrityError as exc:
            await db.rollback()
            raise ConflictError(
                "A view with this name already exists on the list"
            ) from exc
        return await cls.get_by_uuid(db, lst.id, new_uuid)

    @classmethod
    async def update(
        cls,
        db: AsyncSession,
        lst: List,
        uuid: Union[str, UUID],
        payload: ViewUpdate,
    ) -> Optional[View]:
        view = await cls.get_by_uuid(db, lst.id, uuid)
        if view is None:
            return None

        data = payload.model_dump(exclude_unset=True)
        if "config" in data:
            await cls._validate_config(db, lst.id, payload.config)
            view.config = payload.config.model_dump(mode="json")
            data.pop("config")
        if data.get("name") is None:
            data.pop("name", None)
        if data.get("display_name") is None:
            data.pop("display_name", None)
        if data.get("position") is None:
            data.pop("position", None)

        for field, value in data.items():
            setattr(view, field, value)

        try:
            await db.commit()
        except IntegrityError as exc:
            await db.rollback()
            raise ConflictError(
                "A view with this name already exists on the list"
            ) from exc
        return await cls.get_by_uuid(db, lst.id, uuid)

    @classmethod
    async def delete(
        cls, db: AsyncSession, list_id: int, uuid: Union[str, UUID]
    ) -> bool:
        view = await cls.get_by_uuid(db, list_id, uuid)
        if view is None:
            return False

        view.deleted_at = datetime.now(timezone.utc)
        await db.commit()
        return True

    @staticmethod
    async def _validate_config(
        db: AsyncSession, list_id: int, config: ViewConfig
    ) -> None:
        """Ensure every column referenced by the view belongs to the list."""
        referenced: set[UUID] = set()
        referenced.update(c.column_uuid for c in config.filters.conditions)
        referenced.update(s.column_uuid for s in config.sorts)
        if config.group_by is not None:
            referenced.add(config.group_by.column_uuid)
        if not referenced:
            return

        result = await db.execute(
            select(Column.uuid).where(
                Column.list_id == list_id,
                Column.uuid.in_(referenced),
                Column.deleted_at.is_(None),
            )
        )
        found = set(result.scalars().all())
        missing = [str(u) for u in referenced if u not in found]
        if missing:
            raise UnknownReferenceError(
                f"View references column(s) not on this list: {', '.join(missing)}"
            )
