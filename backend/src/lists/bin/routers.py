"""Bin routes for a list's views, forms and metrics.

Mounted at ``/lists/{list_uuid}/bin/{kind}`` rather than under each child collection,
because ``/lists/{list_uuid}/views/deleted`` is not safe here the way
``/lists/{list_uuid}/items/deleted`` is. Records are addressed by uuid alone, so no
record can be called ``deleted``; a view, form or metric is addressed by uuid *or*
name (src.lists.refs), ``deleted`` is a perfectly legal name, and that route would
silently shadow anyone who had used it. A fixed segment under the list collides with
nothing and needs no name reserved to stay that way.

Gated on list-level ``configure`` -- the authority that creates these three and the
only one that can still be resolved once they are deleted. A soft-deleted view has no
permission context of its own (``PermissionService._security_context`` returns None
for a deleted resource), so per-row checks are not available here; this is the same
reasoning that puts the records bin on list-level ``delete``. It does mean the bin is
coarser than the live routes for a view whose ``permissions_inherit`` is False: it was
visible to fewer people than the list, and its recovery is the list configurer's.
"""
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.dependencies import get_current_user
from src.auth.users.models import User
from src.core.exceptions import ConflictError
from src.infrastructure.database import get_db
from src.permissions.enums import Action, ResourceType
from src.permissions.services import PermissionService

from ..dependencies import get_list_or_404
from ..models import List
from . import services as bin_services
from .enums import BinnedKind
from .schemas import BinnedChildRead, RestoredChildRead

router = APIRouter(prefix="/{list_uuid}/bin", tags=["bin"])


async def _require_configure(
    db: AsyncSession, current_user: User, lst: List
) -> None:
    await PermissionService.require(
        db, current_user, ResourceType.list, lst.id, Action.configure
    )


@router.get("/{kind}", response_model=list[BinnedChildRead])
async def list_binned(
    kind: BinnedKind,
    response: Response,
    lst: List = Depends(get_list_or_404),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
):
    await _require_configure(db, current_user, lst)
    response.headers["X-Total-Count"] = str(
        await bin_services.count_deleted(db, kind, lst.id)
    )
    return await bin_services.list_deleted(db, kind, lst.id, skip=skip, limit=limit)


@router.post("/{kind}/{uuid}/restore", response_model=RestoredChildRead)
async def restore_binned(
    kind: BinnedKind,
    uuid: UUID,
    lst: List = Depends(get_list_or_404),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await _require_configure(db, current_user, lst)
    try:
        restored = await bin_services.restore(db, kind, lst.id, uuid)
    except ConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    if restored is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Not found in the bin"
        )
    return restored


@router.delete("/{kind}/{uuid}/permanent", status_code=status.HTTP_204_NO_CONTENT)
async def purge_binned(
    kind: BinnedKind,
    uuid: UUID,
    lst: List = Depends(get_list_or_404),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # Only something already in the bin can be purged; a live one 404s here, so
    # destroying a view is always two deliberate steps. Same rule as records and
    # lists.
    await _require_configure(db, current_user, lst)
    if not await bin_services.purge(db, kind, lst.id, uuid):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Not found in the bin"
        )
