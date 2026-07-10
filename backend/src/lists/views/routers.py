from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions import ConflictError, UnknownReferenceError
from src.infrastructure.database import get_db

from ..dependencies import get_list_or_404
from ..models import List
from .schemas import ViewCreate, ViewRead, ViewUpdate
from .services import ViewService


router = APIRouter(prefix="/{list_uuid}/views", tags=["views"])


@router.post("/", response_model=ViewRead, status_code=status.HTTP_201_CREATED)
async def create_view(
    payload: ViewCreate,
    lst: List = Depends(get_list_or_404),
    db: AsyncSession = Depends(get_db),
):
    try:
        return await ViewService.create(db, lst, payload)
    except ConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except UnknownReferenceError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get("/", response_model=list[ViewRead])
async def list_views(
    lst: List = Depends(get_list_or_404),
    db: AsyncSession = Depends(get_db),
):
    return await ViewService.list(db, lst.id)


@router.get("/{uuid}", response_model=ViewRead)
async def get_view(
    uuid: UUID,
    lst: List = Depends(get_list_or_404),
    db: AsyncSession = Depends(get_db),
):
    view = await ViewService.get_by_uuid(db, lst.id, uuid)
    if view is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="View not found")
    return view


@router.patch("/{uuid}", response_model=ViewRead)
async def update_view(
    uuid: UUID,
    payload: ViewUpdate,
    lst: List = Depends(get_list_or_404),
    db: AsyncSession = Depends(get_db),
):
    try:
        view = await ViewService.update(db, lst, uuid, payload)
    except ConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except UnknownReferenceError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    if view is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="View not found")
    return view


@router.delete("/{uuid}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_view(
    uuid: UUID,
    lst: List = Depends(get_list_or_404),
    db: AsyncSession = Depends(get_db),
):
    if not await ViewService.delete(db, lst.id, uuid):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="View not found")
