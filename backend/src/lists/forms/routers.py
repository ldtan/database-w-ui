from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions import ConflictError, UnknownReferenceError
from src.infrastructure.database import get_db

from ..dependencies import get_list_or_404
from ..models import List
from .schemas import FormCreate, FormRead, FormUpdate
from .services import FormService


router = APIRouter(prefix="/{list_uuid}/forms", tags=["forms"])


@router.post("/", response_model=FormRead, status_code=status.HTTP_201_CREATED)
async def create_form(
    payload: FormCreate,
    lst: List = Depends(get_list_or_404),
    db: AsyncSession = Depends(get_db),
):
    try:
        return await FormService.create(db, lst, payload)
    except ConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except UnknownReferenceError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get("/", response_model=list[FormRead])
async def list_forms(
    lst: List = Depends(get_list_or_404),
    db: AsyncSession = Depends(get_db),
):
    return await FormService.list(db, lst.id)


@router.get("/{uuid}", response_model=FormRead)
async def get_form(
    uuid: UUID,
    lst: List = Depends(get_list_or_404),
    db: AsyncSession = Depends(get_db),
):
    form = await FormService.get_by_uuid(db, lst.id, uuid)
    if form is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Form not found")
    return form


@router.patch("/{uuid}", response_model=FormRead)
async def update_form(
    uuid: UUID,
    payload: FormUpdate,
    lst: List = Depends(get_list_or_404),
    db: AsyncSession = Depends(get_db),
):
    try:
        form = await FormService.update(db, lst, uuid, payload)
    except ConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except UnknownReferenceError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    if form is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Form not found")
    return form


@router.delete("/{uuid}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_form(
    uuid: UUID,
    lst: List = Depends(get_list_or_404),
    db: AsyncSession = Depends(get_db),
):
    if not await FormService.delete(db, lst.id, uuid):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Form not found")
