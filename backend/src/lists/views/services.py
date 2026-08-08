from datetime import datetime, timezone
from typing import Optional, Sequence, Union
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions import ConflictError, UnknownReferenceError
from src.core.utils import coerce_uuid

from ..columns.models import Column, in_shape
from ..items import mirror
from ..models import List
from .models import View
from .schemas import ViewConfig, ViewCreate, ViewUpdate


class ViewService:
    @staticmethod
    def _select(list_id: int):
        return select(View).where(View.deleted_at.is_(None), View.list_id == list_id)

    @classmethod
    async def get_by_uuid(
        cls, db: AsyncSession, list_id: int, uuid: Union[str, UUID]
    ) -> Optional[View]:
        parsed = coerce_uuid(uuid)
        if parsed is None:
            return None
        result = await db.execute(cls._select(list_id).where(View.uuid == parsed))
        return result.scalars().first()

    @classmethod
    async def list(cls, db: AsyncSession, list_id: int, user) -> Sequence[View]:
        from src.permissions.enums import Action, ResourceType
        from src.permissions.services import PermissionService

        result = await db.execute(
            cls._select(list_id).order_by(View.position, View.id)
        )
        views = result.scalars().all()
        # Few views per list, so resolve visibility per row rather than in SQL.
        visible = []
        for view in views:
            if await PermissionService.has(
                db, user, ResourceType.view, view.id, Action.view
            ):
                visible.append(view)
        return visible

    @classmethod
    async def create(cls, db: AsyncSession, lst: List, payload: ViewCreate) -> View:
        await cls._validate_config(db, lst.id, payload.config)
        view = View(
            list_id=lst.id,
            name=payload.name,
            display_name=payload.display_name,
            position=payload.position,
            config=payload.config.model_dump(mode="json"),
        )
        db.add(view)
        try:
            await db.flush()
            new_uuid = view.uuid
            await db.commit()
        except IntegrityError as exc:
            await db.rollback()
            raise ConflictError(
                "A view with this name already exists on the list"
            ) from exc
        return await cls.get_by_uuid(db, lst.id, new_uuid)

    @classmethod
    async def update(
        cls,
        db: AsyncSession,
        lst: List,
        uuid: Union[str, UUID],
        payload: ViewUpdate,
    ) -> Optional[View]:
        view = await cls.get_by_uuid(db, lst.id, uuid)
        if view is None:
            return None

        data = payload.model_dump(exclude_unset=True)
        if "config" in data:
            await cls._validate_config(db, lst.id, payload.config)
            view.config = payload.config.model_dump(mode="json")
            data.pop("config")
        if data.get("name") is None:
            data.pop("name", None)
        if data.get("display_name") is None:
            data.pop("display_name", None)
        if data.get("position") is None:
            data.pop("position", None)
        if data.get("permissions_inherit") is None:
            data.pop("permissions_inherit", None)

        for field, value in data.items():
            setattr(view, field, value)

        try:
            await db.commit()
        except IntegrityError as exc:
            await db.rollback()
            raise ConflictError(
                "A view with this name already exists on the list"
            ) from exc
        return await cls.get_by_uuid(db, lst.id, uuid)

    @classmethod
    async def delete(
        cls, db: AsyncSession, list_id: int, uuid: Union[str, UUID]
    ) -> bool:
        view = await cls.get_by_uuid(db, list_id, uuid)
        if view is None:
            return False

        view.deleted_at = datetime.now(timezone.utc)
        await db.commit()
        return True

    @staticmethod
    async def _validate_config(
        db: AsyncSession, list_id: int, config: ViewConfig
    ) -> None:
        """Ensure every column referenced by the view belongs to the list.

        Filters, sorts and grouping may also name a **mirror key** — a copy of a linked
        record's value that a merged form keeps beside the record (`items/mirror.py`).
        Those are checked against this list's mirrors instead, because the column half
        of the key belongs to the *other* list and would never be found here.

        `hidden_columns` and `column_order` are deliberately **not** widened. They hold
        this list's own uuids and are remapped verbatim on clone
        (`ListService._remap_view_config`), so a mirror key in either would survive the
        remap unmapped and hide the wrong column. Merged-column visibility is client
        state; see the note in the data-model doc.
        """
        referenced: set[str] = set()
        referenced.update(str(c.column_uuid) for c in config.filters.conditions)
        referenced.update(str(s.column_uuid) for s in config.sorts)
        if config.group_by is not None:
            referenced.add(str(config.group_by.column_uuid))
        own = {str(u) for u in config.hidden_columns} | {
            str(u) for u in config.column_order
        }
        bad_placement = [k for k in own if mirror.is_mirror_key(k)]
        if bad_placement:
            raise UnknownReferenceError(
                "A linked field cannot be hidden or reordered through a view: "
                + ", ".join(sorted(bad_placement))
            )
        referenced |= own
        if not referenced:
            return

        mirrors = await mirror.mirror_columns(db, list_id)
        plain = {k for k in referenced if not mirror.is_mirror_key(k)}
        unknown_mirrors = [
            k for k in referenced if mirror.is_mirror_key(k) and k not in mirrors
        ]

        found: set[str] = set()
        if plain:
            result = await db.execute(
                select(Column.uuid).where(
                    Column.list_id == list_id,
                    Column.uuid.in_(plain),
                    in_shape(),
                )
            )
            found = {str(u) for u in result.scalars().all()}
        missing = sorted([u for u in plain if u not in found] + unknown_mirrors)
        if missing:
            raise UnknownReferenceError(
                f"View references column(s) not on this list: {', '.join(missing)}"
            )
