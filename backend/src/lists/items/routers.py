from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.dependencies import get_current_user
from src.auth.users.models import User
from src.core.exceptions import ConflictError, UnknownReferenceError, ValidationError
from src.infrastructure.database import get_db
from src.permissions.enums import Action, ResourceType
from src.permissions.services import PermissionService

from ..dependencies import resolve_list_or_404
from ..models import List
from .schemas import (
    BulkDeleteRequest,
    BulkDeleteResult,
    ItemQuery,
    ListItemCreate,
    ListItemRead,
    ListItemUpdate,
)
from .services import ListItemService


# Item access can be granted directly, independent of the list, so these routes
# resolve the list without a list-level view check and enforce per-item instead.
router = APIRouter(prefix="/{list_uuid}/items", tags=["items"])


@router.post("/", response_model=ListItemRead, status_code=status.HTTP_201_CREATED)
async def create_item(
    payload: ListItemCreate,
    lst: List = Depends(resolve_list_or_404),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await PermissionService.require(
        db, current_user, ResourceType.list, lst.id, Action.create
    )
    try:
        return await ListItemService.create(db, lst, payload)
    except ConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except (ValidationError, UnknownReferenceError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get("/", response_model=list[ListItemRead])
async def list_items(
    lst: List = Depends(resolve_list_or_404),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    view_uuid: Optional[UUID] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    q: Optional[str] = Query(None, description="Match any column's text"),
):
    return await ListItemService.list(
        db, lst, current_user, view_uuid=view_uuid, skip=skip, limit=limit, q=q
    )


@router.post("/bulk-delete", response_model=BulkDeleteResult)
async def bulk_delete_items(
    payload: BulkDeleteRequest,
    lst: List = Depends(resolve_list_or_404),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # Only delete the records the caller is actually allowed to delete; the rest
    # are silently skipped (like unknown uuids).
    permitted = []
    for uuid in payload.uuids:
        item = await ListItemService.get_by_uuid(db, lst.id, uuid)
        if item is None:
            continue
        if await PermissionService.has(
            db, current_user, ResourceType.item, item.id, Action.delete
        ):
            permitted.append(uuid)
    deleted = await ListItemService.bulk_delete(db, lst, permitted)
    return {"deleted": deleted}


@router.post("/query", response_model=list[ListItemRead])
async def query_items(
    payload: ItemQuery,
    lst: List = Depends(resolve_list_or_404),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await ListItemService.query(
        db,
        lst,
        current_user,
        filters=payload.filters.model_dump(),
        sorts=[s.model_dump() for s in payload.sorts],
        skip=payload.skip,
        limit=payload.limit,
    )


@router.get("/{uuid}", response_model=ListItemRead)
async def get_item(
    uuid: UUID,
    lst: List = Depends(resolve_list_or_404),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # get_for_read, not get_by_uuid: this record is about to be rendered, so its
    # values come back in the shape their columns declare (see `coerce_shape`).
    item = await ListItemService.get_for_read(db, lst.id, uuid)
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Item not found")
    await PermissionService.require(
        db, current_user, ResourceType.item, item.id, Action.view
    )
    return item


@router.patch("/{uuid}", response_model=ListItemRead)
async def update_item(
    uuid: UUID,
    payload: ListItemUpdate,
    lst: List = Depends(resolve_list_or_404),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    item = await ListItemService.get_by_uuid(db, lst.id, uuid)
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Item not found")
    await PermissionService.require(
        db, current_user, ResourceType.item, item.id, Action.edit
    )
    if payload.permissions_inherit is not None:
        await PermissionService.require(
            db, current_user, ResourceType.item, item.id, Action.share
        )
    try:
        item = await ListItemService.update(db, lst, uuid, payload)
    except ConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except (ValidationError, UnknownReferenceError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Item not found")
    return item


@router.delete("/{uuid}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_item(
    uuid: UUID,
    lst: List = Depends(resolve_list_or_404),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    item = await ListItemService.get_by_uuid(db, lst.id, uuid)
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Item not found")
    await PermissionService.require(
        db, current_user, ResourceType.item, item.id, Action.delete
    )
    await ListItemService.delete(db, lst, uuid)
