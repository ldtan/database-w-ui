import logging
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import settings
from src.core.exceptions import ConflictError
from .users.models import (
    AUTHENTICATED_USERS_DISPLAY_NAME,
    AUTHENTICATED_USERS_NAME,
    User,
    UserGroup,
)
from .users.schemas import UserCreate
from .users.services import UserService


logger = logging.getLogger(__name__)


async def ensure_system_groups(db: AsyncSession) -> None:
    """Idempotently create the virtual groups the resolver relies on.

    Currently just "Authenticated Users": every signed-in account is implicitly a
    member (it never gets rows in user_group_association), so a grant to it reaches
    everyone — see PermissionService._group_ids. Seeded here at startup rather than
    in a migration, matching how the first user is bootstrapped.
    """
    existing = await db.scalar(
        select(UserGroup.id).where(UserGroup.name == AUTHENTICATED_USERS_NAME)
    )
    if existing is not None:
        return

    db.add(
        UserGroup(
            name=AUTHENTICATED_USERS_NAME,
            display_name=AUTHENTICATED_USERS_DISPLAY_NAME,
            description="Everyone signed in. Share with this group to grant all users.",
            is_system=True,
        )
    )
    try:
        await db.commit()
        logger.info("Seeded system group %r", AUTHENTICATED_USERS_NAME)
    except IntegrityError:
        # Another worker won the race between the check and the insert.
        await db.rollback()


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

    # The bootstrap account is the only superuser the system ever creates on its
    # own; every later one has to be promoted by an existing superuser.
    user.is_superuser = True
    await db.commit()

    logger.info("Created first user %r (superuser)", user.username)
    return user
