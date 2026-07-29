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

from ..bin import services as bin_services
from ..bin.schemas import BinnedChildRead
from ..dependencies import get_list_or_404
from ..models import List
from .models import Column
from .schemas import (
    ColumnCreate,
    ColumnRead,
    ColumnUpdate,
    LookupOption,
    RestoredColumnRead,
)
from .services import ColumnService


router = APIRouter(prefix="/{list_uuid}/columns", tags=["columns"])


@router.post("/", response_model=ColumnRead, status_code=status.HTTP_201_CREATED)
async def create_column(
    payload: ColumnCreate,
    lst: List = Depends(get_list_or_404),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await PermissionService.require(
        db, current_user, ResourceType.list, lst.id, Action.configure
    )
    try:
        return await ColumnService.create(db, lst, payload, current_user)
    except ConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except (ValidationError, UnknownReferenceError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get("/", response_model=list[ColumnRead])
async def list_columns(
    lst: List = Depends(get_list_or_404),
    db: AsyncSession = Depends(get_db),
):
    return await ColumnService.list(db, lst.id)


# Declared before `/{uuid}`: FastAPI matches on path shape and validates afterwards,
# so a later `/deleted` would be captured here and rejected as a malformed uuid.
@router.get("/deleted", response_model=list[BinnedChildRead])
async def list_deleted_columns(
    response: Response,
    lst: List = Depends(get_list_or_404),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
):
    """The list's binned columns — see src.lists.bin.services for what appears here
    and why this is gated on the list rather than on each column."""
    await PermissionService.require(
        db, current_user, ResourceType.list, lst.id, Action.configure
    )
    response.headers["X-Total-Count"] = str(
        await bin_services.count_deleted(db, Column, lst.id)
    )
    return await bin_services.list_deleted(db, Column, lst.id, skip=skip, limit=limit)


@router.get("/{uuid}", response_model=ColumnRead)
async def get_column(
    uuid: UUID,
    lst: List = Depends(get_list_or_404),
    db: AsyncSession = Depends(get_db),
):
    column = await ColumnService.get_by_uuid(db, lst.id, uuid)
    if column is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Column not found")
    return column


# The path `uuid` is this column; the query `uuid` (bound to `row_uuid` below) is the
# rows to resolve. Two parameters of the same name in different places, which OpenAPI
# keys by name *and* location, so they stay distinct to a generated client.
@router.get("/{uuid}/options", response_model=list[LookupOption])
async def lookup_options(
    uuid: UUID,
    q: Optional[str] = Query(None, description="Match rows by their label"),
    row_uuid: Optional[list[UUID]] = Query(
        None,
        max_length=500,
        alias="uuid",
        description="Resolve specific rows, e.g. the ones a page's cells hold",
    ),
    limit: Optional[int] = Query(
        None,
        ge=1,
        le=500,
        description="Defaults to the number of uuids asked for, else a page of suggestions",
    ),
    parent_value: Optional[list[UUID]] = Query(
        None,
        max_length=50,
        description=(
            "On a nested lookup, offer only rows whose filter cell matches "
            "any of these parent-row uuids; ignored on other columns"
        ),
    ),
    lst: List = Depends(get_list_or_404),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """The rows a lookup column can point at, searched server-side.

    The picker is a typeahead over this, and the grid resolves the uuids it holds
    through it — so neither has to fetch a whole target list, which stops working
    once the target has more rows than the fetch's limit.
    """
    column = await ColumnService.get_by_uuid(db, lst.id, uuid)
    if column is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Column not found")
    try:
        return await ColumnService.lookup_options(
            db,
            lst,
            column,
            current_user,
            q=q,
            uuids=row_uuid,
            limit=limit,
            parent_values=parent_value,
        )
    except ValidationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except UnknownReferenceError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.patch("/{uuid}", response_model=ColumnRead)
async def update_column(
    uuid: UUID,
    payload: ColumnUpdate,
    lst: List = Depends(get_list_or_404),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await PermissionService.require(
        db, current_user, ResourceType.list, lst.id, Action.configure
    )
    column = await ColumnService.get_by_uuid(db, lst.id, uuid)
    if column is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Column not found")
    try:
        column = await ColumnService.update(db, lst, column.uuid, payload, current_user)
    except ConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except (ValidationError, UnknownReferenceError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    if column is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Column not found")
    return column


@router.delete("/{uuid}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_column(
    uuid: UUID,
    lst: List = Depends(get_list_or_404),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await PermissionService.require(
        db, current_user, ResourceType.list, lst.id, Action.configure
    )
    column = await ColumnService.get_by_uuid(db, lst.id, uuid)
    if column is None or not await ColumnService.delete(db, lst, column.uuid):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Column not found")


@router.post("/{uuid}/restore", response_model=RestoredColumnRead)
async def restore_column(
    uuid: UUID,
    lst: List = Depends(get_list_or_404),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Bring a column back, with its values — they were never removed from the records.

    What does *not* come back is what the delete cleared elsewhere: the list's
    `primary_column_id` if it pointed here, and the nesting pairing on any lookup that
    referenced this column. `RestoredColumnRead` explains which of those the response
    can speak to.
    """
    await PermissionService.require(
        db, current_user, ResourceType.list, lst.id, Action.configure
    )
    try:
        restored = await bin_services.restore(db, Column, lst.id, uuid)
    except ConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if restored is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Not found in the bin"
        )
    # Read off the list as it stands rather than off what this column used to be: a
    # primary chosen while this sat in the bin is not something to flag.
    await db.refresh(lst)
    return RestoredColumnRead(
        **ColumnRead.model_validate(restored).model_dump(),
        list_has_no_primary_column=lst.primary_column_id is None,
    )


@router.delete("/{uuid}/permanent", status_code=status.HTTP_204_NO_CONTENT)
async def purge_column(
    uuid: UUID,
    lst: List = Depends(get_list_or_404),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Destroy a binned column **and the values it holds in every record**.

    Only something already in the bin can be purged; a live column 404s here, so
    destroying one is always two deliberate steps. Unlike the other three kinds this
    is not only a config delete — it is the one route that can free record data a
    deleted column left behind.
    """
    await PermissionService.require(
        db, current_user, ResourceType.list, lst.id, Action.configure
    )
    if not await bin_services.purge(db, Column, lst.id, uuid):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Not found in the bin"
        )
