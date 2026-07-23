from datetime import datetime, timezone
from typing import Optional, Sequence, Union
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy import null as sa_null, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.core.exceptions import ConflictError, UnknownReferenceError, ValidationError
from src.core.utils import LIKE_ESCAPE, coerce_uuid, like_pattern
from src.workspaces.models import Workspace

from ..items.models import ListItem
from ..models import List
from ..refs import get_by_ref
from .enums import PRINCIPAL_TYPES, REFERENCE_TYPES, ColumnType
from .models import Column
from .schemas import ColumnConfig, ColumnCreate, ColumnUpdate
from .validation import (
    ONUPDATE_TYPES,
    dynamic_tokens,
    is_dynamic_default,
    resolve_choice_options,
    validate_config_constraints,
    validate_scalar,
)


# Suggestions returned when a picker searches (rather than resolving known rows).
_OPTIONS_PAGE = 25


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
    async def get_by_ref(
        cls, db: AsyncSession, list_id: int, ref: Union[str, UUID]
    ) -> Optional[Column]:
        """A column addressed by uuid or by name — see src.lists.refs."""
        return await get_by_ref(db, Column, list_id, ref)

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
            old_target = (column.config or {}).get("target_list_uuid")
            # A column's type is immutable after creation; re-validate the new
            # config against the existing type, preserving settings not resent.
            column.config = await cls._validate_config(
                db,
                lst,
                column.type,
                payload.config,
                existing=column.config,
                current_uuid=column.uuid,
            )
            new_target = (column.config or {}).get("target_list_uuid")
            if old_target is not None and str(old_target) != str(new_target):
                # Retargeting silently invalidates any nesting pairing that
                # references this column — the filter column no longer points
                # where the pairing assumed. Un-nest the dependents.
                await cls._clear_dependent_nesting(db, column)
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
        # Nor a nested lookup pointing at a deleted parent or filter column.
        await cls._clear_dependent_nesting(db, column)
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
        limit: Optional[int] = None,
        parent_values: Optional[Sequence[UUID]] = None,
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

        # Asking for specific rows means wanting all of them: a search's page size
        # would silently drop the labels past it, and the caller would render row
        # stubs with no indication anything was truncated.
        if limit is None:
            limit = len(uuids) if uuids else _OPTIONS_PAGE

        key = await display_column_key(db, target.id, config)
        label = ListItem.data[key].astext if key else sa_null()

        stmt = select(
            ListItem.uuid, label.label("label"), ListItem.deleted_at
        ).where(
            ListItem.list_id == target.id,
            await PermissionService.item_view_filter(db, user, target),
        )

        # Resolving and offering are different questions, and a deleted row answers
        # them differently. A grid asking for *specific* uuids is labelling what its
        # records already hold, and one of those rows may be in the bin — it must
        # still get a name, or a live relationship renders as an unidentifiable stub.
        # A *search* is offering choices, and nothing new may be linked to a row that
        # is on its way out.
        if uuids is not None:
            stmt = stmt.where(ListItem.uuid.in_(uuids))
        else:
            stmt = stmt.where(ListItem.deleted_at.is_(None))
            # Nested lookup: a search offers only rows belonging to the parent's
            # selection. Resolving (`uuids`) is deliberately unfiltered — a held
            # value must always get its label, however stale. The filter column
            # comes from stored config, never from the caller; on a column that
            # is not nested the param is ignored, so a picker racing a config
            # edit degrades to an unfiltered search rather than erroring.
            filter_uuid = config.get("target_filter_column_uuid")
            if parent_values and filter_uuid is not None:
                # `?|` is true for a scalar cell equal to any of the values and
                # for an array cell sharing any element — both target shapes.
                stmt = stmt.where(
                    ListItem.data[str(filter_uuid)].has_any(
                        sa.literal(
                            [str(u) for u in parent_values],
                            postgresql.ARRAY(sa.Text),
                        )
                    )
                )
        if q and q.strip() and key:
            stmt = stmt.where(
                ListItem.deleted_at.is_(None),
                label.ilike(like_pattern(q.strip()), escape=LIKE_ESCAPE),
            )

        rows = await db.execute(stmt.order_by(label, ListItem.id).limit(limit))
        return [
            {"uuid": uuid, "label": text, "deleted": deleted_at is not None}
            for uuid, text, deleted_at in rows
        ]

    @classmethod
    async def _validate_config(
        cls,
        db: AsyncSession,
        lst: List,
        type_: ColumnType,
        config: ColumnConfig,
        existing: Optional[dict] = None,
        current_uuid: Optional[UUID] = None,
    ) -> dict:
        """Validate type-specific settings and return the JSON-ready dict to store.

        ``existing`` is the column's current stored config (on update), used to
        keep settings the caller did not resend (choice options included).
        ``current_uuid`` is the column being updated (absent on create), needed
        to refuse a nested lookup nesting under itself or its own descendants.
        """
        # Only the keys the caller explicitly set on this request, so that on
        # update the merge below preserves stored settings left unspecified.
        data = config.model_dump(mode="json", exclude_unset=True)

        if type_ == ColumnType.choice:
            options = resolve_choice_options(data, existing)
            if not options:
                raise ValidationError("A choice column requires at least one option")
            if len(options) != len(set(options)):
                raise ValidationError("Choice options must be unique")
            data["options"] = options
            data.pop("choices", None)

        # On update, a config PATCH merges onto the stored config rather than
        # replacing it, so settings the caller didn't resend are preserved.
        if existing:
            for key, value in existing.items():
                data.setdefault(key, value)

        if type_ == ColumnType.choice:
            # A cell stores the option itself, so a default must be one of them.
            # Checked after the merge: an edit that drops an option must not let
            # a stored default keep pointing at it.
            default = data.get("default")
            if isinstance(default, str) and default not in set(data["options"]):
                raise ValidationError(
                    f"'{default}' is not one of the column's options"
                )

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

            await cls._validate_nesting(
                db, lst, data, target_list_id, current_uuid
            )

        if type_ == ColumnType.calculated:
            from .formula import column_refs, parse

            formula = parse(data.get("formula"))
            # Every referenced name must be an existing number column of this list.
            # A calculated column referencing itself, or another calculated column,
            # is caught here too: on update it is among these siblings and is not a
            # `number`; on create its name is not present at all.
            siblings = (
                await db.execute(
                    select(Column).where(
                        Column.list_id == lst.id, Column.deleted_at.is_(None)
                    )
                )
            ).scalars().all()
            by_name = {c.name: c for c in siblings}
            referenceable = {
                ColumnType.number,
                ColumnType.text,
                ColumnType.long_text,
            }
            for name in sorted(column_refs(formula)):
                target = by_name.get(name)
                if target is None:
                    raise ValidationError(
                        f"Formula references unknown column '{name}'"
                    )
                if target.type not in referenceable:
                    raise ValidationError(
                        f"Formula can only reference number or text columns; "
                        f"'{name}' is {target.type.value}"
                    )
            if data.get("default") is not None:
                raise ValidationError("A calculated column cannot have a default")

        if type_ in PRINCIPAL_TYPES and data.get("default") is not None:
            # A user column may default to the acting user — a dynamic token resolved
            # per record, not a stored account id, so nothing rots when accounts are
            # deleted. Any other default (a named account, a group) would, so it is
            # refused, the same call lookup makes.
            if not (
                type_ == ColumnType.user
                and is_dynamic_default(type_, data.get("default"))
            ):
                raise ValidationError(
                    "A user column's default can only be the current user"
                    if type_ == ColumnType.user
                    else f"A {type_.value} column cannot have a default value"
                )

        # An on-update value is only meaningful for the types with a dynamic token
        # to re-stamp (a "last modified" date, a "last edited by" user), and must be
        # one of those tokens — there is no static onupdate.
        onupdate = data.get("onupdate")
        if onupdate is not None:
            if type_ not in ONUPDATE_TYPES:
                raise ValidationError(
                    "An on-update value is only available for date, time, datetime, "
                    "and user columns"
                )
            if not is_dynamic_default(type_, onupdate):
                allowed = ", ".join(sorted(dynamic_tokens(type_)))
                raise ValidationError(
                    f"An on-update value for a {type_.value} column must be one of: "
                    f"{allowed}"
                )

        if type_ == ColumnType.link and isinstance(data.get("default"), str):
            # Stored stripped, like a link value, so the default that is written into
            # records is byte-for-byte the one that was validated.
            data["default"] = data["default"].strip()

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

    @staticmethod
    async def _validate_nesting(
        db: AsyncSession,
        lst: List,
        data: dict,
        target_list_id: int,
        current_uuid: Optional[UUID],
    ) -> None:
        """Check a lookup's nesting pair, if configured (see ColumnConfig).

        Runs after the config merge, so a PATCH that resends neither key is
        checked against what is stored — an edit cannot silently break an
        existing pairing.
        """
        parent_uuid = coerce_uuid(data.get("parent_column_uuid"))
        filter_uuid = coerce_uuid(data.get("target_filter_column_uuid"))
        if parent_uuid is None and filter_uuid is None:
            return
        if parent_uuid is None or filter_uuid is None:
            raise ValidationError(
                "parent_column_uuid and target_filter_column_uuid must be "
                "set together"
            )
        if current_uuid is not None and parent_uuid == current_uuid:
            raise ValidationError("A lookup column cannot be nested under itself")

        # The parent is a sibling: same list, and itself a lookup — its cell is
        # what the filter compares against, so anything else has no row uuid to
        # match on.
        siblings = {
            str(column.uuid): column
            for column in (
                await db.execute(
                    select(Column).where(
                        Column.list_id == lst.id, Column.deleted_at.is_(None)
                    )
                )
            ).scalars().all()
        }
        parent = siblings.get(str(parent_uuid))
        if parent is None:
            raise UnknownReferenceError(
                "parent_column_uuid does not reference a column of this list"
            )
        if parent.type != ColumnType.lookup:
            raise ValidationError(
                f"Cannot nest under '{parent.name}': it is not a lookup column"
            )

        # The filter column lives on *this* column's target list and must point
        # back at the parent's target list — that is what makes the pairing mean
        # "target rows belonging to the parent's selection".
        filter_column = (
            await db.execute(
                select(Column).where(
                    Column.list_id == target_list_id,
                    Column.uuid == filter_uuid,
                    Column.deleted_at.is_(None),
                )
            )
        ).scalars().first()
        if filter_column is None:
            raise UnknownReferenceError(
                "target_filter_column_uuid does not reference a column of the "
                "target list"
            )
        if filter_column.type != ColumnType.lookup:
            raise ValidationError(
                f"Cannot match on '{filter_column.name}': it is not a lookup "
                "column"
            )
        parent_target = (parent.config or {}).get("target_list_uuid")
        filter_target = (filter_column.config or {}).get("target_list_uuid")
        if (
            parent_target is None
            or filter_target is None
            or str(parent_target) != str(filter_target)
        ):
            raise ValidationError(
                f"'{filter_column.name}' does not point at the same list as "
                f"'{parent.name}'"
            )

        # Nesting edges must form a forest: walk up from the parent and refuse
        # to close a loop. Chains (A under B under C) are fine.
        if current_uuid is not None:
            seen = set()
            cursor = parent
            while cursor is not None:
                if cursor.uuid == current_uuid:
                    raise ValidationError(
                        "Nesting would create a cycle between lookup columns"
                    )
                if str(cursor.uuid) in seen:
                    break
                seen.add(str(cursor.uuid))
                next_uuid = (cursor.config or {}).get("parent_column_uuid")
                cursor = (
                    siblings.get(str(next_uuid)) if next_uuid is not None else None
                )

    @staticmethod
    async def _clear_dependent_nesting(db: AsyncSession, column: Column) -> None:
        """Un-nest every lookup whose pairing references ``column``.

        Called when a column is deleted or a parent lookup is retargeted —
        either way the pairing no longer means anything, and a dangling one
        would quietly stop filtering. Mirrors how `delete` nulls a list's
        `primary_column_id` rather than blocking the delete. Parent references
        are same-list, filter-column references come from other lists, so the
        scan is global.
        """
        uuid_text = str(column.uuid)
        dependents = (
            await db.execute(
                select(Column).where(
                    Column.deleted_at.is_(None),
                    sa.or_(
                        Column.config["parent_column_uuid"].astext == uuid_text,
                        Column.config["target_filter_column_uuid"].astext
                        == uuid_text,
                    ),
                )
            )
        ).scalars().all()
        for dependent in dependents:
            config = dict(dependent.config or {})
            config["parent_column_uuid"] = None
            config["target_filter_column_uuid"] = None
            # Reassigned, not mutated, so SQLAlchemy sees the JSONB change.
            dependent.config = config
