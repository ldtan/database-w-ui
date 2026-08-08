"""Writing both halves of a merged form in one transaction.

A merged form (forms/schemas.MergeConfig) lays a second list's fields beside this
list's own, reached through one `lookup` column. Submitting it has to write a record on
each side and link them — and has to do so atomically, because a half-finished pair is
a record in somebody else's list that nothing points at, findable only by counting.

That atomicity is the entire reason this module exists rather than the route calling
`ListItemService.create` twice: each of those ends in its own `commit`, so the first
would land before the second was even validated. `create_uncommitted` and
`update_uncommitted` are the seam this composes instead, with one commit at the end.

**Two locks, always in the same order.** Each side takes a per-list advisory lock (the
one `_enforce_unique` already uses) and, on an update, a row lock. Two merged writes
touching the same pair from opposite directions would deadlock if either took them in
arrival order, so both are taken up front, sorted by list id. The advisory locks are
re-entrant within a session, so the ones `_enforce_unique` takes later are free.
"""
from typing import Optional, Union
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions import UnknownReferenceError, ValidationError
from src.core.utils import coerce_uuid

from ..columns.enums import ColumnType
from ..columns.models import Column, in_shape
from ..forms.models import Form
from ..models import List
from .models import ListItem
from .schemas import (
    ListItemCreate,
    ListItemUpdate,
    MergedItemCreate,
    MergedItemUpdate,
)
from .services import ListItemService, _UNIQUE_LOCK_NS


class MergedLink:
    """A resolved merged form: the link column, the list it reaches, and the key."""

    __slots__ = ("form", "column", "target", "key", "allow_shared")

    def __init__(self, form: Form, column: Column, target: List):
        self.form = form
        self.column = column
        self.target = target
        self.key = str(column.uuid)
        self.allow_shared = bool(
            ((form.config or {}).get("merge") or {}).get("allow_shared")
        )


