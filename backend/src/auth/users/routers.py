from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from ..dependencies import get_current_user, require_superuser
from . import bin as bin_services
from .batch.routers import router as batch_router
from .models import User, UserGroup
from .schemas import (
    BinnedPrincipalRead,
    UserCreate,
    UserGroupCreate,
    UserGroupRead,
    UserGroupUpdate,
    UserRead,
    UserSettingsUpdate,
    UserUpdate,
)
from .services import UserGroupService, UserService
from src.core.exceptions import (
    ConflictError,
    PermissionDeniedError,
    UnknownReferenceError,
)
from src.infrastructure.database import get_db


# Reads stay open to any authenticated user: the Share dialog tables users and
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

# Included here, before the /{uuid} routes below are registered, so the literal
# /import path is matched ahead of the parameterised ones -- the convention
# list_deleted_users documents. Nothing actually collides today (the only other POSTs
# under /users are "/" and "/{uuid}/restore"), which is the point: it stays true when
# the next one is added.
router.include_router(batch_router)


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
    uuid: Optional[list[UUID]] = Query(
        None,
        max_length=500,
        description="Resolve specific accounts, e.g. the ones a page's `user` cells hold",
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
        await UserService.count(
            db, q=q, search_person_names=search_names, uuids=uuid
        )
    )
    return await UserService.list(
        db,
        skip=skip,
        limit=limit,
        q=q,
        search_person_names=search_names,
        uuids=uuid,
    )


# Declared before /{uuid} so "me" is not parsed as a uuid. The same goes for the
# /me/... routes below.
@router.get("/me", response_model=UserRead)
async def get_me(current_user: User = Depends(get_current_user)):
    """Who the caller is. The frontend has only a token otherwise, so it needs this
    to know whether to show the account-administration UI."""
    return current_user


@router.patch("/me/settings", response_model=UserRead)
async def update_my_settings(
    payload: UserSettingsUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Save UI preferences against the account so they follow the user to any device.

    Self-only by construction — it takes no uuid — so none of the authorization in
    `update_user` applies. Returns the whole user rather than just the settings so
    the caller can drop the response straight into its cached `me`.
    """
    user = await UserService.update_settings(db, current_user.uuid, payload)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return user


# Declared before `/{uuid}`: FastAPI matches on path shape and validates afterwards,
# so a later `/deleted` would be captured there and rejected as a malformed uuid.
@router.get("/deleted", response_model=list[BinnedPrincipalRead])
async def list_deleted_users(
    response: Response,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_superuser),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
):
    """Accounts still inside the retention window — see src.auth.users.bin for why
    this is superuser-only rather than gated on the data the account could reach."""
    response.headers["X-Total-Count"] = str(
        await bin_services.count_deleted(db, User)
    )
    rows = await bin_services.list_deleted(db, User, skip=skip, limit=limit)
    return [
        BinnedPrincipalRead(
            uuid=row.uuid,
            name=row.username,
            detail=row.email,
            deleted_at=row.deleted_at,
        )
        for row in rows
    ]


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


@router.post("/{uuid}/restore", response_model=UserRead)
async def restore_user(
    uuid: UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_superuser),
):
    """Bring a deleted account back, group memberships and all.

    A real undo: the account keeps its uuid, so every `user` cell naming it resolves
    again on its own, and its memberships were never thrown away — reads filtered them
    while it was gone. See src.auth.users.bin.
    """
    restored = await bin_services.restore(db, User, uuid)
    if restored is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Not found in the bin"
        )
    return restored


@router.delete("/{uuid}/permanent", status_code=status.HTTP_204_NO_CONTENT)
async def purge_user(
    uuid: UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_superuser),
):
    """Destroy a binned account for good, with its grants, profile and tokens.

    Only something already in the bin can be purged, so this is always two deliberate
    steps. **409 while the account still owns workspaces** — `workspaces.owner_id` is
    a non-null foreign key, so the alternative to refusing is taking that data down
    with the account. Reassign or delete them first; the workspace bin is where a
    deleted one is dealt with.
    """
    try:
        destroyed = await bin_services.purge(db, User, uuid)
    except ConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if not destroyed:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Not found in the bin"
        )


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
    except PermissionDeniedError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except UnknownReferenceError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@group_router.get("/", response_model=list[UserGroupRead])
async def list_groups(
    response: Response,
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    q: Optional[str] = Query(None, description="Match name or description"),
    uuid: Optional[list[UUID]] = Query(
        None,
        max_length=500,
        description="Resolve specific groups, e.g. the ones a page's `group` cells hold",
    ),
    db: AsyncSession = Depends(get_db),
):
    response.headers["X-Total-Count"] = str(
        await UserGroupService.count(db, q=q, uuids=uuid)
    )
    return await UserGroupService.list(db, skip=skip, limit=limit, q=q, uuids=uuid)


# Before `/{uuid}`, for the reason the users listing above gives.
@group_router.get("/deleted", response_model=list[BinnedPrincipalRead])
async def list_deleted_groups(
    response: Response,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_superuser),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
):
    response.headers["X-Total-Count"] = str(
        await bin_services.count_deleted(db, UserGroup)
    )
    rows = await bin_services.list_deleted(db, UserGroup, skip=skip, limit=limit)
    return [
        BinnedPrincipalRead(
            uuid=row.uuid,
            name=row.display_name,
            detail=row.description,
            deleted_at=row.deleted_at,
        )
        for row in rows
    ]


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
    except PermissionDeniedError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
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
    try:
        if not await UserGroupService.delete(db, uuid):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
    except PermissionDeniedError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc


@group_router.post("/{uuid}/restore", response_model=UserGroupRead)
async def restore_group(
    uuid: UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_superuser),
):
    """Bring a deleted group back, members and all.

    **This is the sharp edge of the bin and the reason it is superuser-only.** The
    group keeps its id, so every grant naming it applies again the moment it is live —
    and its membership survived the delete, so those grants reach the same people they
    did before. Restoring a group is handing back access, not just a row.
    """
    restored = await bin_services.restore(db, UserGroup, uuid)
    if restored is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Not found in the bin"
        )
    return restored


@group_router.delete("/{uuid}/permanent", status_code=status.HTTP_204_NO_CONTENT)
async def purge_group(
    uuid: UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_superuser),
):
    """Destroy a binned group for good, with its grants and membership rows.

    Nothing blocks this the way owned workspaces block an account: a group is only
    ever a principal, never an owner.
    """
    if not await bin_services.purge(db, UserGroup, uuid):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Not found in the bin"
        )
