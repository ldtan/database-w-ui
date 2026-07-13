from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.dependencies import get_current_user
from src.auth.users.models import User
from src.core.exceptions import ConflictError, UnknownReferenceError
from src.infrastructure.database import get_db
from src.permissions.enums import Action, ResourceType
from src.permissions.services import PermissionService

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
    current_user: User = Depends(get_current_user),
):
    await PermissionService.require(
        db, current_user, ResourceType.list, lst.id, Action.configure
    )
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
    current_user: User = Depends(get_current_user),
):
    return await FormService.list(db, lst.id, current_user)


@router.get("/{ref}", response_model=FormRead)
async def get_form(
    ref: str,
    lst: List = Depends(get_list_or_404),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    form = await FormService.get_by_ref(db, lst.id, ref)
    if form is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Form not found")
    await PermissionService.require(
        db, current_user, ResourceType.form, form.id, Action.view
    )
    return form


@router.patch("/{ref}", response_model=FormRead)
async def update_form(
    ref: str,
    payload: FormUpdate,
    lst: List = Depends(get_list_or_404),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    form = await FormService.get_by_ref(db, lst.id, ref)
    if form is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Form not found")
    await PermissionService.require(
        db, current_user, ResourceType.form, form.id, Action.configure
    )
    if payload.permissions_inherit is not None:
        await PermissionService.require(
            db, current_user, ResourceType.form, form.id, Action.share
        )
    try:
        form = await FormService.update(db, lst, form.uuid, payload)
    except ConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except UnknownReferenceError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    if form is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Form not found")
    return form


@router.delete("/{ref}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_form(
    ref: str,
    lst: List = Depends(get_list_or_404),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    form = await FormService.get_by_ref(db, lst.id, ref)
    if form is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Form not found")
    await PermissionService.require(
        db, current_user, ResourceType.form, form.id, Action.configure
    )
    await FormService.delete(db, lst.id, form.uuid)
