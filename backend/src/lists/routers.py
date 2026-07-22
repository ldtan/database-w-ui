from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.dependencies import get_current_user
from src.auth.users.models import User
from src.core.exceptions import ConflictError, UnknownReferenceError
from src.infrastructure.database import get_db
from src.permissions.enums import Action, ResourceType
from src.permissions.services import PermissionService
from src.workspaces.services import WorkspaceService

from .dependencies import get_list_or_404
from .models import List
from .schemas import DeletedListRead, ListCreate, ListRead, ListUpdate
from .services import ListService

from .columns.routers import router as columns_router
from .items.routers import router as items_router
from .views.routers import router as views_router
from .forms.routers import router as forms_router
from .transfer.routers import router as transfer_router


router = APIRouter(
    prefix="/lists",
    tags=["lists"],
    dependencies=[Depends(get_current_user)],
)


@router.post("/", response_model=ListRead, status_code=status.HTTP_201_CREATED)
async def create_list(
    payload: ListCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # Creating a list is a configuration change on its target workspace.
    workspace = await WorkspaceService.get_any_by_uuid(db, payload.workspace_uuid)
    if workspace is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="workspace_uuid does not reference a workspace",
        )
    await PermissionService.require(
        db, current_user, ResourceType.workspace, workspace.id, Action.configure
    )
    try:
        return await ListService.create(db, payload)
    except ConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.get("/", response_model=list[ListRead])
async def list_lists(
    response: Response,
    workspace_uuid: Optional[UUID] = Query(None),
    shared: bool = Query(False, description="Only lists the caller does not own"),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    q: Optional[str] = Query(None, description="Match list name or display name"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Searching and paging server-side, so a search finds lists past the first page.

    The total number of matches (ignoring skip/limit) comes back in X-Total-Count,
    which is what a paginator needs; the body stays a plain array, so callers that
    just want the list (the nav drawer, the column picker) are unaffected.
    """
    response.headers["X-Total-Count"] = str(
        await ListService.count(
            db, current_user, workspace_uuid=workspace_uuid, shared_only=shared, q=q
        )
    )
    return await ListService.list(
        db,
        current_user,
        workspace_uuid=workspace_uuid,
        shared_only=shared,
        skip=skip,
        limit=limit,
        q=q,
    )


# Declared before GET /{list_uuid} so "deleted" is not parsed as a uuid. Scoped to
# one workspace and gated on that workspace's `delete`: a soft-deleted list resolves
# to no permission context (see PermissionService._security_context), so the check
# has to sit on the live parent — the same reason the records bin is gated on its
# list rather than the record.
@router.get("/deleted", response_model=list[DeletedListRead])
async def list_deleted_lists(
    response: Response,
    workspace_uuid: UUID = Query(..., description="The workspace whose bin to read"),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    workspace = await WorkspaceService.get_any_by_uuid(db, workspace_uuid)
    if workspace is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found"
        )
    await PermissionService.require(
        db, current_user, ResourceType.workspace, workspace.id, Action.delete
    )
    response.headers["X-Total-Count"] = str(
        await ListService.count_deleted_for_workspace(db, workspace.id)
    )
    return await ListService.list_deleted_for_workspace(
        db, workspace.id, skip=skip, limit=limit
    )


@router.get("/{list_uuid}", response_model=ListRead)
async def get_list(lst: List = Depends(get_list_or_404)):
    return lst


@router.patch("/{list_uuid}", response_model=ListRead)
async def update_list(
    payload: ListUpdate,
    lst: List = Depends(get_list_or_404),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # Editing list settings requires 'configure'; toggling inheritance requires 'share'.
    await PermissionService.require(
        db, current_user, ResourceType.list, lst.id, Action.configure
    )
    if payload.permissions_inherit is not None:
        await PermissionService.require(
            db, current_user, ResourceType.list, lst.id, Action.share
        )
    if payload.workspace_uuid is not None:
        target = await WorkspaceService.get_any_by_uuid(db, payload.workspace_uuid)
        if target is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="workspace_uuid does not reference a workspace",
            )
        await PermissionService.require(
            db, current_user, ResourceType.workspace, target.id, Action.configure
        )
    try:
        updated = await ListService.update(db, lst.uuid, payload)
    except ConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except UnknownReferenceError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    if updated is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="List not found")
    return updated


@router.delete("/{list_uuid}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_list(
    lst: List = Depends(get_list_or_404),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await PermissionService.require(
        db, current_user, ResourceType.list, lst.id, Action.delete
    )
    await ListService.delete(db, lst.uuid)


@router.post("/{list_uuid}/restore", response_model=ListRead)
async def restore_list(
    list_uuid: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # Gated on the parent workspace's `delete`, like the bin listing: the list itself
    # is deleted and resolves to no permission context. Restore also revives the
    # list's columns, records, views and forms (see ListService.restore).
    lst = await ListService.get_deleted_by_uuid(db, list_uuid)
    if lst is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="List not found"
        )
    await PermissionService.require(
        db, current_user, ResourceType.workspace, lst.workspace_id, Action.delete
    )
    try:
        restored = await ListService.restore(db, list_uuid)
    except ConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if restored is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="List not found"
        )
    return restored


@router.delete("/{list_uuid}/permanent", status_code=status.HTTP_204_NO_CONTENT)
async def purge_list(
    list_uuid: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # Gated on the parent workspace's `delete`, like the bin and restore. Only a list
    # already in the bin can be purged; a live one 404s here (soft-delete it first).
    lst = await ListService.get_deleted_by_uuid(db, list_uuid)
    if lst is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="List not found"
        )
    await PermissionService.require(
        db, current_user, ResourceType.workspace, lst.workspace_id, Action.delete
    )
    await ListService.purge(db, list_uuid)


# Child resources are scoped under a single list: /lists/{list_uuid}/<child>
router.include_router(columns_router)
router.include_router(items_router)
router.include_router(views_router)
router.include_router(forms_router)
router.include_router(transfer_router)
