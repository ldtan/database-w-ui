from uuid import UUID

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.dependencies import get_current_user
from src.auth.users.models import User
from src.auth.users.services import UserService
from src.core.exceptions import ConflictError
from src.infrastructure.database import get_db
from src.permissions.enums import Action, ResourceType
from src.permissions.services import PermissionService

from .schemas import (
    DeletedWorkspaceRead,
    WorkspaceCreate,
    WorkspaceRead,
    WorkspaceTransfer,
    WorkspaceUpdate,
)
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
    response: Response,
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    q: Optional[str] = Query(None, description="Match name or display name"),
    shared: Optional[bool] = Query(
        None,
        description=(
            "True for workspaces shared with the caller, False for the ones they "
            "own. Omit for both."
        ),
    ),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Searching and paging server-side; the total match count (ignoring skip/limit)
    comes back in X-Total-Count for the paginator, leaving the body a plain array.

    ``shared`` is omitted by default, so a caller that does not ask keeps getting
    owned and shared workspaces together, as this endpoint always has."""
    response.headers["X-Total-Count"] = str(
        await WorkspaceService.count_visible(db, current_user, q=q, shared=shared)
    )
    return await WorkspaceService.list_visible(
        db, current_user, skip=skip, limit=limit, q=q, shared=shared
    )


# Declared before GET /{workspace_uuid} so "deleted" is not parsed as a uuid. The
# bin is owner-scoped: a soft-deleted workspace resolves to no permission context
# (see PermissionService._security_context), so there is no grant to check a
# non-owner against — restoring a whole workspace is the owner's call.
@router.get("/deleted", response_model=list[DeletedWorkspaceRead])
async def list_deleted_workspaces(
    response: Response,
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    response.headers["X-Total-Count"] = str(
        await WorkspaceService.count_deleted_for_owner(db, current_user.id)
    )
    return await WorkspaceService.list_deleted_for_owner(
        db, current_user.id, skip=skip, limit=limit
    )


@router.get("/{workspace_uuid}", response_model=WorkspaceRead)
async def get_workspace(
    workspace_uuid: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    workspace = await WorkspaceService.get_any_by_uuid(db, workspace_uuid)
    if workspace is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")
    await PermissionService.require(
        db, current_user, ResourceType.workspace, workspace.id, Action.view
    )
    return workspace


@router.patch("/{workspace_uuid}", response_model=WorkspaceRead)
async def update_workspace(
    workspace_uuid: UUID,
    payload: WorkspaceUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    workspace = await WorkspaceService.get_any_by_uuid(db, workspace_uuid)
    if workspace is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")
    await PermissionService.require(
        db, current_user, ResourceType.workspace, workspace.id, Action.configure
    )
    try:
        workspace = await WorkspaceService.update(db, workspace_uuid, payload)
    except ConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    if workspace is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")
    return workspace


@router.post("/{workspace_uuid}/transfer", response_model=WorkspaceRead)
async def transfer_workspace(
    workspace_uuid: UUID,
    payload: WorkspaceTransfer,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Hand a workspace, and every list in it, to another user.

    Owner-only, on the same reasoning as the list transfer: ownership is what is being
    given away, so the only thing that can authorize it is holding it — a `manage`
    grant does not qualify. Unlike a list there is no inbox involved; a workspace *is*
    the ownership boundary, so the row simply changes hands and its lists follow.
    Existing shares survive; the giver's implicit owner access ends with the transfer
    (see WorkspaceService.transfer_to_user).
    """
    workspace = await WorkspaceService.get_any_by_uuid(db, workspace_uuid)
    if workspace is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found"
        )
    if workspace.owner_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the workspace's owner can transfer it",
        )

    recipient = await UserService.get_by_uuid(db, payload.user_uuid)
    if recipient is None or not recipient.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="user_uuid does not reference an active user",
        )
    if recipient.id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot transfer a workspace to yourself",
        )

    transferred = await WorkspaceService.transfer_to_user(
        db, workspace_uuid, recipient
    )
    if transferred is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found"
        )
    return transferred


@router.delete("/{workspace_uuid}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_workspace(
    workspace_uuid: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    workspace = await WorkspaceService.get_any_by_uuid(db, workspace_uuid)
    if workspace is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")
    await PermissionService.require(
        db, current_user, ResourceType.workspace, workspace.id, Action.delete
    )
    await WorkspaceService.delete(db, workspace_uuid)


@router.post("/{workspace_uuid}/restore", response_model=WorkspaceRead)
async def restore_workspace(
    workspace_uuid: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # Owner-only: a deleted workspace has no resolvable permission context, so the
    # owner is the only principal it can be acted on by. Restore also revives the
    # lists that went down with it (see WorkspaceService.restore).
    workspace = await WorkspaceService.get_deleted_by_uuid(db, workspace_uuid)
    if workspace is None or workspace.owner_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found"
        )
    try:
        restored = await WorkspaceService.restore(db, workspace_uuid)
    except ConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if restored is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found"
        )
    return restored


@router.delete(
    "/{workspace_uuid}/permanent", status_code=status.HTTP_204_NO_CONTENT
)
async def purge_workspace(
    workspace_uuid: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # Owner-only, like restore: a deleted workspace has no resolvable permission
    # context. Only a binned workspace can be purged; a live one 404s here.
    workspace = await WorkspaceService.get_deleted_by_uuid(db, workspace_uuid)
    if workspace is None or workspace.owner_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found"
        )
    await WorkspaceService.purge(db, workspace_uuid)
