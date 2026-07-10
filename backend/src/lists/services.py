from datetime import datetime, timezone
from typing import Optional, Sequence, Union
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.core.exceptions import ConflictError, UnknownReferenceError
from src.core.utils import coerce_uuid
from src.workspaces.models import Workspace
from src.workspaces.services import WorkspaceService

from .columns.models import Column
from .forms.enums import FormType
from .forms.models import Form
from .models import List
from .schemas import ListCreate, ListUpdate
from .views.models import View
from .views.schemas import ViewConfig


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
    async def list(
        cls,
        db: AsyncSession,
        owner_id: int,
        workspace_uuid: Optional[Union[str, UUID]] = None,
        skip: int = 0,
        limit: int = 100,
    ) -> Sequence[List]:
        stmt = cls._select(owner_id)
        if workspace_uuid is not None:
            parsed = coerce_uuid(workspace_uuid)
            stmt = stmt.where(Workspace.uuid == parsed)
        result = await db.execute(stmt.order_by(List.id).offset(skip).limit(limit))
        return result.scalars().all()

    @classmethod
    async def create(
        cls, db: AsyncSession, owner_id: int, payload: ListCreate
    ) -> List:
        workspace = await WorkspaceService.get_by_uuid(
            db, owner_id, payload.workspace_uuid
        )
        if workspace is None:
            raise UnknownReferenceError(
                "workspace_uuid does not reference a workspace you own"
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

        return await cls.get_by_uuid(db, owner_id, new_uuid)

    @classmethod
    async def update(
        cls,
        db: AsyncSession,
        owner_id: int,
        uuid: Union[str, UUID],
        payload: ListUpdate,
    ) -> Optional[List]:
        lst = await cls.get_by_uuid(db, owner_id, uuid)
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
                workspace = await WorkspaceService.get_by_uuid(db, owner_id, target_uuid)
                if workspace is None:
                    raise UnknownReferenceError(
                        "workspace_uuid does not reference a workspace you own"
                    )
                lst.workspace_id = workspace.id

        # Treat an explicit null name as "leave unchanged" rather than clearing it.
        if data.get("name") is None:
            data.pop("name", None)
        if data.get("display_name") is None:
            data.pop("display_name", None)

        for field, value in data.items():
            setattr(lst, field, value)

        try:
            await db.flush()
            target_uuid = lst.uuid
            await db.commit()
        except IntegrityError as exc:
            await db.rollback()
            raise ConflictError("A list with this name already exists") from exc

        return await cls.get_by_uuid(db, owner_id, target_uuid)

    @classmethod
    async def delete(
        cls, db: AsyncSession, owner_id: int, uuid: Union[str, UUID]
    ) -> bool:
        lst = await cls.get_by_uuid(db, owner_id, uuid)
        if lst is None:
            return False

        lst.deleted_at = datetime.now(timezone.utc)
        await db.commit()
        return True

    @staticmethod
    def _provision_defaults(db: AsyncSession, lst: List) -> None:
        """Seed a new list with a default grid view and create/update forms.

        Forms start with an empty ``fields`` list, which the convention treats as
        "all columns" until the user customizes the layout.
        """
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
