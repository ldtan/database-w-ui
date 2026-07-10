import logging
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import settings
from src.core.exceptions import ConflictError
from .users.models import User
from .users.schemas import UserCreate
from .users.services import UserService


logger = logging.getLogger(__name__)


async def seed_initial_data(db: AsyncSession) -> Optional[User]:
    """Create the bootstrap user when the users table is empty.

    Every /users route is guarded, so without this there is no way to create
    the first account. Soft-deleted rows count as existing: they still hold
    the unique username and email, so re-seeding over them would fail.
    """
    total = await db.scalar(select(func.count()).select_from(User))
    if total:
        return None

    if settings.FIRST_SUPERUSER_PASSWORD == "changeme" and not settings.DEBUG:
        logger.warning(
            "Seeding the first user with the default password. "
            "Set FIRST_SUPERUSER_PASSWORD before running in production."
        )

    try:
        user = await UserService.create(
            db,
            UserCreate(
                username=settings.FIRST_SUPERUSER_USERNAME,
                email=settings.FIRST_SUPERUSER_EMAIL,
                password=settings.FIRST_SUPERUSER_PASSWORD,
            ),
        )
    except ConflictError:
        # Another worker won the race between the count and the insert.
        logger.info("First user already created by another process")
        return None

    logger.info("Created first user %r", user.username)
    return user
