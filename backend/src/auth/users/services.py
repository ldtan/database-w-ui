from datetime import datetime, timezone
from typing import Optional, Sequence, Union
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.core.exceptions import ConflictError, UnknownReferenceError

from ..helpers import get_password_hash
from .models import User, UserGroup
from .schemas import UserCreate, UserGroupCreate, UserGroupUpdate, UserUpdate


def _coerce_uuid(value: Union[str, UUID]) -> Optional[UUID]:
    if isinstance(value, UUID):
        return value
    try:
        return UUID(str(value))
    except (ValueError, TypeError):
        return None


class UserService:
    @staticmethod
    def _select():
        return (
            select(User)
            .where(User.deleted_at.is_(None))
            .options(selectinload(User.groups))
        )

    @classmethod
    async def get_by_uuid(cls, db: AsyncSession, uuid: Union[str, UUID]) -> Optional[User]:
        parsed = _coerce_uuid(uuid)
        if parsed is None:
            return None
        result = await db.execute(cls._select().where(User.uuid == parsed))
        return result.scalars().first()

    @classmethod
    async def get_by_username(cls, db: AsyncSession, username: str) -> Optional[User]:
        result = await db.execute(cls._select().where(User.username == username))
        return result.scalars().first()

    @classmethod
    async def get_by_email(cls, db: AsyncSession, email: str) -> Optional[User]:
        result = await db.execute(cls._select().where(User.email == email))
        return result.scalars().first()

    @classmethod
    async def list(cls, db: AsyncSession, skip: int = 0, limit: int = 100) -> Sequence[User]:
        result = await db.execute(
            cls._select().order_by(User.id).offset(skip).limit(limit)
        )
        return result.scalars().all()

    @classmethod
    async def create(cls, db: AsyncSession, payload: UserCreate) -> User:
        user = User(
            username=payload.username,
            email=payload.email,
            password=get_password_hash(payload.password),
            is_active=payload.is_active,
        )
        db.add(user)
        try:
            await db.flush()
            # Read the generated uuid before commit expires the instance.
            new_uuid = user.uuid
            await db.commit()
        except IntegrityError as exc:
            await db.rollback()
            raise ConflictError("Username or email is already in use") from exc

        return await cls.get_by_uuid(db, new_uuid)

    @classmethod
    async def update(
        cls, db: AsyncSession, uuid: Union[str, UUID], payload: UserUpdate
    ) -> Optional[User]:
        user = await cls.get_by_uuid(db, uuid)
        if user is None:
            return None

        data = payload.model_dump(exclude_unset=True, exclude_none=True)
        password = data.pop("password", None)
        if password is not None:
            user.password = get_password_hash(password)
        for field, value in data.items():
            setattr(user, field, value)

        try:
            await db.flush()
            target_uuid = user.uuid
            await db.commit()
        except IntegrityError as exc:
            await db.rollback()
            raise ConflictError("Username or email is already in use") from exc

        return await cls.get_by_uuid(db, target_uuid)

    @classmethod
    async def delete(cls, db: AsyncSession, uuid: Union[str, UUID]) -> bool:
        user = await cls.get_by_uuid(db, uuid)
        if user is None:
            return False

        user.deleted_at = datetime.now(timezone.utc)
        # Drop memberships so the user stops appearing in UserGroupRead.users.
        user.groups.clear()
        await db.commit()
        return True


class UserGroupService:
    @staticmethod
    def _select():
        return (
            select(UserGroup)
            .where(UserGroup.deleted_at.is_(None))
            .options(selectinload(UserGroup.users))
        )

    @staticmethod
    async def _resolve_users(
        db: AsyncSession, user_uuids: Optional[Sequence[Union[str, UUID]]]
    ) -> list[User]:
        if not user_uuids:
            return []

        parsed: list[UUID] = []
        for raw in user_uuids:
            value = _coerce_uuid(raw)
            if value is None:
                raise UnknownReferenceError(f"Malformed user uuid: {raw}")
            parsed.append(value)

        result = await db.execute(
            select(User).where(User.uuid.in_(parsed), User.deleted_at.is_(None))
        )
        users = list(result.scalars().all())

        found = {user.uuid for user in users}
        missing = [str(value) for value in parsed if value not in found]
        if missing:
            raise UnknownReferenceError(f"Unknown user uuid(s): {', '.join(missing)}")
        return users

    @classmethod
    async def get_by_uuid(
        cls, db: AsyncSession, uuid: Union[str, UUID]
    ) -> Optional[UserGroup]:
        parsed = _coerce_uuid(uuid)
        if parsed is None:
            return None
        result = await db.execute(cls._select().where(UserGroup.uuid == parsed))
        return result.scalars().first()

    @classmethod
    async def get_by_name(cls, db: AsyncSession, name: str) -> Optional[UserGroup]:
        result = await db.execute(cls._select().where(UserGroup.name == name))
        return result.scalars().first()

    @classmethod
    async def list(
        cls, db: AsyncSession, skip: int = 0, limit: int = 100
    ) -> Sequence[UserGroup]:
        result = await db.execute(
            cls._select().order_by(UserGroup.id).offset(skip).limit(limit)
        )
        return result.scalars().all()

    @classmethod
    async def create(cls, db: AsyncSession, payload: UserGroupCreate) -> UserGroup:
        users = await cls._resolve_users(db, payload.user_uuids)
        group = UserGroup(
            name=payload.name,
            description=payload.description,
            users=users,
        )
        db.add(group)
        try:
            await db.flush()
            new_uuid = group.uuid
            await db.commit()
        except IntegrityError as exc:
            await db.rollback()
            raise ConflictError("Group name is already in use") from exc

        return await cls.get_by_uuid(db, new_uuid)

    @classmethod
    async def update(
        cls, db: AsyncSession, uuid: Union[str, UUID], payload: UserGroupUpdate
    ) -> Optional[UserGroup]:
        group = await cls.get_by_uuid(db, uuid)
        if group is None:
            return None

        data = payload.model_dump(exclude_unset=True)
        if "user_uuids" in data:
            group.users = await cls._resolve_users(db, data.pop("user_uuids"))
        if "name" in data and data["name"] is None:
            data.pop("name")
        for field, value in data.items():
            setattr(group, field, value)

        try:
            await db.flush()
            target_uuid = group.uuid
            await db.commit()
        except IntegrityError as exc:
            await db.rollback()
            raise ConflictError("Group name is already in use") from exc

        return await cls.get_by_uuid(db, target_uuid)

    @classmethod
    async def delete(cls, db: AsyncSession, uuid: Union[str, UUID]) -> bool:
        group = await cls.get_by_uuid(db, uuid)
        if group is None:
            return False

        group.deleted_at = datetime.now(timezone.utc)
        # Drop memberships so the group stops appearing in UserRead.groups.
        group.users.clear()
        await db.commit()
        return True
