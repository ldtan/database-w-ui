from typing import Optional
from uuid import UUID

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    HTTPException,
    Query,
    Response,
    status,
)
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.dependencies import get_current_user
from src.auth.users.models import User
from src.core.config import settings
from src.core.exceptions import ConflictError, UnknownReferenceError, ValidationError
from src.infrastructure.database import get_db
from src.permissions.enums import Action, ResourceType
from src.permissions.services import PermissionService

from ..bin import services as bin_services
from ..bin.schemas import BinnedChildRead
from ..dependencies import get_list_or_404
from ..models import List
from .backfill import (
    count_records,
    recompute_locked,
    run_deferred,
    try_lock,
    unlock,
)
from .models import Column
from .conversion_service import ConversionService
from .schemas import (
    ColumnCreate,
    ColumnRead,
    ColumnUpdate,
    ConversionReport,
    ConversionRequest,
    LookupOption,
    RecomputeReport,
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


@router.post("/recompute", response_model=RecomputeReport)
async def recompute_calculated_columns(
    background: BackgroundTasks,
    lst: List = Depends(get_list_or_404),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Rebuild the stored index behind this list's calculated columns.

    **This does not change any value anybody can see.** A calculated cell is recomputed
    on every read, so the grid, a record dialog and the export are already right. What
    it rebuilds is the copy stored in the record, which is what `apply_filters`,
    `apply_sorts` and `_apply_search` read — those run in Postgres, over the stored
    bytes, and so answer for the formula the index was built from. Editing a formula,
    or renaming or deleting a column one reads, leaves that behind until this runs.

    Scoped to the list rather than one column because the columns are not independent:
    a formula may read another calculated column, so the chain is rebuilt as a whole
    (`chain.build_chain`) and re-indexing half of it would be meaningless.

    Under `FORMULA_INLINE_MAX_RECORDS` the work happens here and the response is sent
    once it is done — measured at ~0.035 ms of app CPU per record, that is under two
    seconds of wall time at the threshold even on a 0.1-CPU instance. Over it the work
    is handed to a background task and this answers immediately with `deferred: true`.
    The same total work either way; the threshold only decides who is kept waiting.

    **One run per list.** A second call while one is in flight gets a 409 rather than a
    second scan of the same rows: the index is shared list state converging on one
    answer, so the run already going is doing the caller's work too. That is also the
    answer for a second *person* — the lock is keyed on the list, not on who asked.
    """
    await PermissionService.require(
        db, current_user, ResourceType.list, lst.id, Action.configure
    )
    records = await count_records(db, lst.id)

    if records > settings.FORMULA_INLINE_MAX_RECORDS:
        # Claim the lock before answering, not inside the task: a caller told "started"
        # and then silently skipped for a lock it never held would have no way to know.
        # The task re-locks on its own session and no-ops if that fails, which is the
        # backstop rather than the mechanism.
        if not await try_lock(db, lst.id):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="A refresh is already running for this list",
            )
        await unlock(db, lst.id)
        background.add_task(run_deferred, lst.id)
        return RecomputeReport(records=records, deferred=True, changed=None)

    changed = await recompute_locked(db, lst.id)
    if changed is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A refresh is already running for this list",
        )
    return RecomputeReport(records=records, deferred=False, changed=changed)


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


async def _column_or_404(db, lst: List, uuid: UUID) -> Column:
    column = await ColumnService.get_by_uuid(db, lst.id, uuid)
    if column is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Column not found")
    return column


@router.post("/{uuid}/convert", response_model=ConversionReport)
async def preview_conversion(
    uuid: UUID,
    payload: ConversionRequest,
    lst: List = Depends(get_list_or_404),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Report what converting this column would do. **Writes nothing.**

    A dry run: every cell is converted in memory and counted, and the report says
    what did and did not come through. No column is created, no value is stored and
    nothing is reserved -- so walking away costs nothing, and there is no in-flight
    state left to block the next attempt.

    Apply it by POSTing the same body, with the `config` this returns, to
    `convert/apply`. The two calls deliberately share no server-side handle: that
    is what stops a record written between them from being reverted or blanked.

    Two endings, chosen here by whether a `name` is supplied:

    - **Without a name -- a conversion.** Applying retypes *this* column, keeping
      its uuid, so nothing referring to it needs changing, and the original values
      go to the bin as `<name>__before_conversion`.
    - **With a name -- a duplicate.** Applying adds the new column beside this one
      and leaves this one untouched. The type may be the same as the source's,
      which is a straight copy.
    """
    await PermissionService.require(
        db, current_user, ResourceType.list, lst.id, Action.configure
    )
    column = await _column_or_404(db, lst, uuid)
    try:
        return await ConversionService.preview(
            db,
            lst,
            column,
            payload.type,
            payload.config,
            current_user,
            name=payload.name,
            display_name=payload.display_name,
        )
    except ConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except (ValidationError, UnknownReferenceError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/{uuid}/convert/apply", response_model=ColumnRead)
async def apply_conversion(
    uuid: UUID,
    payload: ConversionRequest,
    lst: List = Depends(get_list_or_404),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Do it. The point of no return, and a single transaction.

    Reads, converts, writes and retypes in one go, so there is never a moment when
    the column's declared type and its stored values disagree, and no window in
    which a record somebody else writes can be reverted or blanked. For a
    conversion the original values land in a binned column named
    `<name>__before_conversion`, restorable for the usual retention window; for a
    **duplicate** the source is untouched.

    Send the `config` the preview returned rather than the one you sent it. For
    `text` -> `choice` the preview mints the options from the source's distinct
    values, and re-deriving them here would quietly answer a different question
    than the one that was on screen.

    Two ways this refuses, both leaving the list exactly as it was. `required` and
    `unique` are checked once the values are in place -- per cell they would be the
    wrong question, since a blank source cell is ordinary. And if any record on this
    list was written while the conversion was running, it refuses rather than finish
    over the top of it; nothing was committed, so running it again is safe.
    """
    await PermissionService.require(
        db, current_user, ResourceType.list, lst.id, Action.configure
    )
    column = await _column_or_404(db, lst, uuid)
    try:
        return await ConversionService.apply(
            db,
            lst,
            column,
            payload.type,
            payload.config,
            current_user,
            name=payload.name,
            display_name=payload.display_name,
        )
    except ConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except (ValidationError, UnknownReferenceError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


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
    """Bring a column back, with its values — they were hidden, never removed.

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
