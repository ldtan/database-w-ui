from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.dependencies import get_current_user
from src.auth.users.models import User
from src.auth.users.services import UserService
from src.core.exceptions import ConflictError, UnknownReferenceError
from src.infrastructure.database import get_db
from src.permissions.enums import Action, ResourceType
from src.permissions.services import PermissionService
from src.workspaces.services import WorkspaceService

from .dependencies import get_table_or_404
from .models import Table
from .schemas import (
    DeletedTableRead,
    TableClone,
    TableCreate,
    TableRead,
    TableTransfer,
    TableUpdate,
)
from .services import TableService

from .columns.routers import router as columns_router
from .records.routers import router as records_router
from .views.routers import router as views_router
from .forms.routers import router as forms_router
from .metrics.routers import router as metrics_router
from .transfer.routers import router as transfer_router


router = APIRouter(
    prefix="/tables",
    tags=["tables"],
    dependencies=[Depends(get_current_user)],
)


@router.post("/", response_model=TableRead, status_code=status.HTTP_201_CREATED)
async def create_table(
    payload: TableCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # Creating a table is a configuration change on its target workspace.
    workspace = await WorkspaceService.get_any_by_uuid(db, payload.workspace_uuid)
    if workspace is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="workspace_uuid does not reference a workspace",
        )
    await PermissionService.require(
        db, current_user, ResourceType.workspace, workspace.id, Action.configure
    )
    try:
        return await TableService.create(db, payload)
    except ConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.get("/", response_model=list[TableRead])
async def list_tables(
    response: Response,
    workspace_uuid: Optional[UUID] = Query(None),
    shared: bool = Query(False, description="Only tables the caller does not own"),
    lookup_targets: bool = Query(
        False,
        description=(
            "Also include tables shared only as lookup targets — tables the caller "
            "holds 'lookup' but not 'view' on. For the lookup target picker."
        ),
    ),
    workspace_hidden: Optional[bool] = Query(
        None,
        description=(
            "Filter on whether the caller can view the table's parent workspace. "
            "True keeps only tables whose workspace is hidden from them — the ones "
            "that hang off nothing and are reachable no other way. Omit for both."
        ),
    ),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    q: Optional[str] = Query(None, description="Match table name or display name"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Searching and paging server-side, so a search finds tables past the first page.

    The total number of matches (ignoring skip/limit) comes back in X-Total-Count,
    which is what a paginator needs; the body stays a plain array, so callers that
    just want the table (the nav drawer, the column picker) are unaffected.

    ``lookup_targets`` is off by default, so every existing caller keeps showing only
    tables the caller can open — a lookup-only table stays out of the nav and out of
    "Shared with me", which is the point of that grant.

    ``shared`` and ``workspace_hidden`` are two different questions and both are
    needed: the first asks who owns the table, the second whether the caller can open
    the workspace holding it. "Shared with me" combines them to find the tables that
    have no other way in; the lookup target picker wants ``shared`` alone.
    """
    response.headers["X-Total-Count"] = str(
        await TableService.count(
            db,
            current_user,
            workspace_uuid=workspace_uuid,
            shared_only=shared,
            q=q,
            lookup_targets=lookup_targets,
            workspace_hidden=workspace_hidden,
        )
    )
    return await TableService.list(
        db,
        current_user,
        workspace_uuid=workspace_uuid,
        shared_only=shared,
        skip=skip,
        limit=limit,
        q=q,
        lookup_targets=lookup_targets,
        workspace_hidden=workspace_hidden,
    )


# Declared before GET /{table_uuid} so "deleted" is not parsed as a uuid. Scoped to
# one workspace and gated on that workspace's `delete`: a soft-deleted table resolves
# to no permission context (see PermissionService._security_context), so the check
# has to sit on the live parent — the same reason the records bin is gated on its
# table rather than the record.
@router.get("/deleted", response_model=list[DeletedTableRead])
async def list_deleted_tables(
    response: Response,
    workspace_uuid: UUID = Query(..., description="The workspace whose bin to read"),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    workspace = await WorkspaceService.get_any_by_uuid(db, workspace_uuid)
    if workspace is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found"
        )
    await PermissionService.require(
        db, current_user, ResourceType.workspace, workspace.id, Action.delete
    )
    response.headers["X-Total-Count"] = str(
        await TableService.count_deleted_for_workspace(db, workspace.id)
    )
    return await TableService.list_deleted_for_workspace(
        db, workspace.id, skip=skip, limit=limit
    )


@router.get("/{table_uuid}", response_model=TableRead)
async def get_table(table: Table = Depends(get_table_or_404)):
    return table


@router.patch("/{table_uuid}", response_model=TableRead)
async def update_table(
    payload: TableUpdate,
    table: Table = Depends(get_table_or_404),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # Editing table settings requires 'configure'; toggling inheritance requires 'share'.
    await PermissionService.require(
        db, current_user, ResourceType.table, table.id, Action.configure
    )
    if payload.permissions_inherit is not None:
        await PermissionService.require(
            db, current_user, ResourceType.table, table.id, Action.share
        )
    if payload.workspace_uuid is not None:
        target = await WorkspaceService.get_any_by_uuid(db, payload.workspace_uuid)
        if target is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="workspace_uuid does not reference a workspace",
            )
        await PermissionService.require(
            db, current_user, ResourceType.workspace, target.id, Action.configure
        )
    try:
        updated = await TableService.update(db, table.uuid, payload)
    except ConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except UnknownReferenceError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    if updated is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Table not found")
    return updated


@router.post("/{table_uuid}/transfer", response_model=TableRead)
async def transfer_table(
    table_uuid: UUID,
    payload: TableTransfer,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Hand a table to another user — it moves into their immutable "Incoming"
    workspace and becomes theirs.

    Owner-only, and deliberately *not* the generic cross-workspace move: that move
    requires `configure` on the target workspace, which nobody holds on a stranger's
    inbox. Transferring instead resolves the recipient's inbox itself, so the only
    authorization is being the table's current owner — the giver can only give away
    what is fully theirs. Existing shares survive the move; the giver's implicit
    owner access ends with it (see TableService.transfer_to_user).
    """
    table = await TableService.get_any_by_uuid(db, table_uuid)
    if table is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Table not found"
        )
    # Owner-only: ownership flows through the workspace, so the caller must own the
    # table's current workspace. This is the whole authorization — no grant qualifies.
    if table.workspace is None or table.workspace.owner_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the table's owner can transfer it",
        )

    recipient = await UserService.get_by_uuid(db, payload.user_uuid)
    if recipient is None or not recipient.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="user_uuid does not reference an active user",
        )
    if recipient.id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot transfer a table to yourself",
        )

    transferred = await TableService.transfer_to_user(db, table.uuid, recipient.id)
    if transferred is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Table not found"
        )
    return transferred