class MergedItemService:
    @classmethod
    async def resolve(
        cls, db: AsyncSession, lst: List, form_uuid: Union[str, UUID], user
    ) -> MergedLink:
        """The form's link column and the list it reaches, with `view` checked on it.

        Re-validated on every write rather than trusted from config time, because the
        link column can be deleted or retargeted, and the caller's access to the target
        can be revoked, long after the form was saved.

        **`view`, never `lookup`.** A merged form reads and writes arbitrary columns of
        the target; `lookup` exists to disclose a display column and nothing else.
        Refused indistinguishably from a missing form so this cannot be used to learn
        that a list exists.
        """
        from src.permissions.enums import Action, ResourceType
        from src.permissions.services import PermissionService

        parsed = coerce_uuid(form_uuid)
        form = (
            await db.execute(
                select(Form).where(
                    Form.deleted_at.is_(None),
                    Form.list_id == lst.id,
                    Form.uuid == parsed,
                )
            )
        ).scalars().first() if parsed is not None else None
        merge = ((form.config or {}) if form is not None else {}).get("merge") or {}
        if not merge.get("link_column_uuid"):
            raise UnknownReferenceError("No merged form with that uuid on this list")

        column = (
            await db.execute(
                select(Column).where(
                    Column.list_id == lst.id,
                    Column.uuid == coerce_uuid(merge["link_column_uuid"]),
                    in_shape(),
                )
            )
        ).scalars().first()
        if column is None or column.type != ColumnType.lookup:
            # The link was deleted or retyped since the form was saved. The form is
            # still a form; it just cannot reach anything, so a merged write on it is
            # the thing that fails rather than every read of it.
            raise UnknownReferenceError(
                "This merged form's link column is no longer a lookup on this list"
            )

        from ..services import ListService

        target = await ListService.get_any_by_uuid(
            db, (column.config or {}).get("target_list_uuid")
        )
        if target is None or not await PermissionService.has(
            db, user, ResourceType.list, target.id, Action.view
        ):
            raise UnknownReferenceError(
                f"Lookup column '{column.name}' points at a missing target list"
            )

        return MergedLink(form, column, target)

    @staticmethod
    async def _lock(db: AsyncSession, *list_ids: int) -> None:
        """Take each list's advisory lock, lowest id first. See the module docstring."""
        for list_id in sorted(set(list_ids)):
            await db.execute(select(func.pg_advisory_xact_lock(_UNIQUE_LOCK_NS, list_id)))

    @classmethod
    async def create(
        cls, db: AsyncSession, lst: List, payload: MergedItemCreate, user
    ) -> tuple[ListItem, Optional[ListItem]]:
        from src.permissions.enums import Action, ResourceType
        from src.permissions.services import PermissionService

        link = await cls.resolve(db, lst, payload.form_uuid, user)
        await cls._lock(db, lst.id, link.target.id)

        try:
            linked = await cls._linked_side_for_create(db, link, payload, user)
            # The link cell is written from the record just staged, never from the
            # client — a caller that could name the linked record itself would be able
            # to point this row at a record it never wrote to.
            data = {**payload.data, link.key: str(linked.uuid)}
            item = await ListItemService.create_uncommitted(
                db, lst, ListItemCreate(data=data), user=user
            )
            item_uuid, linked_uuid = item.uuid, linked.uuid
            await db.commit()
        except Exception:
            # Both halves go, or neither does. Without this the linked record staged
            # above would survive a refusal on this side as an orphan.
            await db.rollback()
            raise

        return await cls._read_pair(db, lst, link, item_uuid, linked_uuid, user)

    @classmethod
    async def _linked_side_for_create(
        cls, db: AsyncSession, link: MergedLink, payload: MergedItemCreate, user
    ) -> ListItem:
        """Stage (or find) the record on the other side, uncommitted.

        Three cases, and the permission each needs is different: minting a record needs
        `create` on that list; writing onto one somebody picked needs `edit` on **that
        record**, per-item rather than list-level, because a record can be shared on its
        own; merely pointing at one needs nothing beyond the `view` already checked.
        """
        from src.permissions.enums import Action, ResourceType
        from src.permissions.services import PermissionService

        if payload.linked_uuid is None:
            await PermissionService.require(
                db, user, ResourceType.list, link.target.id, Action.create
            )
            return await ListItemService.create_uncommitted(
                db, link.target, ListItemCreate(data=payload.linked_data), user=user
            )

        existing = await ListItemService.get_by_uuid(
            db, link.target.id, payload.linked_uuid
        )
        if existing is None:
            raise UnknownReferenceError(
                "No record with that uuid in the linked list"
            )
        if not await PermissionService.has(
            db, user, ResourceType.item, existing.id, Action.view
        ):
            # Same wording as a missing record: whether a record the caller cannot see
            # exists is not something a link picker should answer.
            raise UnknownReferenceError(
                "No record with that uuid in the linked list"
            )

        if payload.update_linked and payload.linked_data:
            await PermissionService.require(
                db, user, ResourceType.item, existing.id, Action.edit
            )
            updated = await ListItemService.update_uncommitted(
                db,
                link.target,
                existing.uuid,
                ListItemUpdate(data=payload.linked_data),
                user=user,
            )
            if updated is not None:
                return updated
        return existing

    @classmethod
    async def update(
        cls,
        db: AsyncSession,
        lst: List,
        uuid: Union[str, UUID],
        payload: MergedItemUpdate,
        user,
    ) -> Optional[tuple[ListItem, Optional[ListItem]]]:
        from src.permissions.enums import Action, ResourceType
        from src.permissions.services import PermissionService

        link = await cls.resolve(db, lst, payload.form_uuid, user)
        await cls._lock(db, lst.id, link.target.id)

        item = await ListItemService.get_by_uuid(db, lst.id, uuid)
        if item is None:
            return None
        linked_uuid = coerce_uuid((item.data or {}).get(link.key))

        try:
            if payload.linked_data and linked_uuid is not None:
                existing = await ListItemService.get_by_uuid(
                    db, link.target.id, linked_uuid
                )
                if existing is None:
                    # The link is stale — the record it named is gone. Writing this
                    # side anyway would silently drop what was typed into the other.
                    raise ValidationError(
                        "The linked record no longer exists, so its fields cannot be "
                        "saved. Re-link this row first"
                    )
                await PermissionService.require(
                    db, user, ResourceType.item, existing.id, Action.edit
                )
                await ListItemService.update_uncommitted(
                    db,
                    link.target,
                    existing.uuid,
                    ListItemUpdate(data=payload.linked_data),
                    user=user,
                )
            elif payload.linked_data:
                raise ValidationError(
                    "This row is not linked to a record yet, so there is nothing to "
                    "save its linked fields onto"
                )

            updated = await ListItemService.update_uncommitted(
                db,
                lst,
                item.uuid,
                ListItemUpdate(
                    data=payload.data,
                    permissions_inherit=payload.permissions_inherit,
                ),
                user=user,
            )
            if updated is None:
                await db.rollback()
                return None
            item_uuid = updated.uuid
            # Re-read after the write: the link cell may have moved in `data`.
            linked_uuid = coerce_uuid((updated.data or {}).get(link.key))
            await db.commit()
        except Exception:
            await db.rollback()
            raise

        return await cls._read_pair(db, lst, link, item_uuid, linked_uuid, user)

    @classmethod
    async def _read_pair(
        cls,
        db: AsyncSession,
        lst: List,
        link: MergedLink,
        item_uuid: UUID,
        linked_uuid: Optional[UUID],
        user,
    ) -> tuple[ListItem, Optional[ListItem]]:
        """Both records as a reader should see them, the linked one permission-checked.

        The linked record comes back as ``None`` rather than a refusal when the caller
        cannot see it. A record can be shared on its own (`permissions_inherit`), so
        `view` on the list does not settle it — and a row whose pair is restricted is
        still a row this caller may read.
        """
        from src.permissions.enums import Action, ResourceType
        from src.permissions.services import PermissionService

        item = await ListItemService.get_for_read(db, lst.id, item_uuid)
        linked = None
        if linked_uuid is not None:
            candidate = await ListItemService.get_for_read(
                db, link.target.id, linked_uuid
            )
            if candidate is not None and await PermissionService.has(
                db, user, ResourceType.item, candidate.id, Action.view
            ):
                linked = candidate
        return item, linked
