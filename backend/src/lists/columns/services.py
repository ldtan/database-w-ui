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

from ..items.models import ListItem
from ..models import List
from . import backfill
from .enums import COMPUTED_TYPES, PRINCIPAL_TYPES, REFERENCE_TYPES, ColumnType
from .models import Column, in_shape
from .schemas import ColumnConfig, ColumnCreate, ColumnRead, ColumnUpdate
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
        return select(Column).where(in_shape(), Column.list_id == list_id)

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
    async def get_including_hidden(
        cls, db: AsyncSession, list_id: int, uuid: Union[str, UUID]
    ) -> Optional[Column]:
        """A column whether or not it is in the list's shape.

        Only the conversion machinery wants this — it has to read and write the
        scratch column it is filling, which by design no ordinary reader can see.
        """
        parsed = coerce_uuid(uuid)
        if parsed is None:
            return None
        result = await db.execute(
            select(Column).where(
                Column.deleted_at.is_(None),
                Column.list_id == list_id,
                Column.uuid == parsed,
            )
        )
        return result.scalars().first()

    @classmethod
    async def list(cls, db: AsyncSession, list_id: int) -> Sequence[Column]:
        result = await db.execute(
            cls._select(list_id).order_by(Column.position, Column.id)
        )
        return result.scalars().all()

    # -- rendering ----------------------------------------------------------
    #
    # `recompute_pending` is the one field of `ColumnRead` that is not a property of
    # the column row: it is a question about the *list* — do this column's stored
    # values match the schema its formulas are read against — so it cannot be answered
    # by `model_validate` on one row. These two are the seam every response goes
    # through, so an endpoint cannot accidentally return the field's default and
    # quietly report a stale column as fresh.

    @staticmethod
    def _read(column: Column, pending: set[str]) -> ColumnRead:
        read = ColumnRead.model_validate(column)
        read.recompute_pending = str(column.uuid) in pending
        return read

    @classmethod
    def read_all(cls, columns: Sequence[Column]) -> list[ColumnRead]:
        """Render the list's whole shape. Costs no extra query, because these columns
        *are* the shape the staleness check needs."""
        pending = backfill.pending_keys({str(c.uuid): c for c in columns})
        return [cls._read(column, pending) for column in columns]

    @classmethod
    async def read_one(
        cls, db: AsyncSession, list_id: int, column: Column
    ) -> ColumnRead:
        """Render one column, fetching the shape around it — a single column cannot
        answer the staleness question on its own."""
        pending = backfill.pending_keys(await backfill.list_columns(db, list_id))
        return cls._read(column, pending)

    @classmethod
    async def create(
        cls, db: AsyncSession, lst: List, payload: ColumnCreate, user
    ) -> Column:
        config = await cls._validate_config(
            db, lst, payload.type, payload.config, user, name=payload.name
        )
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

        # A new calculated column has no values in any record yet, so it would read
        # blank on every existing row until each was next saved. Fill them in now —
        # they are stored values, and a column that only works for records created
        # after it is not a column anyone can filter or sort by.
        if payload.type in COMPUTED_TYPES:
            await backfill.recompute_records(db, lst.id)

        return await cls.get_by_uuid(db, lst.id, new_uuid)

    @classmethod
    async def update(
        cls,
        db: AsyncSession,
        lst: List,
        uuid: Union[str, UUID],
        payload: ColumnUpdate,
        user,
    ) -> Optional[Column]:
        column = await cls.get_by_uuid(db, lst.id, uuid)
        if column is None:
            return None

        data = payload.model_dump(exclude_unset=True)
        # Captured before anything is applied: the two questions a recompute turns on
        # are "did the formula change" and "did a name a formula reads change", and
        # both need the before picture.
        old_name = column.name
        old_config = dict(column.config or {})

        if "config" in data:
            old_target = (column.config or {}).get("target_list_uuid")
            # A column's type is immutable after creation; re-validate the new
            # config against the existing type, preserving settings not resent.
            column.config = await cls._validate_config(
                db,
                lst,
                column.type,
                payload.config,
                user,
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

        await cls._resettle_formulas(
            db,
            lst,
            touched=column,
            renamed_from=old_name if column.name != old_name else None,
            formula_changed=old_config.get("formula")
            != (column.config or {}).get("formula"),
        )
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
        # A formula that read this column can no longer answer, so the cells it fed
        # have to go blank — leaving the last computed value would leave a number that
        # reads as current sitting in a filterable, sortable column.
        await cls._resettle_formulas(db, lst, touched=column, gone=True)
        return True

    @classmethod
    async def resettle_after_restore(
        cls, db: AsyncSession, lst: List, column: Column
    ) -> None:
        """`_resettle_formulas` for a column coming back out of the bin.

        A restore is the mirror of a delete and needs the same treatment: `gone` says
        "this column's presence in the list just changed", which is as true of arriving
        as of leaving.
        """
        await cls._resettle_formulas(db, lst, touched=column, gone=True)

    @classmethod
    async def _resettle_formulas(
        cls,
        db: AsyncSession,
        lst: List,
        *,
        touched: Column,
        renamed_from: Optional[str] = None,
        formula_changed: bool = False,
        gone: bool = False,
    ) -> None:
        """Put the list's stored calculated values back in step after a schema change.

        The one place that decides whether a column edit was the kind a formula cares
        about, so the answer cannot differ between create, update, delete and restore.
        Three things can move a stored calculated value: the formula itself changed,
        the column *is* calculated and just appeared or disappeared, or a column some
        formula reads by name was renamed or removed out from under it.

        Deliberately narrow. A backfill rewrites the whole list, so running it on every
        column PATCH would make an unrelated edit — widening a text column's
        `max_length`, say — cost a full table scan.
        """
        calculated = touched.type in COMPUTED_TYPES
        if not (calculated or renamed_from or gone):
            return
        if calculated and not (formula_changed or gone or renamed_from):
            # A calculated column whose formula did not move: only `compute_order` and
            # cosmetics can have changed, and neither alters a single cell.
            await backfill.sync_orders(db, lst.id)
            return

        columns = await backfill.list_columns(db, lst.id)
        if not calculated and not backfill.references_a_formula(
            columns, touched.name, renamed_from
        ):
            return  # nothing reads it; nothing to redo

        await backfill.sync_orders(db, lst.id)
        await backfill.recompute_records(db, lst.id)

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

        Sees exactly what the caller may see: `lookup` on the target list (which
        `view` confers), then the per-record filter. A picker must never offer a row
        its user cannot read.
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
            db, user, ResourceType.list, target.id, Action.lookup
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
            await PermissionService.item_view_filter(
                db, user, target, Action.lookup
            ),
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
        user,
        existing: Optional[dict] = None,
        current_uuid: Optional[UUID] = None,
        name: Optional[str] = None,
    ) -> dict:
        """Validate type-specific settings and return the JSON-ready dict to store.

        ``user`` is the caller: a lookup may point at any list they can *view*,
        so its target is checked against their permissions, not the list's owner.
        ``existing`` is the column's current stored config (on update), used to
        keep settings the caller did not resend (choice options included).
        ``current_uuid`` is the column being updated (absent on create), needed
        to refuse a nested lookup nesting under itself or its own descendants.
        ``name`` is the column's name on create, where there is no row to read it
        from yet and a formula's dependency graph still needs to know what to call
        the node it is about to add.
        """
        # Only the keys the caller explicitly set on this request, so that on
        # update the merge below preserves stored settings left unspecified.
        data = config.model_dump(mode="json", exclude_unset=True)
        # What the caller actually sent, before the merge below folds the stored
        # config in. `compute_order` needs the distinction: a value this request asked
        # for is a choice to honour or refuse, while the same number arriving from
        # storage is only where the column happened to sit last time.
        sent = set(data)

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
            # A lookup may target any list the caller can `lookup` — their own,
            # one in another of their workspaces, one shared with them, or one
            # shared *only* as a lookup target. That last case is the whole reason
            # to grant `lookup`, so it has to be enough to build the column, not
            # just to read it afterwards. Resolving labels re-checks the target's
            # permissions per caller (see `lookup_options`), so this only has to
            # establish that this user may read a label out of the target at all.
            # Deferred import: a module-level one cycles back through
            # auth/workspaces services.
            from src.permissions.enums import Action, ResourceType
            from src.permissions.services import PermissionService

            target = (
                await db.execute(
                    select(List).where(
                        List.uuid == coerce_uuid(target_list_uuid),
                        List.deleted_at.is_(None),
                    )
                )
            ).scalars().first()
            # A target the caller cannot read is reported exactly like one that
            # does not exist, so the field cannot be used to probe for the
            # existence of lists the caller was never given.
            if target is None or not await PermissionService.has(
                db, user, ResourceType.list, target.id, Action.lookup
            ):
                raise UnknownReferenceError(
                    "target_list_uuid does not reference a list you can look up"
                )
            target_list_id = target.id

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
            await cls._validate_formula(db, lst, data, name, current_uuid, sent)

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

    @classmethod
    async def _validate_formula(
        cls,
        db: AsyncSession,
        lst: List,
        data: dict,
        name: Optional[str],
        current_uuid: Optional[UUID],
        sent: set[str],
    ) -> None:
        """Check a calculated column's formula, its chain, and its `compute_order`.

        Runs after the config merge, so a PATCH that resends only `compute_order` is
        checked against the stored formula rather than against nothing.

        A formula may reference another calculated column — that is the chain — so what
        used to be enforced by the referenceable *types* (self-reference and
        calc-on-calc were both simply "not a number column") is now enforced by the
        graph instead: the only thing refused is a loop, including the one-step loop of
        a column naming itself.
        """
        from .chain import check_order, derive_order, find_cycle, formula_edges
        from .formula import column_refs, parse

        formula = parse(data.get("formula"))
        if data.get("default") is not None:
            raise ValidationError("A calculated column cannot have a default")

        siblings = (
            await db.execute(
                select(Column).where(
                    Column.list_id == lst.id, Column.deleted_at.is_(None)
                )
            )
        ).scalars().all()
        by_name = {c.name: c for c in siblings}

        # The name this column will be known by. On update the row already exists and
        # its stored name is authoritative — a rename in the same PATCH is applied
        # after this, and renaming a column that others read is a separate hazard the
        # caller is warned about rather than one the graph can resolve here.
        current = next(
            (c for c in siblings if current_uuid is not None and c.uuid == current_uuid),
            None,
        )
        own_name = current.name if current is not None else name

        referenceable = {ColumnType.number, ColumnType.text, ColumnType.calculated}
        refs = column_refs(formula)
        for ref in sorted(refs):
            target = by_name.get(ref)
            if target is None:
                raise ValidationError(f"Formula references unknown column '{ref}'")
            if target.type not in referenceable:
                raise ValidationError(
                    f"Formula can only reference number, text or calculated columns; "
                    f"'{ref}' is {target.type.value}"
                )

        # The graph as it will be *after* this save: every other calculated column with
        # its stored formula, plus this one with the formula being saved. Built by name
        # because that is what a formula and an error message both speak. The siblings
        # are passed whole — including this column, whose entry is then overwritten —
        # so that *their* references to it are still recognised as edges.
        calculated_refs = {
            ref for ref in refs if by_name[ref].type == ColumnType.calculated
        }
        edges = formula_edges(
            list(siblings), frozenset({own_name}) if own_name else frozenset()
        )
        if own_name is not None:
            edges[own_name] = calculated_refs
            loop = find_cycle(edges, own_name)
            if loop is not None:
                raise ValidationError(
                    "That formula would make a loop: "
                    + " → ".join(loop)
                    + ". A calculated column may build on another, but the chain has "
                    "to end somewhere."
                )

        # `compute_order` last, once the dependencies it has to agree with are known.
        # An explicit value is refused when it contradicts the graph; a stored one is
        # only a floor, so adding a dependency further down the chain lifts the column
        # past it instead of 400ing an edit nobody made about a number nobody typed.
        explicit = data.get("compute_order")
        if explicit is not None and "compute_order" in sent:
            if not isinstance(explicit, int) or explicit < 0:
                raise ValidationError("compute_order must be a whole number, 0 or more")
            check_order(own_name or "this column", calculated_refs, by_name, explicit)
        data["compute_order"] = derive_order(calculated_refs, by_name, explicit)

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
