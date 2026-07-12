from datetime import datetime, timezone
from typing import Optional, Sequence, Union
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.core.exceptions import ConflictError
from src.core.utils import LIKE_ESCAPE, coerce_uuid, like_pattern

from .models import Person
from .schemas import PersonCreate, PersonUpdate

# Required columns: an explicit null means "leave unchanged", not "blank it out".
_REQUIRED = ("first_name", "last_name")


class PersonService:
    @staticmethod
    def _select():
        return (
            select(Person)
            .where(Person.deleted_at.is_(None))
            .options(selectinload(Person.user))
        )

    @classmethod
    async def get_by_uuid(
        cls, db: AsyncSession, uuid: Union[str, UUID]
    ) -> Optional[Person]:
        parsed = coerce_uuid(uuid)
        if parsed is None:
            return None
        result = await db.execute(cls._select().where(Person.uuid == parsed))
        return result.scalars().first()

    @classmethod
    async def get_by_user_id(cls, db: AsyncSession, user_id: int) -> Optional[Person]:
        result = await db.execute(cls._select().where(Person.user_id == user_id))
        return result.scalars().first()

    @classmethod
    async def list(
        cls,
        db: AsyncSession,
        skip: int = 0,
        limit: int = 100,
        q: Optional[str] = None,
    ) -> Sequence[Person]:
        stmt = cls._select()
        if q and q.strip():
            # Safe to search names here: the route is superuser-only.
            pattern = like_pattern(q.strip())
            stmt = stmt.where(
                or_(
                    Person.first_name.ilike(pattern, escape=LIKE_ESCAPE),
                    Person.middle_name.ilike(pattern, escape=LIKE_ESCAPE),
                    Person.last_name.ilike(pattern, escape=LIKE_ESCAPE),
                )
            )
        result = await db.execute(
            stmt.order_by(Person.id).offset(skip).limit(limit)
        )
        return result.scalars().all()

    @classmethod
    async def create(
        cls, db: AsyncSession, user_id: int, payload: PersonCreate
    ) -> Person:
        """The router resolves user_uuid to user_id and enforces who may create
        a profile for whom."""
        person = Person(
            user_id=user_id,
            first_name=payload.first_name,
            middle_name=payload.middle_name,
            last_name=payload.last_name,
            birth_date=payload.birth_date,
        )
        db.add(person)
        try:
            await db.flush()
            new_uuid = person.uuid
            await db.commit()
        except IntegrityError as exc:
            await db.rollback()
            raise ConflictError("This user already has a person record") from exc

        return await cls.get_by_uuid(db, new_uuid)

    @classmethod
    async def update(
        cls, db: AsyncSession, uuid: Union[str, UUID], payload: PersonUpdate
    ) -> Optional[Person]:
        person = await cls.get_by_uuid(db, uuid)
        if person is None:
            return None

        # exclude_unset (not exclude_none) so an explicit null clears a nullable
        # field — omitting middle_name leaves it alone, sending null blanks it.
        data = payload.model_dump(exclude_unset=True)
        for field in _REQUIRED:
            if field in data and data[field] is None:
                data.pop(field)

        for field, value in data.items():
            setattr(person, field, value)

        await db.flush()
        target_uuid = person.uuid
        await db.commit()
        return await cls.get_by_uuid(db, target_uuid)

    @classmethod
    async def delete(cls, db: AsyncSession, uuid: Union[str, UUID]) -> bool:
        person = await cls.get_by_uuid(db, uuid)
        if person is None:
            return False
        person.deleted_at = datetime.now(timezone.utc)
        await db.commit()
        return True
