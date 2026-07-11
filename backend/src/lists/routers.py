from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.dependencies import get_current_user
from src.auth.users.models import User
from src.core.exceptions import ConflictError, UnknownReferenceError
from src.infrastructure.database import get_db

from .schemas import ListCreate, ListRead, ListUpdate
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
    try:
        return await ListService.create(db, current_user.id, payload)
    except ConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.get("/", response_model=list[ListRead])
async def list_lists(
    workspace_uuid: Optional[UUID] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await ListService.list(
        db, current_user.id, workspace_uuid=workspace_uuid, skip=skip, limit=limit
    )


@router.get("/{list_uuid}", response_model=ListRead)
async def get_list(
    list_uuid: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    lst = await ListService.get_by_uuid(db, current_user.id, list_uuid)
    if lst is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="List not found")
    return lst


@router.patch("/{list_uuid}", response_model=ListRead)
async def update_list(
    list_uuid: UUID,
    payload: ListUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        lst = await ListService.update(db, current_user.id, list_uuid, payload)
    except ConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except UnknownReferenceError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    if lst is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="List not found")
    return lst


@router.delete("/{list_uuid}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_list(
    list_uuid: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if not await ListService.delete(db, current_user.id, list_uuid):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="List not found")


# Child resources are scoped under a single list: /lists/{list_uuid}/<child>
router.include_router(columns_router)
router.include_router(items_router)
router.include_router(views_router)
router.include_router(forms_router)
router.include_router(transfer_router)
