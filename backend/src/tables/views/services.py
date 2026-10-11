from datetime import datetime, timezone
from typing import Optional, Sequence, Union
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions import ConflictError, UnknownReferenceError
from src.core.utils import coerce_uuid

from ..columns.models import Column, in_shape
from ..models import Table
from .models import View
from .schemas import ViewConfig, ViewCreate, ViewUpdate


class ViewService:
    @staticmethod
    def _select(table_id: int):
        return select(View).where(View.deleted_at.is_(None), View.table_id == table_id)

    @classmethod
    async def get_by_uuid(
        cls, db: AsyncSession, table_id: int, uuid: Union[str, UUID]
    ) -> Optional[View]:
        parsed = coerce_uuid(uuid)
        if parsed is None:
            return None
        result = await db.execute(cls._select(table_id).where(View.uuid == parsed))
        return result.scalars().first()

    @classmethod
    async def list(cls, db: AsyncSession, table_id: int, user) -> Sequence[View]:
        from src.permissions.enums import Action, ResourceType
        from src.permissions.services import PermissionService

        result = await db.execute(cls._select(table_id).order_by(View.position, View.id))
        views = result.scalars().all()
        if not views:
            return []

        # One resolve for the whole collection, not one per row. The comment this
        # replaces said "few views per table, so resolve visibility per row" — true
        # in practice and enforced by nothing: there is no cap on views, and this is
        # what `GET /views/` calls on every table open — so the cost was a
        # `_security_context` walk plus a grant SELECT per view per page load.
        # `effective_actions_bulk` answers the same question in a fixed number of
        # queries; the records router already used it.
        granted = await PermissionService.effective_actions_bulk(
            db, user, [(ResourceType.view, view.uuid) for view in views]
        )
        return [
            view
            for view in views
            if Action.view in granted.get((ResourceType.view, view.uuid), set())
        ]

    @classmethod
    async def create(cls, db: AsyncSession, table: Table, payload: ViewCreate) -> View:
        await cls._validate_config(db, table.id, payload.config)
        view = View(
            table_id=table.id,
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
                "A view with this name already exists on the table"
            ) from exc
        return await cls.get_by_uuid(db, table.id, new_uuid)

    @classmethod
    async def update(
        cls,
        db: AsyncSession,
        table: Table,
        uuid: Union[str, UUID],
        payload: ViewUpdate,
    ) -> Optional[View]:
        view = await cls.get_by_uuid(db, table.id, uuid)
        if view is None:
            return None

        data = payload.model_dump(exclude_unset=True)
        if "config" in data:
            await cls._validate_config(db, table.id, payload.config)
            view.config = payload.config.model_dump(mode="json")
            data.pop("config")
        if data.get("name") is None:
            data.pop("name", None)
        if data.get("display_name") is None:
            data.pop("display_name", None)
        if data.get("position") is None:
            data.pop("position", None)
        if data.get("permissions_inherit") is None:
            data.pop("permissions_inherit", None)

        for field, value in data.items():
            setattr(view, field, value)

        try:
            await db.commit()
        except IntegrityError as exc:
            await db.rollback()
            raise ConflictError(
                "A view with this name already exists on the table"
            ) from exc
        return await cls.get_by_uuid(db, table.id, uuid)

    @classmethod
    async def delete(
        cls, db: AsyncSession, table_id: int, uuid: Union[str, UUID]
    ) -> bool:
        view = await cls.get_by_uuid(db, table_id, uuid)
        if view is None:
            return False

        view.deleted_at = datetime.now(timezone.utc)
        await db.commit()
        return True

    @staticmethod
    async def _validate_config(
        db: AsyncSession, table_id: int, config: ViewConfig
    ) -> None:
        """Ensure every column referenced by the view belongs to the table."""
        referenced: set[UUID] = set()
        referenced.update(c.column_uuid for c in config.filters.conditions)
        referenced.update(s.column_uuid for s in config.sorts)
        referenced.update(config.hidden_columns)
        referenced.update(config.column_order)
        if config.group_by is not None:
            referenced.add(config.group_by.column_uuid)
        if not referenced:
            return

        result = await db.execute(
            select(Column.uuid).where(
                Column.table_id == table_id,
                Column.uuid.in_(referenced),
                in_shape(),
            )
        )
        found = set(result.scalars().all())
        missing = [str(u) for u in referenced if u not in found]
        if missing:
            raise UnknownReferenceError(
                f"View references column(s) not on this table: {', '.join(missing)}"
            )
