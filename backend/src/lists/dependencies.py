from uuid import UUID

from fastapi import Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.dependencies import get_current_user
from src.auth.users.models import User
from src.infrastructure.database import get_db
from src.permissions.enums import Action, ResourceType
from src.permissions.services import PermissionService

from .models import List
from .services import ListService


async def resolve_list_or_404(
    list_uuid: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> List:
    """Resolve a live list by uuid without a permission check.

    Item routes depend on this because access to an item can be granted directly,
    even when the caller cannot view the list as a whole; each item endpoint then
    enforces the appropriate per-item permission.
    """
    lst = await ListService.get_any_by_uuid(db, list_uuid)
    if lst is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="List not found"
        )
    return lst


async def get_list_or_404(
    list_uuid: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> List:
    """Resolve a list the current user can view, or raise 404/403.

    List-level routers (columns, forms, views, transfer, list reads) depend on this
    so 'view' access is enforced in one place.
    """
    lst = await ListService.get_any_by_uuid(db, list_uuid)
    if lst is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="List not found"
        )
    await PermissionService.require(
        db, current_user, ResourceType.list, lst.id, Action.view
    )
    return lst
