from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.dependencies import get_current_user
from src.auth.users.models import User
from src.core.exceptions import ConflictError, UnknownReferenceError
from src.infrastructure.database import get_db
from src.permissions.enums import Action, ResourceType
from src.permissions.services import PermissionService

from ..bin import services as bin_services
from ..bin.schemas import BinnedChildRead
from ..dependencies import get_list_or_404
from ..models import List
from .models import View
from .schemas import ViewCreate, ViewRead, ViewUpdate
from .services import ViewService


router = APIRouter(prefix="/{list_uuid}/views", tags=["views"])


async def _visible_or_404(
    db: AsyncSession, current_user: User, lst: List, uuid: UUID
) -> View:
    """The view with this uuid, or 404 — including when it exists but is not the
    caller's to see.

    A 403 would answer "a view with this uuid exists" to anyone who can reach the
    list, and a view can be restricted further than its list is. A uuid is not
    guessable, so that admission buys an attacker nothing; the 404 stays because
    `list_views` already omits the views the caller cannot see, and one route
    calling a view absent while another calls it forbidden is a distinction that
    only ever confuses.
    """
    view = await ViewService.get_by_uuid(db, lst.id, uuid)
    if view is None or not await PermissionService.has(
        db, current_user, ResourceType.view, view.id, Action.view
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="View not found")
    return view


@router.post("/", response_model=ViewRead, status_code=status.HTTP_201_CREATED)
async def create_view(
    payload: ViewCreate,
    lst: List = Depends(get_list_or_404),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await PermissionService.require(
        db, current_user, ResourceType.list, lst.id, Action.configure
    )
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
    current_user: User = Depends(get_current_user),
):
    return await ViewService.list(db, lst.id, current_user)


# Declared before `/{uuid}`: FastAPI matches on path shape and validates afterwards,
# so a later `/deleted` would be captured here and rejected as a malformed uuid.
@router.get("/deleted", response_model=list[BinnedChildRead])
async def list_deleted_views(
    response: Response,
    lst: List = Depends(get_list_or_404),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
):
    """The list's binned views — see src.lists.bin.services for what appears here
    and why this is gated on the list rather than on each view."""
    await PermissionService.require(
        db, current_user, ResourceType.list, lst.id, Action.configure
    )
    response.headers["X-Total-Count"] = str(
        await bin_services.count_deleted(db, View, lst.id)
    )
    return await bin_services.list_deleted(db, View, lst.id, skip=skip, limit=limit)


@router.get("/{uuid}", response_model=ViewRead)
async def get_view(
    uuid: UUID,
    lst: List = Depends(get_list_or_404),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await _visible_or_404(db, current_user, lst, uuid)


@router.patch("/{uuid}", response_model=ViewRead)
async def update_view(
    uuid: UUID,
    payload: ViewUpdate,
    lst: List = Depends(get_list_or_404),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    view = await _visible_or_404(db, current_user, lst, uuid)
    await PermissionService.require(
        db, current_user, ResourceType.view, view.id, Action.configure
    )
    if payload.permissions_inherit is not None:
        await PermissionService.require(
            db, current_user, ResourceType.view, view.id, Action.share
        )
    try:
        view = await ViewService.update(db, lst, view.uuid, payload)
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
    current_user: User = Depends(get_current_user),
):
    view = await _visible_or_404(db, current_user, lst, uuid)
    await PermissionService.require(
        db, current_user, ResourceType.view, view.id, Action.configure
    )
    await ViewService.delete(db, lst.id, view.uuid)


@router.post("/{uuid}/restore", response_model=ViewRead)
async def restore_view(
    uuid: UUID,
    lst: List = Depends(get_list_or_404),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await PermissionService.require(
        db, current_user, ResourceType.list, lst.id, Action.configure
    )
    try:
        restored = await bin_services.restore(db, View, lst.id, uuid)
    except ConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if restored is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Not found in the bin"
        )
    return restored


@router.delete("/{uuid}/permanent", status_code=status.HTTP_204_NO_CONTENT)
async def purge_view(
    uuid: UUID,
    lst: List = Depends(get_list_or_404),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # Only something already in the bin can be purged; a live view 404s here, so
    # destroying one is always two deliberate steps. Same rule as records and lists.
    await PermissionService.require(
        db, current_user, ResourceType.list, lst.id, Action.configure
    )
    if not await bin_services.purge(db, View, lst.id, uuid):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Not found in the bin"
        )
