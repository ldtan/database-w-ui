from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.dependencies import get_current_user
from src.auth.users.models import User
from src.core.exceptions import ConflictError, UnknownReferenceError, ValidationError
from src.infrastructure.database import get_db
from src.permissions.enums import Action, ResourceType
from src.permissions.services import PermissionService

from ..dependencies import filter_param, resolve_list_or_404
from ..models import List
from .schemas import (
    BulkDeleteRequest,
    BulkDeleteResult,
    DeletedListItemRead,
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
        return await ListItemService.create(db, lst, payload, user=current_user)
    except ConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except (ValidationError, UnknownReferenceError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get("/", response_model=list[ListItemRead])
async def list_items(
    response: Response,
    lst: List = Depends(resolve_list_or_404),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    view_uuid: Optional[str] = Query(
        None, description="The view to apply, by uuid or by name"
    ),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    q: Optional[str] = Query(None, description="Match any column's text"),
    sort_by: Optional[str] = Query(
        None,
        description=(
            "Column uuid to order by; replaces the view's saved sorts. Empty means "
            "no order at all, as distinct from omitting it"
        ),
    ),
    sort_dir: str = Query("asc", pattern="^(asc|desc)$"),
    group_by: Optional[str] = Query(
        None,
        description=(
            "Column uuid to group by; replaces the view's saved grouping. Empty "
            "means no grouping, as distinct from omitting it"
        ),
    ),
    filters: Optional[dict] = Depends(filter_param),
):
    """Filtering, grouping, searching, ordering and paging, all server-side — so a
    page past the first is a page of the *list* rather than of whatever the browser
    happened to hold.

    A saved view supplies the filters, sorts and grouping. Every one of them can
    also be given per request, in which case it stands in for what the view saved:
    that is how someone reads a list a particular way without saving that way to the
    list, and without needing `configure` on a view to do it. Omitting a parameter
    and passing it empty therefore mean different things — the first defers to the
    view, the second overrules it with nothing.

    The total number of matches (ignoring skip/limit, honouring everything else)
    comes back in X-Total-Count, which is what a paginator needs; the body stays a
    plain array, like every other listing here.
    """
    # "" is how a caller says "no grouping" as opposed to "whatever the view says";
    # an empty dict is falsy, so `apply_sorts` skips it, but it is not None, so it
    # still displaces the view's.
    grouping = None if group_by is None else ({"column_uuid": group_by} if group_by else {})
    response.headers["X-Total-Count"] = str(
        await ListItemService.count(
            db, lst, current_user, view_uuid=view_uuid, q=q, filters=filters
        )
    )
    return await ListItemService.list(
        db,
        lst,
        current_user,
        view_uuid=view_uuid,
        skip=skip,
        limit=limit,
        q=q,
        sort_by=sort_by,
        sort_dir=sort_dir,
        filters=filters,
        group_by=grouping,
    )


# Declared before GET /{uuid} so "deleted" is not parsed as a uuid. The bin is a
# recovery surface, so it is gated on list-level `delete` rather than per-record:
# a soft-deleted item resolves to no permission context (see PermissionService.
# _security_context), so there is nothing to check per record. The workspace owner
# always qualifies via the owner short-circuit.
@router.get("/deleted", response_model=list[DeletedListItemRead])
async def list_deleted_items(
    response: Response,
    lst: List = Depends(resolve_list_or_404),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
):
    await PermissionService.require(
        db, current_user, ResourceType.list, lst.id, Action.delete
    )
    response.headers["X-Total-Count"] = str(
        await ListItemService.count_deleted(db, lst)
    )
    return await ListItemService.list_deleted(db, lst, skip=skip, limit=limit)


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
        item = await ListItemService.update(db, lst, uuid, payload, user=current_user)
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


@router.post("/{uuid}/restore", response_model=ListItemRead)
async def restore_item(
    uuid: UUID,
    lst: List = Depends(resolve_list_or_404),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # Gated on list-level `delete`, like the bin listing: a soft-deleted record has
    # no per-item permission context to check against.
    await PermissionService.require(
        db, current_user, ResourceType.list, lst.id, Action.delete
    )
    try:
        item = await ListItemService.restore(db, lst, uuid)
    except ConflictError as exc:
        # A unique value was taken while the record sat in the bin.
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Item not found")
    return item


@router.delete("/{uuid}/permanent", status_code=status.HTTP_204_NO_CONTENT)
async def purge_item(
    uuid: UUID,
    lst: List = Depends(resolve_list_or_404),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # Gated on list-level `delete`, like the bin listing and restore. Only a record
    # already in the bin can be purged; a live one 404s here (soft-delete it first).
    await PermissionService.require(
        db, current_user, ResourceType.list, lst.id, Action.delete
    )
    if not await ListItemService.purge(db, lst, uuid):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Item not found")
