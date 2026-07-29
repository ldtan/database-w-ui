from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.dependencies import get_current_user
from src.auth.users.models import User
from src.core.exceptions import ConflictError, UnknownReferenceError, ValidationError
from src.infrastructure.database import get_db
from src.permissions.enums import Action, ResourceType
from src.permissions.services import PermissionService

from ..dependencies import get_list_or_404
from ..models import List
from .schemas import ColumnCreate, ColumnRead, ColumnUpdate, LookupOption
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
