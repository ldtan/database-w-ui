from datetime import datetime, timezone
from typing import Optional, Sequence, Union
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions import ConflictError
from src.core.utils import coerce_uuid

from .models import Workspace
from .schemas import WorkspaceCreate, WorkspaceUpdate


class WorkspaceService:
    @staticmethod
    def _select(owner_id: int):
        return select(Workspace).where(
            Workspace.deleted_at.is_(None), Workspace.owner_id == owner_id
        )

    @classmethod
    async def get_by_uuid(
        cls, db: AsyncSession, owner_id: int, uuid: Union[str, UUID]
    ) -> Optional[Workspace]:
        parsed = coerce_uuid(uuid)
        if parsed is None:
            return None
        result = await db.execute(cls._select(owner_id).where(Workspace.uuid == parsed))
        return result.scalars().first()

    @classmethod
    async def list(
        cls, db: AsyncSession, owner_id: int, skip: int = 0, limit: int = 100
    ) -> Sequence[Workspace]:
        result = await db.execute(
            cls._select(owner_id).order_by(Workspace.id).offset(skip).limit(limit)
        )
        return result.scalars().all()

    @classmethod
    async def create(
        cls, db: AsyncSession, owner_id: int, payload: WorkspaceCreate
    ) -> Workspace:
        workspace = Workspace(
            owner_id=owner_id,
            name=payload.name,
            display_name=payload.display_name,
            description=payload.description,
            settings=payload.settings or {},
        )
        db.add(workspace)
        try:
            await db.flush()
            new_uuid = workspace.uuid
            await db.commit()
        except IntegrityError as exc:
            await db.rollback()
            raise ConflictError("A workspace with this name already exists") from exc

        return await cls.get_by_uuid(db, owner_id, new_uuid)

    @classmethod
    async def create_default(cls, db: AsyncSession, owner_id: int) -> Workspace:
        """Ensure a user has a workspace so they can create lists immediately.

        Called when a user is created. Idempotent: if the user already has a
        workspace, the first one is returned instead of creating another.
        """
        existing = await cls.list(db, owner_id, limit=1)
        if existing:
            return existing[0]
        return await cls.create(
            db,
            owner_id,
            WorkspaceCreate(
                name="personal",
                display_name="Personal",
                description="Your personal workspace",
            ),
        )

    @classmethod
    async def update(
        cls,
        db: AsyncSession,
        owner_id: int,
        uuid: Union[str, UUID],
        payload: WorkspaceUpdate,
    ) -> Optional[Workspace]:
        workspace = await cls.get_by_uuid(db, owner_id, uuid)
        if workspace is None:
            return None

        data = payload.model_dump(exclude_unset=True)
        if data.get("name") is None:
            data.pop("name", None)
        if data.get("display_name") is None:
            data.pop("display_name", None)

        for field, value in data.items():
            setattr(workspace, field, value)

        try:
            await db.flush()
            target_uuid = workspace.uuid
            await db.commit()
        except IntegrityError as exc:
            await db.rollback()
            raise ConflictError("A workspace with this name already exists") from exc

        return await cls.get_by_uuid(db, owner_id, target_uuid)

    @classmethod
    async def delete(
        cls, db: AsyncSession, owner_id: int, uuid: Union[str, UUID]
    ) -> bool:
        workspace = await cls.get_by_uuid(db, owner_id, uuid)
        if workspace is None:
            return False

        workspace.deleted_at = datetime.now(timezone.utc)
        await db.commit()
        return True
