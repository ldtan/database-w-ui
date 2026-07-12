from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from ..dependencies import get_current_user, require_superuser
from .models import User
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


# Reads stay open to any authenticated user: the Share dialog lists users and
# groups to grant to, so gating them would break sharing. Writes are superuser-only
# — accounts and group membership sit *under* the permission model (groups are
# principals in the grant table), so whoever can edit them can route around it.
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
    _: User = Depends(require_superuser),
):
    """Administrative account creation. Public self-signup, if it is ever added,
    belongs on its own unauthenticated route with its own narrower schema — not
    here, where the caller can set fields like is_active."""
    try:
        return await UserService.create(db, payload)
    except ConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.get("/", response_model=list[UserRead])
async def list_users(
    response: Response,
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    q: Optional[str] = Query(
        None, description="Match username or email (and person name, for superusers)"
    ),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Searching and paging server-side, so a search finds users past the first page.

    The total number of matches (ignoring skip/limit) comes back in X-Total-Count,
    which is what a paginator needs. Keeping it in a header leaves the body a plain
    array, so callers that just want the list are unaffected.

    Person names are only searchable by a superuser — they are private, and this
    route is open to any authenticated user.
    """
    search_names = current_user.is_superuser
    response.headers["X-Total-Count"] = str(
        await UserService.count(db, q=q, search_person_names=search_names)
    )
    return await UserService.list(
        db, skip=skip, limit=limit, q=q, search_person_names=search_names
    )


# Declared before /{uuid} so "me" is not parsed as a uuid.
@router.get("/me", response_model=UserRead)
async def get_me(current_user: User = Depends(get_current_user)):
    """Who the caller is. The frontend has only a token otherwise, so it needs this
    to know whether to show the account-administration UI."""
    return current_user


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
    current_user: User = Depends(get_current_user),
):
    is_self = current_user.uuid == uuid
    if not (is_self or current_user.is_superuser):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You may only modify your own account",
        )
    # Otherwise any account could promote itself to superuser.
    if payload.is_superuser is not None and not current_user.is_superuser:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only a superuser may change superuser status",
        )
    try:
        # Changing your own password means proving you know it; a superuser resetting
        # someone else's cannot be expected to.
        user = await UserService.update(
            db, uuid, payload, verify_current_password=is_self
        )
    except ConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return user


@router.delete("/{uuid}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    uuid: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if not (current_user.uuid == uuid or current_user.is_superuser):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You may only delete your own account",
        )
    if not await UserService.delete(db, uuid):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")


@group_router.post("/", response_model=UserGroupRead, status_code=status.HTTP_201_CREATED)
async def create_group(
    payload: UserGroupCreate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_superuser),
):
    try:
        return await UserGroupService.create(db, payload)
    except ConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except UnknownReferenceError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@group_router.get("/", response_model=list[UserGroupRead])
async def list_groups(
    response: Response,
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    q: Optional[str] = Query(None, description="Match name or description"),
    db: AsyncSession = Depends(get_db),
):
    response.headers["X-Total-Count"] = str(await UserGroupService.count(db, q=q))
    return await UserGroupService.list(db, skip=skip, limit=limit, q=q)


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
    _: User = Depends(require_superuser),
):
    # Membership is the sharp edge: groups are principals in the grant table, so
    # whoever can edit this can hand themselves any grant the group holds.
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
    _: User = Depends(require_superuser),
):
    if not await UserGroupService.delete(db, uuid):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
