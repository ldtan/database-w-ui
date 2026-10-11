"""The bin for deleted accounts and groups.

The last thing in the app whose delete was one-way in practice. Deleting a user or a
group has always only set ``deleted_at`` -- the row stays so the cells naming it can
read as "(deleted)" and so every foreign key stays honest -- but nothing read that
back. There was no way to see what had been deleted, no way to undo it, and
``purge_deleted_principals`` deliberately never removed the row, so a mistake was
permanent and invisible at the same time.

**A separate surface with a different authority.** The records, tables and workspaces
bins are gated on ownership of the data; this one is ``require_superuser``. Accounts
and groups are identity administration and sit *under* the permission model -- groups
are principals in the grant table, so whoever can restore one can hand it back every
grant it held. That is the same reasoning the live write routes already use, and the
reason this is not folded into any per-table trash.

**Restore is a real undo, memberships included.** The deletes used to clear them so
the row stopped appearing in the other side's read schema, which made the delete lossy
in a way nothing recorded: an account came back with no groups and no way to tell it
had ever had any. The rows are kept now and the *reads* filter instead -- both
relationships join through ``deleted_at IS NULL`` (``models.py``), and
``PermissionService._group_ids`` checks the same thing against the association table it
queries directly. That second one is the security half: without it a deleted group
would keep conferring every grant it holds on its members.

**Restore has no uniqueness edge**, unlike every other bin here. ``username``, ``email``
and a group's ``name`` are plain unique constraints rather than the partial
(``WHERE deleted_at IS NULL``) indexes the rest of the schema uses, so a deleted
account keeps its name reserved and nothing can take it while it sits here. That is a
quirk worth knowing rather than a design -- it costs a permanently occupied username
per deleted account -- but it does mean a restore cannot 409.

**Permanent delete is not symmetrical.** A group is clean: grants and membership rows
are the only things pointing at it. A user is not -- ``workspaces.owner_id`` is a real
non-null foreign key, so destroying an account that still owns a workspace either
fails at the database or takes somebody's data with it. ``purge`` refuses while any
remain and names them, leaving "who owns this now?" to a human with the workspace bin
and its own transfer routes. Their profile (``people``) and login tokens are part of
the account rather than things it owns, and go with it.
"""
from datetime import datetime, timedelta, timezone
from typing import Optional, Sequence, Union
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.core.config import settings
from src.core.exceptions import ConflictError
from src.core.utils import coerce_uuid
from src.workspaces.models import Workspace

from .models import User, UserGroup, user_group_association

# Per model: the singular noun for messages, and the membership relationship its read
# schema serializes. The loader matters because a restore answers with that schema, and
# a lazy relationship on a detached row raises rather than emitting a query.
_KINDS = {
    User: ("account", User.groups),
    UserGroup: ("group", UserGroup.users),
}

# Grants point at their principal with a real foreign key, but nothing cascades, so a
# hard delete has to clear them or it fails outright.
_DROP_GRANTS = {
    User: "DELETE FROM permission_grants WHERE user_id = :id",
    UserGroup: "DELETE FROM permission_grants WHERE group_id = :id",
}


def _cutoff() -> datetime:
    """The oldest deletion still offered for recovery, matching every other bin."""
    return datetime.now(timezone.utc) - timedelta(
        days=settings.PURGE_DELETED_AFTER_DAYS
    )


def _select_deleted(model: type):
    return select(model).where(
        model.deleted_at.is_not(None),
        model.deleted_at >= _cutoff(),
    )


async def list_deleted(
    db: AsyncSession, model: type, skip: int = 0, limit: int = 100
) -> Sequence:
    """The bin for one kind, most recently deleted first."""
    stmt = (
        _select_deleted(model)
        .order_by(model.deleted_at.desc(), model.id.desc())
        .offset(skip)
        .limit(limit)
    )
    return (await db.execute(stmt)).scalars().all()


async def count_deleted(db: AsyncSession, model: type) -> int:
    """The paginator's total, off the same predicate as the listing."""
    stmt = select(func.count()).select_from(_select_deleted(model).subquery())
    return await db.scalar(stmt) or 0


