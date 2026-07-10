from uuid import UUID

from fastapi import Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.dependencies import get_current_user
from src.auth.users.models import User
from src.infrastructure.database import get_db

from .models import Workspace
from .services import WorkspaceService


async def get_workspace_or_404(
    workspace_uuid: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Workspace:
    """Resolve a workspace owned by the current user, or raise 404."""
    workspace = await WorkspaceService.get_by_uuid(db, current_user.id, workspace_uuid)
    if workspace is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found"
        )
    return workspace
