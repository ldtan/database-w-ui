from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions import ConflictError, UnknownReferenceError, ValidationError
from src.infrastructure.database import get_db

from ..dependencies import get_list_or_404
from ..models import List
from .schemas import ItemQuery, ListItemCreate, ListItemRead, ListItemUpdate
from .services import ListItemService


router = APIRouter(prefix="/{list_uuid}/items", tags=["items"])


@router.post("/", response_model=ListItemRead, status_code=status.HTTP_201_CREATED)
async def create_item(
    payload: ListItemCreate,
    lst: List = Depends(get_list_or_404),
    db: AsyncSession = Depends(get_db),
):
    try:
        return await ListItemService.create(db, lst, payload)
    except ConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except (ValidationError, UnknownReferenceError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get("/", response_model=list[ListItemRead])
async def list_items(
    lst: List = Depends(get_list_or_404),
    db: AsyncSession = Depends(get_db),
    view_uuid: Optional[UUID] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
):
    return await ListItemService.list(
        db, lst, view_uuid=view_uuid, skip=skip, limit=limit
    )


@router.post("/query", response_model=list[ListItemRead])
async def query_items(
    payload: ItemQuery,
    lst: List = Depends(get_list_or_404),
    db: AsyncSession = Depends(get_db),
):
    return await ListItemService.query(
        db,
        lst,
        filters=payload.filters.model_dump(),
        sorts=[s.model_dump() for s in payload.sorts],
        skip=payload.skip,
        limit=payload.limit,
    )


@router.get("/{uuid}", response_model=ListItemRead)
async def get_item(
    uuid: UUID,
    lst: List = Depends(get_list_or_404),
    db: AsyncSession = Depends(get_db),
):
    item = await ListItemService.get_by_uuid(db, lst.id, uuid)
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Item not found")
    return item


@router.patch("/{uuid}", response_model=ListItemRead)
async def update_item(
    uuid: UUID,
    payload: ListItemUpdate,
    lst: List = Depends(get_list_or_404),
    db: AsyncSession = Depends(get_db),
):
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
    lst: List = Depends(get_list_or_404),
    db: AsyncSession = Depends(get_db),
):
    if not await ListItemService.delete(db, lst, uuid):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Item not found")