async def get_deleted(
    db: AsyncSession, model: type, uuid: Union[str, UUID]
) -> Optional[object]:
    """One row from the bin. None if there is no such row, or it is live, or it is
    already past the window -- restore and purge want the same answer."""
    parsed = coerce_uuid(uuid)
    if parsed is None:
        return None
    stmt = _select_deleted(model).where(model.uuid == parsed)
    return (await db.execute(stmt)).scalars().first()


async def restore(
    db: AsyncSession, model: type, uuid: Union[str, UUID]
) -> Optional[object]:
    """Bring one back. None if it is not in the bin.

    A plain ``deleted_at = NULL``, and unlike the other bins nothing can refuse it: the
    name was never released (see the module docstring). Memberships come back with it,
    because the delete never took them away -- which also means a restored group starts
    conferring its grants on its members again the moment this returns.
    """
    row = await get_deleted(db, model, uuid)
    if row is None:
        return None

    parsed = row.uuid
    row.deleted_at = None
    # A restored group confers its grants again immediately, and `_group_ids` caches
    # the answer to "which live groups is this person in" for the life of the
    # session. Drop it, or a check later in this request still reads the group as
    # deleted. Deferred import: permissions.services imports auth.users.services,
    # which imports this package.
    from src.permissions.services import PermissionService

    PermissionService.forget_group_ids(db)
    await db.commit()

    # Re-read with the membership loaded: the caller serializes it, and the delete
    # emptied it, so what comes back is an accurate empty rather than an error.
    _, relationship = _KINDS[model]
    return (
        await db.execute(
            select(model)
            .where(model.uuid == parsed, model.deleted_at.is_(None))
            .options(selectinload(relationship))
        )
    ).scalars().first()


async def owned_workspaces(db: AsyncSession, user: User) -> list[str]:
    """The names of every workspace still owned by this account, deleted ones too.

    A soft-deleted workspace holds the foreign key exactly as a live one does, so it
    blocks just the same -- and it is recoverable, which makes it worth naming rather
    than stepping over. Its own bin is where it gets dealt with.
    """
    rows = await db.execute(
        select(Workspace.name).where(Workspace.owner_id == user.id).order_by(
            Workspace.name
        )
    )
    return list(rows.scalars().all())


async def purge(db: AsyncSession, model: type, uuid: Union[str, UUID]) -> bool:
    """Destroy one for good. False if it is not in the bin. Irreversible.

    Raises ``ConflictError`` when an account still owns workspaces: the foreign key is
    non-null, so the choice is to refuse or to take that data down with the account,
    and only one of those is somebody's to make deliberately.

    Everything that is part of the principal goes with it -- grants, group membership
    rows, and for an account its profile and login tokens. Everything the principal
    merely *owns* does not, which is why the check above exists.
    """
    row = await get_deleted(db, model, uuid)
    if row is None:
        return False

    noun, _ = _KINDS[model]
    if model is User:  # noqa: SIM102 — the group branch is the absence of this one
        blocking = await owned_workspaces(db, row)
        if blocking:
            listed = ", ".join(blocking[:5])
            more = f" and {len(blocking) - 5} more" if len(blocking) > 5 else ""
            raise ConflictError(
                f"This {noun} still owns {len(blocking)} workspace(s): {listed}{more}. "
                "Reassign or delete them before destroying it."
            )

    await db.execute(sa.text(_DROP_GRANTS[model]), {"id": row.id})
    await db.execute(
        sa.delete(user_group_association).where(
            (user_group_association.c.user_id == row.id)
            if model is User
            else (user_group_association.c.group_id == row.id)
        )
    )
    if model is User:
        # Part of the account rather than something it owns: a profile belongs to
        # exactly one user and a token is a session artifact.
        await db.execute(sa.text("DELETE FROM people WHERE user_id = :id"), {"id": row.id})
        await db.execute(
            sa.text("DELETE FROM refresh_tokens WHERE user_id = :id"), {"id": row.id}
        )

    await db.execute(sa.delete(model).where(model.id == row.id))
    await db.commit()
    return True
