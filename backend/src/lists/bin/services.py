"""The bin for a list's views, forms, metrics and columns.

All four are siblings -- list-scoped, named, ordered -- and deleting one has always
only set ``deleted_at``, like everything else here. Nothing ever read that back. There
was no way to recover one through the API and no job to reclaim the row, so a deleted
view sat in its table forever, invisible and permanent at the same time. This module
is the other half.

Written once against a model rather than four times over. Two things differ per kind
and both live in ``_KINDS``: whether the row can be a grant target
(``permission_grants.resource_type`` -- a view or form can, a metric or column never
can), which decides whether a permanent delete has grants to clear; and the noun for
its error messages. The routes live on each child's own collection beside the live
ones and pass their model in; this module is what keeps the four from drifting apart.

**A column is the odd one.** The other three are pure config and own nothing; a
column's values sit under its uuid in every record's ``data``. So ``purge`` has a
branch for it, and its restore reports what did not come back with it -- see
``RestoredColumnRead``.

**Every one of these routes is gated on list-level ``configure``** -- the authority
that creates these three, and the only one that can still be resolved once they are
deleted. A soft-deleted view has no permission context of its own
(``PermissionService._security_context`` returns None for a deleted resource), so the
per-row checks the live routes use are not available here; the records bin sits on
list-level ``delete`` for the same reason. It does mean the bin is coarser than the
live routes for a view whose ``permissions_inherit`` is False: it was visible to
fewer people than the list, and its recovery is the list configurer's.

**Records are deliberately absent.** They have a bin of their own already, older and
shaped around per-record permissions, at ``/lists/{uuid}/items/deleted``.

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

from ..columns.models import Column
from ..forms.models import Form
from ..metrics.models import Metric
from ..purge import strip_column_keys
from ..views.models import View

# Per model: how `permission_grants.resource_type` names it (None when it is never a
# grant target), and the singular noun for error messages.
_KINDS = {
    View: (ResourceType.view, "view"),
    Form: (ResourceType.form, "form"),
    Metric: (None, "metric"),
    Column: (None, "column"),
}


def _cutoff() -> datetime:
    """The oldest deletion still offered for recovery. Past this a row is due to be
    purged and is no longer promised back, which is the rule the records and lists
    bins already follow."""
    return datetime.now(timezone.utc) - timedelta(
        days=settings.PURGE_DELETED_AFTER_DAYS
    )


def _select_deleted(model: type, list_id: int):
    return select(model).where(
        model.list_id == list_id,
        model.deleted_at.is_not(None),
        model.deleted_at >= _cutoff(),
    )


async def list_deleted(
    db: AsyncSession,
    model: type,
    list_id: int,
    skip: int = 0,
    limit: int = 100,
) -> Sequence:
    """A list's bin for one kind, most recently deleted first."""
    stmt = (
        _select_deleted(model, list_id)
        .order_by(model.deleted_at.desc(), model.id.desc())
        .offset(skip)
        .limit(limit)
    )
    return (await db.execute(stmt)).scalars().all()


async def count_deleted(db: AsyncSession, model: type, list_id: int) -> int:
    """The paginator's total, built off the same predicate as the listing so the two
    cannot drift."""
    stmt = select(func.count()).select_from(_select_deleted(model, list_id).subquery())
    return await db.scalar(stmt) or 0


async def get_deleted(
    db: AsyncSession, model: type, list_id: int, uuid: Union[str, UUID]
) -> Optional[object]:
    """One row from the bin. None if there is no such row, or it is live, or it is
    already past the window -- restore and purge both want the same answer, and a
    caller that gets None should 404."""
    parsed = coerce_uuid(uuid)
    if parsed is None:
        return None
    stmt = _select_deleted(model, list_id).where(model.uuid == parsed)
    return (await db.execute(stmt)).scalars().first()


async def restore(
    db: AsyncSession, model: type, list_id: int, uuid: Union[str, UUID]
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
    row = await get_deleted(db, model, list_id, uuid)
    if row is None:
        return None

    _, noun = _KINDS[model]
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
    db: AsyncSession, model: type, list_id: int, uuid: Union[str, UUID]
) -> bool:
    """Destroy one for good. False if it is not in the bin. Irreversible.

    Clears the row's grants first when it can have any. ``permission_grants`` points
    at its resource polymorphically with no foreign key, so nothing cascades: while
    the row is soft-deleted its grants are inert, and hard-deleting it is precisely
    what would turn them into orphans pointing at a reused id.

    **A column also takes its data.** The other three own no records; a column's values
    live under its uuid in every record's ``data``, and deleting the column never
    touched them -- ``shape_values`` stops serving them, which is what makes them
    hidden, but nothing removes them. Dropping the row without dropping the keys would
    strand that data permanently: no live column to read it through, no route to reach
    it, and no way to delete it afterwards because nothing would name it. This is the
    only surface that can free it, which is most of why a column bin is worth having
    at all.
    """
    row = await get_deleted(db, model, list_id, uuid)
    if row is None:
        return False

    resource_type, _ = _KINDS[model]
    if resource_type is not None:
        await db.execute(
            sa.text(
                "DELETE FROM permission_grants "
                "WHERE resource_type = :rt AND resource_id = :rid"
            ),
            {"rt": resource_type.value, "rid": row.id},
        )
    if model is Column:
        # Before the row goes, while its uuid is still known. Batched, since the
        # records holding it are the one part of this sized by the data — and without
        # committing between batches, because a binned column is restorable right up
        # until this succeeds (see src.lists.purge).
        await strip_column_keys(db, list_id, [str(row.uuid)], commit=False)
    await db.execute(sa.delete(model).where(model.id == row.id))
    await db.commit()
    return True
