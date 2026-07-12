from datetime import datetime, timezone
from typing import Optional, Sequence, Union
from uuid import UUID

from sqlalchemy import null as sa_null, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.core.exceptions import ConflictError, UnknownReferenceError, ValidationError
from src.core.utils import LIKE_ESCAPE, coerce_uuid, like_pattern
from src.workspaces.models import Workspace

from ..items.models import ListItem
from ..models import List
from .enums import PRINCIPAL_TYPES, REFERENCE_TYPES, ColumnType
from .models import Column
from .schemas import ColumnConfig, ColumnCreate, ColumnUpdate
from .validation import (
    is_dynamic_default,
    resolve_choice_options,
    validate_config_constraints,
    validate_scalar,
)


class ColumnService:
    @staticmethod
    def _select(list_id: int):
        return select(Column).where(
            Column.deleted_at.is_(None), Column.list_id == list_id
        )

    @classmethod
    async def get_by_uuid(
        cls, db: AsyncSession, list_id: int, uuid: Union[str, UUID]
    ) -> Optional[Column]:
        parsed = coerce_uuid(uuid)
        if parsed is None:
            return None
        result = await db.execute(cls._select(list_id).where(Column.uuid == parsed))
        return result.scalars().first()

    @classmethod
    async def list(cls, db: AsyncSession, list_id: int) -> Sequence[Column]:
        result = await db.execute(
            cls._select(list_id).order_by(Column.position, Column.id)
        )
        return result.scalars().all()

    @classmethod
    async def create(
        cls, db: AsyncSession, lst: List, payload: ColumnCreate
    ) -> Column:
        config = await cls._validate_config(db, lst, payload.type, payload.config)
        column = Column(
            list_id=lst.id,
            name=payload.name,
            display_name=payload.display_name,
            type=payload.type,
            position=payload.position,
            config=config,
        )
        db.add(column)
        try:
            await db.flush()
            new_uuid = column.uuid
            await db.commit()
        except IntegrityError as exc:
            await db.rollback()
            raise ConflictError(
                "A column with this name already exists on the list"
            ) from exc

        return await cls.get_by_uuid(db, lst.id, new_uuid)

    @classmethod
    async def update(
        cls,
        db: AsyncSession,
        lst: List,
        uuid: Union[str, UUID],
        payload: ColumnUpdate,
    ) -> Optional[Column]:
        column = await cls.get_by_uuid(db, lst.id, uuid)
        if column is None:
            return None

        data = payload.model_dump(exclude_unset=True)

        if "config" in data:
            # A column's type is immutable after creation; re-validate the new
            # config against the existing type, preserving choice option ids.
            column.config = await cls._validate_config(
                db, lst, column.type, payload.config, existing=column.config
            )
            data.pop("config")
        if data.get("name") is None:
            data.pop("name", None)
        if data.get("display_name") is None:
            data.pop("display_name", None)
        if data.get("position") is None:
            data.pop("position", None)

        for field, value in data.items():
            setattr(column, field, value)

        try:
            await db.flush()
            target_uuid = column.uuid
            await db.commit()
        except IntegrityError as exc:
            await db.rollback()
            raise ConflictError(
                "A column with this name already exists on the list"
            ) from exc

        return await cls.get_by_uuid(db, lst.id, target_uuid)

    @classmethod
    async def delete(
        cls, db: AsyncSession, lst: List, uuid: Union[str, UUID]
    ) -> bool:
        column = await cls.get_by_uuid(db, lst.id, uuid)
        if column is None:
            return False

        column.deleted_at = datetime.now(timezone.utc)
        # Don't leave the list pointing at a deleted primary field.
        if lst.primary_column_id == column.id:
            lst.primary_column_id = None
        await db.commit()
        return True

    @classmethod
    async def lookup_options(
        cls,
        db: AsyncSession,
        lst: List,
        column: Column,
        user,
        q: Optional[str] = None,
        uuids: Optional[Sequence[UUID]] = None,
        limit: int = 25,
    ) -> list[dict]:
        """Rows of a lookup's target list, as ``{uuid, label}`` options.

        Two callers, one endpoint: a picker searching by label (``q``), and a grid
        resolving the uuids it already holds (``uuids``). Resolved server-side
        because only the server knows which column supplies a row's label — the
        same `display_column_key` the export and the import use, so a label means
        the same thing everywhere.

        Sees exactly what the caller may see: `view` on the target list, then the
        per-record filter. A picker must never offer a row its user cannot read.
        """
        # Deferred like the other permission call sites: a module-level import
        # cycles back through auth/workspaces services.
        from src.permissions.enums import Action, ResourceType
        from src.permissions.services import PermissionService

        from ..items.services import display_column_key

        if column.type != ColumnType.lookup:
            raise ValidationError(f"Column '{column.name}' is not a lookup column")

        config = column.config or {}
        target = (
            await db.execute(
                select(List)
                .options(selectinload(List.workspace))
                .where(
                    List.uuid == coerce_uuid(config.get("target_list_uuid")),
                    List.deleted_at.is_(None),
                )
            )
        ).scalars().first()
        if target is None:
            raise UnknownReferenceError(
                f"Lookup column '{column.name}' points at a missing target list"
            )
        await PermissionService.require(
            db, user, ResourceType.list, target.id, Action.view
        )

        key = await display_column_key(db, target.id, config)
        label = ListItem.data[key].astext if key else sa_null()

        stmt = select(ListItem.uuid, label.label("label")).where(
            ListItem.list_id == target.id,
            ListItem.deleted_at.is_(None),
            await PermissionService.item_view_filter(db, user, target),
        )
        if uuids is not None:
            stmt = stmt.where(ListItem.uuid.in_(uuids))
        if q and q.strip() and key:
            stmt = stmt.where(label.ilike(like_pattern(q.strip()), escape=LIKE_ESCAPE))

        rows = await db.execute(stmt.order_by(label, ListItem.id).limit(limit))
        return [{"uuid": uuid, "label": text} for uuid, text in rows]

    @staticmethod
    async def _validate_config(
        db: AsyncSession,
        lst: List,
        type_: ColumnType,
        config: ColumnConfig,
        existing: Optional[dict] = None,
    ) -> dict:
        """Validate type-specific settings and return the JSON-ready dict to store.

        ``existing`` is the column's current stored config (on update), used to
        keep choice option ids stable across edits.
        """
        # Only the keys the caller explicitly set on this request, so that on
        # update the merge below preserves stored settings left unspecified.
        data = config.model_dump(mode="json", exclude_unset=True)

        if type_ == ColumnType.choice:
            options = resolve_choice_options(data, existing)
            if not options:
                raise ValidationError("A choice column requires at least one option")
            ids = [opt["id"] for opt in options]
            if len(ids) != len(set(ids)):
                raise ValidationError("Choice option ids must be unique")
            data["options"] = options
            data.pop("choices", None)
            # Let a default be given by option label; store it as the option id.
            default = data.get("default")
            if isinstance(default, str) and default not in set(ids):
                by_label = {opt["label"]: opt["id"] for opt in options}
                if default in by_label:
                    data["default"] = by_label[default]

        # On update, a config PATCH merges onto the stored config rather than
        # replacing it, so settings the caller didn't resend are preserved.
        if existing:
            for key, value in existing.items():
                data.setdefault(key, value)

        if type_ == ColumnType.lookup:
            target_list_uuid = data.get("target_list_uuid")
            if target_list_uuid is None:
                raise ValidationError("A lookup column requires a target_list_uuid")
            if data.get("default") is not None:
                raise ValidationError("A lookup column cannot have a default value")
            result = await db.execute(
                select(List.id)
                .join(Workspace, List.workspace_id == Workspace.id)
                .where(
                    List.uuid == coerce_uuid(target_list_uuid),
                    Workspace.owner_id == lst.workspace.owner_id,
                    List.deleted_at.is_(None),
                    Workspace.deleted_at.is_(None),
                )
            )
            target_list_id = result.scalars().first()
            if target_list_id is None:
                raise UnknownReferenceError(
                    "target_list_uuid does not reference a list you own"
                )

            display_column_uuid = data.get("display_column_uuid")
            if display_column_uuid is not None:
                exists = await db.execute(
                    select(Column.id).where(
                        Column.list_id == target_list_id,
                        Column.uuid == coerce_uuid(display_column_uuid),
                        Column.deleted_at.is_(None),
                    )
                )
                if exists.scalars().first() is None:
                    raise UnknownReferenceError(
                        "display_column_uuid does not reference a column of the "
                        "target list"
                    )

        if type_ in PRINCIPAL_TYPES and data.get("default") is not None:
            # A default would have to name a specific account or group, which then
            # rots when that account is deleted. Same call as lookup makes.
            raise ValidationError(f"A {type_.value} column cannot have a default value")

        # Numeric/temporal/text settings must be well-formed (min <= max, ...).
        validate_config_constraints(type_, data)

        # A configured default must itself be a valid value for the column
        # (dynamic tokens like "today"/"now" are resolved at insert time).
        # Reference types are excluded: their values are checked against the
        # database, which the pure validators cannot do.
        default = data.get("default")
        if (
            default is not None
            and type_ not in REFERENCE_TYPES
            and not is_dynamic_default(type_, default)
        ):
            validate_scalar(type_, data, "default", default)

        return data
