"""The bin for a list's views, forms and metrics.

These three are siblings -- list-scoped, named, ordered, with their behaviour in a
JSONB ``config`` -- and deleting one has always only set ``deleted_at``, like
everything else here. Nothing ever read that back. There was no way to recover one
through the API and no job to reclaim the row, so a deleted view sat in its table
forever, invisible and permanent at the same time. This module is the other half.

Written once against a model rather than three times over, because the three differ
in exactly one respect that matters to a bin: a view or a form can be a grant target
(``permission_grants.resource_type``), and a metric never is -- it is always its
list's. That is the ``ResourceType`` in ``_KINDS``, and it is what decides whether a
permanent delete has grants to clear.

**Addressed by uuid, never by name.** Every other child route takes either (see
src.lists.refs), and the bin cannot: the unique index on ``name`` is partial
(``WHERE deleted_at IS NULL``), so a binned ``sales`` view and a live one coexist
legitimately, and two binned ones can share a name as well. A name in the bin has no
single answer, so it is not offered.

**Only children binned on their own appear here.** These routes resolve a *live*
list, and a list delete stamps every child with the list's own ``deleted_at`` and
restores exactly that set -- so a child under a live list is one somebody deleted by
itself. A child binned *before* its list keeps its own earlier timestamp, is left
behind by the list's restore, and correctly turns up here afterwards.
"""
from datetime import datetime, timedelta, timezone
from typing import Optional, Sequence, Union
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import settings
from src.core.exceptions import ConflictError
from src.core.utils import coerce_uuid
from src.permissions.enums import ResourceType

from ..forms.models import Form
from ..metrics.models import Metric
from ..views.models import View
from .enums import BinnedKind

# Per kind: the model, how `permission_grants.resource_type` names it (None when it
# is never a grant target), and the singular noun for error messages.
_KINDS = {
    BinnedKind.views: (View, ResourceType.view, "view"),
    BinnedKind.forms: (Form, ResourceType.form, "form"),
    BinnedKind.metrics: (Metric, None, "metric"),
}


def _cutoff() -> datetime:
    """The oldest deletion still offered for recovery. Past this a row is due to be
    purged and is no longer promised back, which is the rule the records and lists
    bins already follow."""
    return datetime.now(timezone.utc) - timedelta(
        days=settings.PURGE_DELETED_AFTER_DAYS
    )


def _select_deleted(kind: BinnedKind, list_id: int):
    model = _KINDS[kind][0]
    return select(model).where(
        model.list_id == list_id,
        model.deleted_at.is_not(None),
        model.deleted_at >= _cutoff(),
    )


async def list_deleted(
    db: AsyncSession,
    kind: BinnedKind,
    list_id: int,
    skip: int = 0,
    limit: int = 100,
) -> Sequence:
    """A list's bin for one kind, most recently deleted first."""
    model = _KINDS[kind][0]
    stmt = (
        _select_deleted(kind, list_id)
        .order_by(model.deleted_at.desc(), model.id.desc())
        .offset(skip)
        .limit(limit)
    )
    return (await db.execute(stmt)).scalars().all()


async def count_deleted(db: AsyncSession, kind: BinnedKind, list_id: int) -> int:
    """The paginator's total, built off the same predicate as the listing so the two
    cannot drift."""
    stmt = select(func.count()).select_from(_select_deleted(kind, list_id).subquery())
    return await db.scalar(stmt) or 0


async def get_deleted(
    db: AsyncSession, kind: BinnedKind, list_id: int, uuid: Union[str, UUID]
) -> Optional[object]:
    """One row from the bin. None if there is no such row, or it is live, or it is
    already past the window -- restore and purge both want the same answer, and a
    caller that gets None should 404."""
    parsed = coerce_uuid(uuid)
    if parsed is None:
        return None
    model = _KINDS[kind][0]
    stmt = _select_deleted(kind, list_id).where(model.uuid == parsed)
    return (await db.execute(stmt)).scalars().first()


async def restore(
    db: AsyncSession, kind: BinnedKind, list_id: int, uuid: Union[str, UUID]
) -> Optional[object]:
    """Bring one back. None if it is not in the bin.

    The one live edge is the name, exactly as it is for a record's unique columns: a
    name is unique per list among *live* rows only, so a new view may have taken the
    binned one's name while it sat there, and the partial index refuses the restore.
    That is a real 409 and not a bug to route around -- the caller renames one of
    them and tries again.

    What is deliberately *not* re-checked is the ``config``. A view's filters, a
    form's fields and a metric's formula all reference columns, and a column may have
    been deleted while this row was binned. Restoring it anyway lands it in exactly
    the state a *live* one would be in if that column had been deleted underneath it,
    which is a state all three already tolerate -- a metric reports an ``error``
    rather than a number, and the other two skip what they cannot resolve. Blocking
    the restore instead would refuse to return something the app is perfectly willing
    to hold.
    """
    row = await get_deleted(db, kind, list_id, uuid)
    if row is None:
        return None

    model, _, noun = _KINDS[kind]
    parsed = row.uuid
    row.deleted_at = None
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise ConflictError(
            f"A {noun} with this name already exists on the list"
        ) from exc

    return (
        await db.execute(
            select(model).where(
                model.list_id == list_id,
                model.deleted_at.is_(None),
                model.uuid == parsed,
            )
        )
    ).scalars().first()


async def purge(
    db: AsyncSession, kind: BinnedKind, list_id: int, uuid: Union[str, UUID]
) -> bool:
    """Destroy one for good. False if it is not in the bin. Irreversible.

    Clears the row's grants first when it can have any. ``permission_grants`` points
    at its resource polymorphically with no foreign key, so nothing cascades: while
    the row is soft-deleted its grants are inert, and hard-deleting it is precisely
    what would turn them into orphans pointing at a reused id.
    """
    row = await get_deleted(db, kind, list_id, uuid)
    if row is None:
        return False

    model, resource_type, _ = _KINDS[kind]
    if resource_type is not None:
        await db.execute(
            sa.text(
                "DELETE FROM permission_grants "
                "WHERE resource_type = :rt AND resource_id = :rid"
            ),
            {"rt": resource_type.value, "rid": row.id},
        )
    await db.execute(sa.delete(model).where(model.id == row.id))
    await db.commit()
    return True
