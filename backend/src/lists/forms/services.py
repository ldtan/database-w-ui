from datetime import datetime, timezone
from typing import Optional, Sequence, Union
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions import ConflictError, UnknownReferenceError
from src.core.utils import coerce_uuid

from ..columns.models import Column, in_shape
from ..models import List
from .models import Form
from .schemas import FormConfig, FormCreate, FormUpdate


class FormService:
    @staticmethod
    def _select(list_id: int):
        return select(Form).where(Form.deleted_at.is_(None), Form.list_id == list_id)

    @classmethod
    async def get_by_uuid(
        cls, db: AsyncSession, list_id: int, uuid: Union[str, UUID]
    ) -> Optional[Form]:
        parsed = coerce_uuid(uuid)
        if parsed is None:
            return None
        result = await db.execute(cls._select(list_id).where(Form.uuid == parsed))
        return result.scalars().first()

    @classmethod
    async def list(cls, db: AsyncSession, list_id: int, user) -> Sequence[Form]:
        from src.permissions.enums import Action, ResourceType
        from src.permissions.services import PermissionService

        result = await db.execute(cls._select(list_id).order_by(Form.id))
        forms = result.scalars().all()
        # Few forms per list, so resolve visibility per row rather than in SQL.
        visible = []
        for form in forms:
            if await PermissionService.has(
                db, user, ResourceType.form, form.id, Action.view
            ):
                visible.append(form)
        return visible

    @classmethod
    async def create(cls, db: AsyncSession, lst: List, payload: FormCreate) -> Form:
        await cls._validate_config(db, lst.id, payload.config)
        form = Form(
            list_id=lst.id,
            name=payload.name,
            display_name=payload.display_name,
            type=payload.type,
            is_default=payload.is_default,
            config=payload.config.model_dump(mode="json"),
        )
        db.add(form)
        try:
            await db.flush()
            new_uuid = form.uuid
            await db.commit()
        except IntegrityError as exc:
            await db.rollback()
            raise ConflictError(
                "A form with this name already exists on the list"
            ) from exc
        return await cls.get_by_uuid(db, lst.id, new_uuid)

    @classmethod
    async def update(
        cls,
        db: AsyncSession,
        lst: List,
        uuid: Union[str, UUID],
        payload: FormUpdate,
    ) -> Optional[Form]:
        form = await cls.get_by_uuid(db, lst.id, uuid)
        if form is None:
            return None

        data = payload.model_dump(exclude_unset=True)
        if "config" in data:
            await cls._validate_config(db, lst.id, payload.config)
            form.config = payload.config.model_dump(mode="json")
            data.pop("config")
        for key in ("name", "display_name", "type", "is_default", "permissions_inherit"):
            if key in data and data[key] is None:
                data.pop(key)

        for field, value in data.items():
            setattr(form, field, value)

        try:
            await db.commit()
        except IntegrityError as exc:
            await db.rollback()
            raise ConflictError(
                "A form with this name already exists on the list"
            ) from exc
        return await cls.get_by_uuid(db, lst.id, uuid)

    @classmethod
    async def delete(
        cls, db: AsyncSession, list_id: int, uuid: Union[str, UUID]
    ) -> bool:
        form = await cls.get_by_uuid(db, list_id, uuid)
        if form is None:
            return False

        form.deleted_at = datetime.now(timezone.utc)
        await db.commit()
        return True

    @staticmethod
    async def _validate_config(
        db: AsyncSession, list_id: int, config: FormConfig
    ) -> None:
        """Ensure every column a form names belongs to the list.

        Two kinds of reference, checked by one query: the field itself, and the columns
        its `condition` compares against. A condition naming a column of some other list
        would simply never resolve when the form is filled in, and the field would show
        unconditionally with nothing on screen to say why — so it is refused here, where
        there is somebody to tell.
        """
        # A field that hides on its own value keeps that value, so the condition stays
        # true and the field never comes back. Not a cycle — conditions are evaluated in
        # one pass over the draft's raw values — but the result reads as a bug in the app
        # rather than in the config, so it is refused rather than explained.
        for field in config.fields:
            if field.condition is None:
                continue
            if any(
                cond.column_uuid == field.column_uuid
                for cond in field.condition.when.conditions
            ):
                raise UnknownReferenceError(
                    f"A field's condition cannot test the field's own column "
                    f"({field.column_uuid})"
                )

        referenced = {field.column_uuid for field in config.fields}
        referenced |= {
            cond.column_uuid
            for field in config.fields
            if field.condition is not None
            for cond in field.condition.when.conditions
        }
        if not referenced:
            return

        result = await db.execute(
            select(Column.uuid).where(
                Column.list_id == list_id,
                Column.uuid.in_(referenced),
                in_shape(),
            )
        )
        found = set(result.scalars().all())
        missing = [str(u) for u in referenced if u not in found]
        if missing:
            raise UnknownReferenceError(
                f"Form references column(s) not on this list: {', '.join(missing)}"
            )
