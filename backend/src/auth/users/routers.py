from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from ..dependencies import get_current_user
from .schemas import (
    UserCreate,
    UserGroupCreate,
    UserGroupRead,
    UserGroupUpdate,
    UserRead,
    UserUpdate,
)
from .services import UserGroupService, UserService
from src.core.exceptions import ConflictError, UnknownReferenceError
from src.infrastructure.database import get_db


router = APIRouter(
    prefix="/users",
    tags=["users"],
    dependencies=[Depends(get_current_user)],
)

group_router = APIRouter(
    prefix="/groups",
    tags=["groups"],
    dependencies=[Depends(get_current_user)],
)


@router.post("/", response_model=UserRead, status_code=status.HTTP_201_CREATED)
async def create_user(
    payload: UserCreate,
    db: AsyncSession = Depends(get_db),
):
    try:
        return await UserService.create(db, payload)
    except ConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.get("/", response_model=list[UserRead])
async def list_users(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
):
    return await UserService.list(db, skip=skip, limit=limit)


@router.get("/{uuid}", response_model=UserRead)
async def get_user(
    uuid: UUID,
    db: AsyncSession = Depends(get_db),
):
    user = await UserService.get_by_uuid(db, uuid)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return user


@router.patch("/{uuid}", response_model=UserRead)
async def update_user(
    uuid: UUID,
    payload: UserUpdate,
    db: AsyncSession = Depends(get_db),
):
    try:
        user = await UserService.update(db, uuid, payload)
    except ConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return user


@router.delete("/{uuid}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    uuid: UUID,
    db: AsyncSession = Depends(get_db),
):
    if not await UserService.delete(db, uuid):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")


@group_router.post("/", response_model=UserGroupRead, status_code=status.HTTP_201_CREATED)
async def create_group(
    payload: UserGroupCreate,
    db: AsyncSession = Depends(get_db),
):
    try:
        return await UserGroupService.create(db, payload)
    except ConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except UnknownReferenceError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@group_router.get("/", response_model=list[UserGroupRead])
async def list_groups(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
):
    return await UserGroupService.list(db, skip=skip, limit=limit)


@group_router.get("/{uuid}", response_model=UserGroupRead)
async def get_group(
    uuid: UUID,
    db: AsyncSession = Depends(get_db),
):
    group = await UserGroupService.get_by_uuid(db, uuid)
    if group is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
    return group


@group_router.patch("/{uuid}", response_model=UserGroupRead)
async def update_group(
    uuid: UUID,
    payload: UserGroupUpdate,
    db: AsyncSession = Depends(get_db),
):
    try:
        group = await UserGroupService.update(db, uuid, payload)
    except ConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except UnknownReferenceError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    if group is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
    return group


@group_router.delete("/{uuid}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_group(
    uuid: UUID,
    db: AsyncSession = Depends(get_db),
):
    if not await UserGroupService.delete(db, uuid):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