@router.post(
    "/{table_uuid}/clone",
    response_model=TableRead,
    status_code=status.HTTP_201_CREATED,
)
async def clone_table(
    payload: TableClone,
    table: Table = Depends(get_table_or_404),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Duplicate a table into its own workspace — structure always, records optionally.

    Reading the source needs 'view' (enforced by the dependency); creating the copy
    is a configuration change on the same workspace, so it needs 'configure' there —
    the same gate `create_table` applies. Columns get new uuids and every reference to
    them (record data keys, primary column, view/form configs) is remapped; grants
    are not copied, so the clone starts private like any new table.

    Copying the records is a read of them, so `clone` takes the caller and applies
    the per-record filter — 'view' on the table does not reach a record whose
    inheritance was broken and which was never granted to them.
    """
    await PermissionService.require(
        db, current_user, ResourceType.workspace, table.workspace_id, Action.configure
    )
    try:
        cloned = await TableService.clone(
            db, table.uuid, with_records=payload.with_records, user=current_user
        )
    except ConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if cloned is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Table not found")
    return cloned


@router.delete("/{table_uuid}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_table(
    table: Table = Depends(get_table_or_404),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await PermissionService.require(
        db, current_user, ResourceType.table, table.id, Action.delete
    )
    await TableService.delete(db, table.uuid)


@router.post("/{table_uuid}/restore", response_model=TableRead)
async def restore_table(
    table_uuid: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # Gated on the parent workspace's `delete`, like the bin listing: the table itself
    # is deleted and resolves to no permission context. Restore also revives the
    # table's columns, records, views and forms (see TableService.restore).
    table = await TableService.get_deleted_by_uuid(db, table_uuid)
    if table is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Table not found"
        )
    await PermissionService.require(
        db, current_user, ResourceType.workspace, table.workspace_id, Action.delete
    )
    try:
        restored = await TableService.restore(db, table_uuid)
    except ConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if restored is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Table not found"
        )
    return restored


@router.delete("/{table_uuid}/permanent", status_code=status.HTTP_204_NO_CONTENT)
async def purge_table(
    table_uuid: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # Gated on the parent workspace's `delete`, like the bin and restore. Only a table
    # already in the bin can be purged; a live one 404s here (soft-delete it first).
    table = await TableService.get_deleted_by_uuid(db, table_uuid)
    if table is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Table not found"
        )
    await PermissionService.require(
        db, current_user, ResourceType.workspace, table.workspace_id, Action.delete
    )
    await TableService.purge(db, table_uuid)


# Child resources are scoped under a single table: /tables/{table_uuid}/<child>
router.include_router(columns_router)
router.include_router(records_router)
router.include_router(views_router)
router.include_router(forms_router)
router.include_router(metrics_router)
router.include_router(transfer_router)
