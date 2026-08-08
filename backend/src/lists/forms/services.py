from datetime import datetime, timezone
from typing import Optional, Sequence, Union
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions import ConflictError, UnknownReferenceError
from src.core.utils import coerce_uuid

from ..columns.enums import COMPUTED_TYPES, ColumnType
from ..columns.models import Column, in_shape
from ..models import List
from .models import Form
from .schemas import FormConfig, FormCreate, FormUpdate, MergeConfig


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
    async def create(
        cls, db: AsyncSession, lst: List, payload: FormCreate, user=None
    ) -> Form:
        # `user` because a merged form's config names another list, and whether it may
        # is a question about the caller. Threaded the same way `ColumnService` took it
        # when a lookup gained cross-workspace targets.
        await cls._validate_config(db, lst.id, payload.config, user=user)
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
        user=None,
    ) -> Optional[Form]:
        form = await cls.get_by_uuid(db, lst.id, uuid)
        if form is None:
            return None

        data = payload.model_dump(exclude_unset=True)
        if "config" in data:
            await cls._validate_config(
                db, lst.id, payload.config, user=user, stored=form.config or {}
            )
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

    @classmethod
    async def _validate_config(
        cls,
        db: AsyncSession,
        list_id: int,
        config: FormConfig,
        user=None,
        stored: Optional[dict] = None,
    ) -> None:
        """Ensure every field references a column that belongs to the list.

        ``stored`` is the config already on the form, and only the *merge* block reads
        it: an unchanged block is not re-validated, so a form whose link column has
        since been deleted stays editable instead of becoming unsaveable. See
        `_validate_merge`.
        """
        referenced = {field.column_uuid for field in config.fields}
        if referenced:
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

        if config.merge is not None:
            unchanged = stored is not None and stored.get("merge") == config.merge.model_dump(
                mode="json"
            )
            if not unchanged:
                await cls._validate_merge(db, list_id, config.merge, user)

    @staticmethod
    async def _validate_merge(
        db: AsyncSession, list_id: int, merge: MergeConfig, user
    ) -> None:
        """Check a merged form's link column and the fields it borrows.

        **The target must be readable with `view`, never merely `lookup`.** That is the
        one rule here worth stating loudly. `lookup` exists so a reference list can
        resolve other people's row labels while staying shut — it discloses the display
        column and nothing else. A merged form puts *arbitrary* columns of the target on
        screen, so accepting `lookup` would quietly turn the narrowest action in the
        model into full read access to someone else's list. Config time is the right
        place to ask, because a form is a stored artefact handed to other people.
        """
        from src.permissions.enums import Action, ResourceType
        from src.permissions.services import PermissionService

        link = (
            await db.execute(
                select(Column).where(
                    Column.list_id == list_id,
                    Column.uuid == merge.link_column_uuid,
                    in_shape(),
                )
            )
        ).scalars().first()
        if link is None:
            raise UnknownReferenceError(
                "A merged form's link column must be a column of this list"
            )
        if link.type != ColumnType.lookup:
            raise UnknownReferenceError(
                f"Column '{link.name}' is not a lookup, so a merged form cannot "
                f"reach another list through it"
            )

        link_config = link.config or {}
        if link_config.get("multiple"):
            # One merged record pairs with one linked record. A multi-valued link has
            # no single record to write the borrowed fields into.
            raise UnknownReferenceError(
                f"Lookup column '{link.name}' allows several values, so a merged form "
                f"cannot decide which linked record its fields belong to"
            )
        if link_config.get("parent_column_uuid"):
            # A nested lookup's options depend on a sibling cell of the same record,
            # which a merged create has not written yet when it needs the target.
            raise UnknownReferenceError(
                f"Lookup column '{link.name}' is nested under another column, so it "
                f"cannot also be a merged form's link"
            )

        target = (
            await db.execute(
                select(List).where(
                    List.uuid == coerce_uuid(link_config.get("target_list_uuid")),
                    List.deleted_at.is_(None),
                )
            )
        ).scalars().first()
        if target is None:
            raise UnknownReferenceError(
                f"Lookup column '{link.name}' points at a missing target list"
            )
        if not await PermissionService.has(
            db, user, ResourceType.list, target.id, Action.view
        ):
            # Refused the same way a missing list is, so this cannot be used to learn
            # that a list exists — the rule `_validate_config` already follows for a
            # lookup target the caller cannot reach.
            raise UnknownReferenceError(
                f"Lookup column '{link.name}' points at a missing target list"
            )

        if not merge.fields:
            # Empty means *no* borrowed fields, unlike `FormConfig.fields`, where empty
            # means every column. The other list is not this form's to enumerate: "all
            # of them" would silently widen as the target gains columns, putting fields
            # on screen that nobody chose. Nothing to check, so stop here.
            return

        borrowed = {field.column_uuid for field in merge.fields}
        rows = (
            await db.execute(
                select(Column.uuid, Column.type, Column.name).where(
                    Column.list_id == target.id,
                    Column.uuid.in_(borrowed),
                    in_shape(),
                )
            )
        ).all()
        found = {row.uuid for row in rows}
        missing = [str(u) for u in borrowed if u not in found]
        if missing:
            raise UnknownReferenceError(
                f"Merged form references column(s) not on the linked list: "
                f"{', '.join(missing)}"
            )
        calculated = [row.name for row in rows if row.type in COMPUTED_TYPES]
        if calculated:
            # Its formula is its only writer, so it can never be an input — the rule
            # create/update forms already follow for their own list's columns.
            raise UnknownReferenceError(
                f"A merged form cannot borrow calculated column(s): "
                f"{', '.join(sorted(calculated))}"
            )
