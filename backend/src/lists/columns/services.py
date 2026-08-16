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

from ..items.filtering import apply_filters
from ..items.models import ListItem
from ..models import List
from .enums import (
    COMPUTED_TYPES,
    FORMULA_REFERENCEABLE_TYPES,
    PRINCIPAL_TYPES,
    REFERENCE_TYPES,
    ColumnType,
)
from .models import Column, in_shape
from .resanitize import resanitize_column, turns_rich
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
    async def list(cls, db: AsyncSession, list_id: int) -> Sequence[Column]:
        result = await db.execute(
            cls._select(list_id).order_by(Column.position, Column.id)
        )
        return result.scalars().all()

    @classmethod
    async def create(
        cls, db: AsyncSession, lst: List, payload: ColumnCreate, user
    ) -> Column:
        config = await cls._validate_config(
            db, lst, payload.type, payload.config, user, current_name=payload.name
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
        # Captured before `_validate_config` overwrites it: turning `text_mode` on is
        # what makes every value already stored under this column renderable as HTML,
        # and the only way to know that happened is to compare against what was there.
        was = dict(column.config or {})

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
                # The name this save lands on: a PATCH may rename and re-formulate in
                # one request, and a cycle has to be judged against the name the
                # formula will be found under afterwards.
                current_name=payload.name or column.name,
            )
            new_target = (column.config or {}).get("target_list_uuid")
            if str(old_target) != str(new_target):
                # Retargeting silently invalidates any nesting pairing that
                # references this column — the filter column no longer points
                # where the pairing assumed. Un-nest the dependents.
                #
                # There is deliberately no `old_target is not None` guard. It was
                # redundant while every lookup had a target (for a non-lookup both
                # sides are None and the comparison is false anyway), and it stopped
                # being harmless the moment a deleted target list began clearing the
                # field: `is not None` would then skip exactly the column that most
                # needs its dependents un-nested — one being repaired after the list
                # it named was deleted. `ListService._scrub_references_to_lists`
                # already un-nests at delete time, so this is the second line rather
                # than the first, and it is cheap enough to keep.
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

        # In this transaction, so the column never reads as rich while a value written
        # under the plain one is still stored — see resanitize.py for why that ordering
        # is the whole point.
        if turns_rich(column.type, was, column.config or {}):
            await resanitize_column(db, lst.id, column.uuid)

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

        Sees exactly what the caller may see: `lookup` on the target list (which
        `view` confers), then the per-record filter. A picker must never offer a row
        its user cannot read.

        A search is narrowed further by whatever the column saved in
        `option_filters` — and only a search: resolving named uuids stays exempt,
        so a cell keeps its label after the filter stops offering the row. Nothing
        here reads a filter off the request. That asymmetry is the point; see the
        field's note in `schemas.ColumnConfig`.
        """
        # Deferred like the other permission call sites: a module-level import
        # cycles back through auth/workspaces services.
        from src.permissions.enums import Action, ResourceType
        from src.permissions.services import PermissionService

        from ..items.services import ListItemService, display_column_key

        if column.type != ColumnType.lookup:
            raise ValidationError(f"Column '{column.name}' is not a lookup column")

        config = column.config or {}
        if config.get("target_list_uuid") is None:
            # An unconfigured lookup offers nothing, rather than erroring. Deleting a
            # target list clears this field on every column that named it
            # (`ListService._scrub_references_to_lists`), and a column mid-edit can
            # legitimately be here too. Raising instead used to 404 the options
            # endpoint, which is what the picker, the record dialog and every label
            # resolution call — so one deleted list turned its dependants into columns
            # that could not even be looked at, let alone repaired. This is the same
            # tolerance a dangling nesting pairing already gets (known-gaps §7).
            return []
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
            # Still an error, and deliberately still distinct from the case above: a
            # config naming a list that is not there means something got out of step
            # — a hand-edited config, or a delete path that did not scrub — and
            # answering "no options" would hide it. The scrub is what keeps ordinary
            # deletion out of this branch.
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
            ListItem.uuid,
            label.label("label"),
            ListItem.deleted_at,
            ListItem.archived_at,
        ).where(
            ListItem.list_id == target.id,
            await PermissionService.item_view_filter(
                db, user, target, Action.lookup
            ),
        )

        # Resolving and offering are different questions, and a deleted or archived
        # row answers them differently. A grid asking for *specific* uuids is
        # labelling what its records already hold, and one of those rows may be in the
        # bin or the archive — it must still get a name, or a live relationship
        # renders as an unidentifiable stub. A *search* is offering choices, and
        # nothing new may be linked to a row that is on its way out or finished with.
        if uuids is not None:
            stmt = stmt.where(ListItem.uuid.in_(uuids))
        else:
            stmt = stmt.where(
                ListItem.deleted_at.is_(None), ListItem.archived_at.is_(None)
            )
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
            # The column's saved option filter, over the target's own columns.
            # Another `where` on the statement `item_view_filter` already
            # constrains, so it composes with the nesting above and can only ever
            # narrow — no arrangement of conditions reaches a row the caller was
            # not already allowed. Resolving is exempt for the usual reason: a
            # held value must keep its label even once the filter would no longer
            # offer it. `user` is read only to resolve "today"/"now" in their zone.
            #
            # `_columns_by_key` rather than a local query for the target's
            # columns, because it is the same map the grid filters through: it
            # keys by uuid string as `apply_filters` expects, and it is scoped by
            # `in_shape`, so a conversion's live-but-not-yet-a-field scratch
            # column cannot be filtered on here while being invisible everywhere
            # else.
            option_filters = config.get("option_filters")
            if option_filters:
                stmt = apply_filters(
                    stmt,
                    option_filters,
                    await ListItemService._columns_by_key(db, target.id),
                    user,
                )
        if q and q.strip() and key:
            stmt = stmt.where(
                ListItem.deleted_at.is_(None),
                ListItem.archived_at.is_(None),
                label.ilike(like_pattern(q.strip()), escape=LIKE_ESCAPE),
            )

        rows = await db.execute(stmt.order_by(label, ListItem.id).limit(limit))
        return [
            {
                "uuid": uuid,
                "label": text,
                "deleted": deleted_at is not None,
                "archived": archived_at is not None,
            }
            for uuid, text, deleted_at, archived_at in rows
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
        current_name: Optional[str] = None,
    ) -> dict:
        """Validate type-specific settings and return the JSON-ready dict to store.

        ``user`` is the caller: a lookup may point at any list they can *view*,
        so its target is checked against their permissions, not the list's owner.
        ``existing`` is the column's current stored config (on update), used to
        keep settings the caller did not resend (choice options included).
        ``current_uuid`` is the column being updated (absent on create), needed
        to refuse a nested lookup nesting under itself or its own descendants.
        ``current_name`` is the name this column will have once saved — the *new*
        one when a PATCH renames it — which is what a formula cycle is expressed in,
        since formulas reference columns by name.
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

        # `unique` and `multiple` together are refused, because together they do
        # nothing. `_enforce_unique` (items/services.py) skips any cell holding a
        # list, so a column carrying both accepted unlimited duplicates in silence
        # — the worst shape a constraint can take, since the setting is on screen
        # and the guarantee is absent. Refused here rather than made to work: the
        # question a multi-valued unique column would have to answer is "may two
        # records share *any* element", which is a different constraint needing its
        # own query and its own index, and nobody has asked for it. A column must
        # not claim something it does not do.
        #
        # Checked after the merge, so a PATCH turning either one on over a column
        # that already has the other is caught. Existing columns holding both are
        # left alone — there is no retro-validation, so the only thing that changes
        # for them is that the next config PATCH has to resolve it.
        if data.get("unique") and data.get("multiple"):
            raise ValidationError(
                "A column cannot be both 'unique' and 'multiple': uniqueness is "
                "not enforced on a multi-valued column. Turn one of them off."
            )

        if type_ == ColumnType.choice:
            # A cell stores the option itself, so a default must be one of them —
            # unless the column takes an answer of the record's own, in which case
            # a default off the list is as valid as a cell holding one (the shared
            # validator below still refuses a blank). Checked after the merge: an
            # edit that drops an option, or that turns `allow_other` off, must not
            # let a stored default keep pointing at what is no longer offered.
            default = data.get("default")
            if (
                isinstance(default, str)
                and not data.get("allow_other")
                and default not in set(data["options"])
            ):
                raise ValidationError(
                    f"'{default}' is not one of the column's options"
                )

        if type_ == ColumnType.lookup:
            target_list_uuid = data.get("target_list_uuid")
            if target_list_uuid is None:
                # Still required, including on the columns whose target was cleared
                # because the list it named was deleted. That is deliberate: a lookup
                # with nowhere to look is not a configuration worth storing, and the
                # only repairs are retargeting or deleting the column — both of which
                # this allows. What was actually broken before was not the strictness
                # here but that the *old* config survived: the dialog showed a dead
                # uuid as "…" under a hint about lookup-only sharing, so nobody could
                # tell what was wrong. With the scrub clearing it, the picker opens
                # empty and this message names the thing to fix.
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

            await cls._validate_option_filters(db, user, data, target_list_id)
            await cls._validate_nesting(
                db, lst, data, target_list_id, current_uuid
            )

        if type_ == ColumnType.calculated:
            from .chain import find_cycle, formula_edges
            from .formula import column_refs, parse

            formula = parse(data.get("formula"))
            # `in_shape`, not `deleted_at` alone: a hidden column is live but not a
            # field of the list, so `_columns_by_key` will never hand its value to a
            # formula. Nothing creates hidden columns now (see `Column.hidden`), but the
            # predicate has to keep agreeing with that reader whatever does. Letting one be referenced here would save a formula
            # whose operand is permanently missing, and — now that the answer is
            # stored — write a blank into something a filter can find.
            siblings = (
                await db.execute(
                    select(Column).where(Column.list_id == lst.id, in_shape())
                )
            ).scalars().all()
            by_name = {c.name: c for c in siblings}
            # `calculated` is among the referenceable types now that the value is
            # stored: a chain is computed front to back in one pass
            # (`chain.build_chain`), so reading another formula's answer is reading
            # this write's, not the last one's. Cycles are what that opens up, and
            # they are refused below.
            for name in sorted(column_refs(formula)):
                target = by_name.get(name)
                if target is None:
                    raise ValidationError(
                        f"Formula references unknown column '{name}'"
                    )
                if target.type not in FORMULA_REFERENCEABLE_TYPES:
                    # Listed from the set rather than spelled out, so a type added
                    # there can never be accepted by a message that denies it.
                    allowed = ", ".join(
                        sorted(t.value for t in FORMULA_REFERENCEABLE_TYPES)
                    )
                    raise ValidationError(
                        f"Formula can only reference {allowed} columns; "
                        f"'{name}' is {target.type.value}"
                    )
            if data.get("default") is not None:
                raise ValidationError("A calculated column cannot have a default")
            # `unique` *is* allowed, unlike `default`. A default is a value somebody
            # supplies and the formula would immediately overwrite; uniqueness is a
            # question about the answer, and a formula is a perfectly good way to
            # express a key that no single column holds — `CONCAT(course, section)`,
            # say. `_validate_data` writes calculated cells before `_enforce_unique`
            # reads them, so the check sees this write's answer, not the last one's.
            # What it compares against is the stored index of the *other* rows, so a
            # formula edit that has not been re-indexed yet is judged against the old
            # answers; see the note on staleness in `_enforce_unique`.

            # Self-reference used to be caught by the type check (the column was among
            # its own siblings and was not a `number`). Now that `calculated` is
            # referenceable it is a cycle of length one, and it comes out here with
            # every longer ring.
            if current_name:
                edges = formula_edges(siblings, also=frozenset({current_name}))
                calc_names = {
                    c.name for c in siblings if c.type in COMPUTED_TYPES
                } | {current_name}
                # The pending formula, not the stored one: on update `formula_edges`
                # read the config this save is replacing, and on create there is no
                # row to read at all.
                edges[current_name] = {
                    name for name in column_refs(formula) if name in calc_names
                }
                cycle = find_cycle(edges, current_name)
                if cycle is not None:
                    raise ValidationError(
                        "A formula cannot depend on itself: "
                        + " -> ".join(cycle)
                    )

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
    async def _validate_option_filters(
        db: AsyncSession,
        user,
        data: dict,
        target_list_id: int,
    ) -> None:
        """Check a lookup's saved option filter names real columns of the target.

        Worth checking at all because `apply_filters` *drops* a condition whose
        column it cannot find rather than failing (filtering.py), so a mistyped
        uuid would produce a filter that silently offers everything — the same
        trap `views/operators.ts` warns about for operators.

        Skipped when the caller cannot read the target's schema, which is the
        whole point of a `lookup`-only grant: `GET /lists/{it}/columns/` refuses
        them, so an error message that distinguishes "not a column of the target"
        from "accepted" would answer, one uuid at a time, the question the refusal
        exists to avoid. They keep the silent-drop behaviour instead — a worse
        filter for them, rather than a schema oracle for everyone.
        """
        filters = data.get("option_filters")
        if not filters:
            return
        # Deferred for the same reason as the other permission call sites.
        from src.permissions.enums import Action, ResourceType
        from src.permissions.services import PermissionService

        if not await PermissionService.has(
            db, user, ResourceType.list, target_list_id, Action.view
        ):
            return

        referenced = {
            str(cond.get("column_uuid"))
            for cond in (filters.get("conditions") or [])
            if cond.get("column_uuid") is not None
        }
        if not referenced:
            return
        found = await db.execute(
            select(Column.uuid).where(
                Column.list_id == target_list_id,
                Column.uuid.in_([coerce_uuid(u) for u in referenced]),
                in_shape(),
            )
        )
        missing = referenced - {str(u) for u in found.scalars().all()}
        if missing:
            raise UnknownReferenceError(
                "option_filters references a column that is not on the target "
                f"list: {', '.join(sorted(missing))}"
            )

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
