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
from ..transfer.schemas import BulkCreateRequest, ImportSummary
from ..transfer.services import import_records
from .schemas import (
    ArchivedListItemRead,
    BulkArchiveRequest,
    BulkArchiveResult,
    BulkDeleteRequest,
    BulkDeleteResult,
    DeletedListItemRead,
    ListItemCountRead,
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
    after: Optional[UUID] = Query(
        None,
        description=(
            "Page from the record with this uuid rather than by offset — the last "
            "one you already have. Costs the same at any depth. Needs the default "
            "order, so it cannot be combined with sorting, grouping or skip"
        ),
    ),
    total: bool = Query(
        True,
        description=(
            "Whether to compute X-Total-Count. Pass false when the total is already "
            "known — it is the same number for every page of one query, and it costs "
            "a scan of every matching record to produce"
        ),
    ),
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
    plain array, like every other listing here. `total=false` suppresses it, and
    `GET items/count` answers it on its own — the total is the same number for
    every page of one query, so a caller that turns pages can ask once instead of
    once per page. See that endpoint for what it costs.

    Two ways to ask for a page. `skip` is what a numbered paginator needs, since
    page 7 is an offset and nothing else; its cost grows with depth. `after` names
    the last record you already have and costs the same wherever it lands, which is
    what a "load more" or an API client walking the whole list wants — see
    `ListItemService._after_id` for the measurements and for why it is confined to
    the default order.
    """
    # "" is how a caller says "no grouping" as opposed to "whatever the view says";
    # an empty dict is falsy, so `apply_sorts` skips it, but it is not None, so it
    # still displaces the view's.
    grouping = None if group_by is None else ({"column_uuid": group_by} if group_by else {})
    # The page before the total, so a request `after` has already ruled out does not
    # pay for a count nothing will read. Counting is the expensive half of this
    # endpoint once the page is fetched by cursor — measured flat at ~13 ms against
    # a 100k list, where the page itself is ~4 ms.
    try:
        items = await ListItemService.list(
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
            after=after,
        )
    except ValidationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    if total:
        response.headers["X-Total-Count"] = str(
            await ListItemService.count(
                db, lst, current_user, view_uuid=view_uuid, q=q, filters=filters
            )
        )
    return items


# Declared before GET /{uuid} so "count" is not parsed as a uuid.
@router.get("/count", response_model=ListItemCountRead)
async def count_items(
    lst: List = Depends(resolve_list_or_404),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    view_uuid: Optional[str] = Query(None, description="The view to apply"),
    q: Optional[str] = Query(None, description="Match any column's text"),
    filters: Optional[dict] = Depends(filter_param),
):
    """How many records match — the same number `GET items/` puts in X-Total-Count.

    Separate because it is the same number for every page of one query, and it is
    not cheap: counting means visiting every matching record, so it scales with the
    list rather than with the page. Measured against a list of 100,000, the count is
    12 ms where the page beside it is 4 ms; under a search it is 268 ms against a
    page of 501 ms. Asked once per query instead of once per page, that is most of
    the cost of turning a page.

    It takes only the parameters that change *which* records match — the view, the
    search, the filter. The page, the order and the grouping are all absent by
    design: none of them changes how many records there are, so including them would
    invite a caller to re-ask a question whose answer cannot have moved.
    """
    return {
        "total": await ListItemService.count(
            db, lst, current_user, view_uuid=view_uuid, q=q, filters=filters
        )
    }


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
    #
    # Resolved for the whole batch at once. Asking per uuid meant a query to find
    # the record and two more for the caller's group membership and grants, every
    # time round: three statements a record, where five in total now cover any batch.
    # `effective_actions_bulk` loads the records in one query, which also warms the
    # identity map so each chain walk is served from memory, and reads groups and
    # grants once. It omits unknown and deleted resources, so they fall out of
    # `permitted` on their own, as before. It resolves item uuids without regard to
    # which list they belong to; `bulk_delete` is what confines the delete to this
    # one, so a uuid from elsewhere is still skipped.
    granted = await PermissionService.effective_actions_bulk(
        db,
        current_user,
        [(ResourceType.item, uuid) for uuid in payload.uuids],
    )
    permitted = [
        uuid for (_, uuid), actions in granted.items() if Action.delete in actions
    ]
    deleted = await ListItemService.bulk_delete(db, lst, permitted)
    return {"deleted": deleted}


@router.get("/archived", response_model=list[ArchivedListItemRead])
async def list_archived_items(
    response: Response,
    lst: List = Depends(resolve_list_or_404),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
):
    # Gated on list-level `delete`, matching the bin. An archived record still has a
    # permission context of its own, unlike a soft-deleted one — but archiving is
    # what takes a record away from everybody who can read the list, so the authority
    # to see the archive is the authority to have put things in it.
    await PermissionService.require(
        db, current_user, ResourceType.list, lst.id, Action.delete
    )
    response.headers["X-Total-Count"] = str(
        await ListItemService.count_archived(db, lst)
    )
    return await ListItemService.list_archived(db, lst, skip=skip, limit=limit)


@router.post("/bulk-archive", response_model=BulkArchiveResult)
async def bulk_archive_items(
    payload: BulkArchiveRequest,
    lst: List = Depends(resolve_list_or_404),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # Same shape as `bulk_delete_items`: resolve the whole batch's permissions in one
    # go, archive only what the caller may delete, and let `bulk_archive` confine the
    # update to this list so a uuid from elsewhere is skipped like an unknown one.
    granted = await PermissionService.effective_actions_bulk(
        db,
        current_user,
        [(ResourceType.item, uuid) for uuid in payload.uuids],
    )
    permitted = [
        uuid for (_, uuid), actions in granted.items() if Action.delete in actions
    ]
    archived = await ListItemService.bulk_archive(db, lst, permitted)
    return {"archived": archived}


@router.post("/bulk-create", response_model=ImportSummary)
async def bulk_create_items(
    payload: BulkCreateRequest,
    lst: List = Depends(resolve_list_or_404),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Create many records from one JSON body. Rows are keyed by column **name**.

    The import path without the file. `POST /lists/{uuid}/import` already accepts
    JSON, but only wrapped in a multipart upload — so a script with records in hand
    had to either synthesise a CSV or loop `POST /items/`, one request and several
    round trips per record. This is for that caller; the UI keeps uploading files.

    It is a thin router over `import_records` on purpose, not a second
    implementation. Everything a file gets, a body gets: per-type coercion
    (`1:30` as a duration, `a;b` split for a `multiple` column), lookup and
    principal cells resolved from *labels* under this caller's permissions, the
    per-row savepoint so one bad row never sinks the batch, and the periodic
    commit. Two parsers for one vocabulary is how the export and import drifted
    once before (see `_parse_cell`), and there is no reason to open that again.

    `create` **and** `edit`, matching the upload route: naming an existing
    `unique` value is a conflict rather than an update, but a row can carry a
    reference that resolves against records this caller must be able to write.

    Rows are numbered by position from 1, so `errors[].row` points at an index in
    the array the caller sent rather than at a line in a file nobody uploaded.
    """
    await PermissionService.require(
        db, current_user, ResourceType.list, lst.id, Action.create
    )
    await PermissionService.require(
        db, current_user, ResourceType.list, lst.id, Action.edit
    )
    rows = ((i, record) for i, record in enumerate(payload.records, start=1))
    try:
        return await import_records(db, lst, rows, user=current_user)
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc


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
    # Live or archived: delete wins over archive, so the archive must not be a place
    # a record can hide from being binned (`get_unbinned_by_uuid`).
    item = await ListItemService.get_unbinned_by_uuid(db, lst.id, uuid)
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


@router.post("/{uuid}/archive", status_code=status.HTTP_204_NO_CONTENT)
async def archive_item(
    uuid: UUID,
    lst: List = Depends(resolve_list_or_404),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # Per-item `delete`, exactly like `delete_item`: an archive takes the record away
    # from everyone who can read the list, so it asks for the same authority as
    # binning it. The record is still live at this point, so it still resolves to a
    # permission context of its own.
    item = await ListItemService.get_by_uuid(db, lst.id, uuid)
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Item not found")
    await PermissionService.require(
        db, current_user, ResourceType.item, item.id, Action.delete
    )
    await ListItemService.archive(db, lst, uuid)


@router.post("/{uuid}/unarchive", response_model=ListItemRead)
async def unarchive_item(
    uuid: UUID,
    lst: List = Depends(resolve_list_or_404),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # Gated on list-level `delete`, matching the archive listing this is reached
    # from. Unlike `restore_item` there is no 409 to handle: an archived record never
    # released its unique values, so nothing can have taken them (`unarchive`).
    await PermissionService.require(
        db, current_user, ResourceType.list, lst.id, Action.delete
    )
    item = await ListItemService.unarchive(db, lst, uuid)
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Item not found")
    return item
