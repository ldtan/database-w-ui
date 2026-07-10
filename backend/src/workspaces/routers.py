from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.dependencies import get_current_user
from src.auth.users.models import User
from src.core.exceptions import ConflictError
from src.infrastructure.database import get_db

from .schemas import WorkspaceCreate, WorkspaceRead, WorkspaceUpdate
from .services import WorkspaceService


router = APIRouter(
    prefix="/workspaces",
    tags=["workspaces"],
    dependencies=[Depends(get_current_user)],
)


@router.post("/", response_model=WorkspaceRead, status_code=status.HTTP_201_CREATED)
async def create_workspace(
    payload: WorkspaceCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        return await WorkspaceService.create(db, current_user.id, payload)
    except ConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.get("/", response_model=list[WorkspaceRead])
async def list_workspaces(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await WorkspaceService.list(db, current_user.id, skip=skip, limit=limit)


@router.get("/{workspace_uuid}", response_model=WorkspaceRead)
async def get_workspace(
    workspace_uuid: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    workspace = await WorkspaceService.get_by_uuid(db, current_user.id, workspace_uuid)
    if workspace is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")
    return workspace


@router.patch("/{workspace_uuid}", response_model=WorkspaceRead)
async def update_workspace(
    workspace_uuid: UUID,
    payload: WorkspaceUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        workspace = await WorkspaceService.update(db, current_user.id, workspace_uuid, payload)
    except ConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    if workspace is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")
    return workspace


@router.delete("/{workspace_uuid}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_workspace(
    workspace_uuid: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if not await WorkspaceService.delete(db, current_user.id, workspace_uuid):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")
