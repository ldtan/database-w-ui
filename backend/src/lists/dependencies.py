from uuid import UUID

from fastapi import Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.dependencies import get_current_user
from src.auth.users.models import User
from src.infrastructure.database import get_db

from .models import List
from .services import ListService


async def get_list_or_404(
    list_uuid: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> List:
    """Resolve a list owned by the current user, or raise 404.

    Child routers depend on this so ownership is enforced in one place.
    """
    lst = await ListService.get_by_uuid(db, current_user.id, list_uuid)
    if lst is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="List not found"
        )
    return lst
