"""Run a column type conversion: build, backfill, verify, cut over.

Three steps, deliberately separate, because the middle one is where a person
decides whether to go on:

1. **start** — create the target column *hidden*, and backfill it through
   `conversion.CellConverter`. Nothing about the list changes: the source column
   keeps serving every view, filter, form and formula untouched, and the target is
   invisible to every reader (`Column.in_shape`). Returns a report.
2. **commit** — swap the two columns' values in every record and retype the source
   column in place. This is the only moment the list changes, and it is atomic per
   chunk rather than instant, which is why nothing may reference the target until
   it is done. A **duplicate** (started with a name) takes the other ending: the new
   column is simply made visible beside the source, which rewrites nothing at all
   because its values are already under its own key.
3. **abandon** — delete the scratch column and its values. Costs nothing, because
   nothing ever saw them.

**Why not convert in place.** Filters and sorts compile to SQL over the raw JSONB
(`items/filtering.py`), so a column whose declared type and stored bytes disagree
does not return stale results — a `number` column holding one text cell makes
`apply_sorts` raise `InvalidTextRepresentationError`, and every sort and range
filter on it fails until the last row lands. A hidden column has no such window:
nothing queries it, and it only ever receives values that already validated.

**The cutover keeps the column's identity, so nothing needs repointing.** Rather
than swapping uuids between two column rows, the *source row itself* is retyped —
same uuid, same name, same position. Views, forms, formulas and
`primary_column_id` all keep pointing exactly where they pointed, because nothing
they point at moved. Only the record values move, and only between two keys.

**The old values survive in the bin.** The cutover swaps the two keys rather than
overwriting one, so the scratch column ends up holding the *original* data with
the *original* type, and is then soft-deleted. Restoring it from the bin within
the retention window is a real undo.
"""
import json
from datetime import datetime, timezone
from typing import Optional, Union
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions import (
    ConflictError,
    UnknownReferenceError,
    ValidationError,
)

from ..items.models import ListItem
from ..models import List
from . import backfill
from .conversion import CellConverter, check_supported, mint_choice_options
from .enums import ColumnType
from .models import Column
from .schemas import ColumnConfig
from .services import ColumnService
from .validation import resolve_choice_options

# Records read, converted and written per transaction. Matches the import's batch
# rather than the reconcile's 5,000: every row here costs Python work and possibly a
# reference lookup, so the transaction stays short at a smaller size.
CHUNK = 500

# Above this many records a conversion is refused in the request and has to be run
# from the maintenance workflow, which holds no web worker while it runs. Free
# Postgres is 1GB, so this is well beyond any list that fits — it exists so the
# ceiling is a deliberate number rather than whatever the request timeout happens
# to be.
ROW_CEILING = 50_000

# Failures named individually in the report. Past this the count still climbs, but
# a person reading 5,000 identical messages has already learned what they need.
MAX_REPORTED_ERRORS = 100

# Marks a scratch column as belonging to a conversion, and of what. Not a
# `ColumnConfig` field: it is bookkeeping this service owns, and a client must not
# be able to set it. Config merges preserve unknown keys, so it survives a PATCH.
CONVERSION_OF = "_conversion_of"
# Marks the scratch column as a **duplicate**: at commit it goes live beside the
# source rather than replacing it. Same bookkeeping rules as `CONVERSION_OF`, and
# both are stripped from the config when the column becomes real.
KEEP_SOURCE = "_keep_source"

_SUFFIX = "__converting"


class ConversionService:
    # -- helpers ---------------------------------------------------------

    @staticmethod
    def _scratch_name(source: Column) -> str:
        # Bounded by the column name limit (120) with room for the suffix.
        return f"{source.name[:100]}{_SUFFIX}"

    @classmethod
    async def _find_scratch(
        cls, db: AsyncSession, lst: List, source: Column
    ) -> Optional[Column]:
        """The in-flight conversion for this column, if there is one."""
        result = await db.execute(
            select(Column).where(
                Column.list_id == lst.id,
                Column.deleted_at.is_(None),
                Column.hidden.is_(True),
                Column.config[CONVERSION_OF].astext == str(source.uuid),
            )
        )
        return result.scalars().first()

    @staticmethod
    async def _row_count(db: AsyncSession, list_id: int) -> int:
        # Soft-deleted records included, for the reason `reconcile_shapes` includes
        # them: a restore must not bring back a value in the old column's type.
        return await db.scalar(
            select(sa.func.count())
            .select_from(ListItem)
            .where(ListItem.list_id == list_id)
        )

    # -- 1. start --------------------------------------------------------

    @classmethod
    async def start(
        cls,
        db: AsyncSession,
        lst: List,
        source: Column,
        target_type: ColumnType,
        config: ColumnConfig,
        user,
        name: Optional[str] = None,
        display_name: Optional[str] = None,
    ) -> dict:
        """Create the hidden target column and backfill it. Returns the report.

        Naming it makes this a **duplicate** rather than a conversion: the column
        goes live alongside the source at commit instead of replacing it. The name
        is taken here rather than at commit so the scratch column holds it against
        the unique index for the whole run — a backfill that succeeds and then
        cannot be named would be a maddening way to lose the work.
        """
        duplicate = name is not None
        if await cls._find_scratch(db, lst, source) is not None:
            raise ConflictError(
                f"'{source.name}' already has a conversion in progress. Commit or "
                "abandon it before starting another."
            )

        # Refuse the pair before creating anything, so a rejected conversion leaves
        # no scratch column behind to clean up. A same-type duplicate is a straight
        # copy, which `is_supported` already allows.
        probe = Column(type=target_type, name=source.name, config={})
        check_supported(source, probe)

        rows = await cls._row_count(db, lst.id)
        if rows > ROW_CEILING:
            raise ValidationError(
                f"This list has {rows:,} records, more than the {ROW_CEILING:,} a "
                "conversion will run through in a request. Run it from the "
                "maintenance workflow instead."
            )

        supplied = config.model_dump(mode="json", exclude_unset=True)
        if target_type == ColumnType.choice and source.type == ColumnType.text:
            # Either spelling counts as "the caller said what the choices are" —
            # `choices` is the shorthand the column dialog sends, and treating only
            # `options` as supplied would mint over the top of what they typed.
            supplied["options"] = await mint_choice_options(
                db,
                lst.id,
                source,
                supplied.get("options") or resolve_choice_options(supplied, None),
            )
            supplied.pop("choices", None)

        stored = await ColumnService._validate_config(
            db, lst, target_type, ColumnConfig(**supplied), user
        )
        bookkeeping = {CONVERSION_OF: str(source.uuid)}
        if duplicate:
            bookkeeping[KEEP_SOURCE] = True
        target = Column(
            list_id=lst.id,
            name=name if duplicate else cls._scratch_name(source),
            display_name=(
                display_name or name
                if duplicate
                else f"{source.display_name} (converting)"
            ),
            # Inherited, not computed. Columns are ordered by `position, id` and
            # nothing in the UI ever sets `position`, so in practice every column
            # is 0 and a duplicate sorts last by id rather than next to its source.
            # Placing it adjacent would mean renumbering every column after it,
            # which is a reordering feature and not this one.
            position=source.position,
            type=target_type,
            hidden=True,
            config={**stored, **bookkeeping},
        )
        db.add(target)
        try:
            await db.flush()
        except IntegrityError as exc:
            await db.rollback()
            raise ConflictError(
                f"A column named '{name}' already exists on this list"
                if duplicate
                else "A column is already using the name this conversion needs"
            ) from exc
        await db.commit()
        await db.refresh(target)

        report = await cls._backfill(db, lst, source, target, user)
        report["target_uuid"] = str(target.uuid)
        report["new_type"] = target.type.value
        report["duplicate"] = duplicate
        return report

    @classmethod
    async def _backfill(
        cls,
        db: AsyncSession,
        lst: List,
        source: Column,
        target: Column,
        user,
    ) -> dict:
        """Convert every record's source cell into the target column.

        Chunked and committed per batch, so row locks are held for milliseconds and
        a killed run leaves a partly-filled *hidden* column rather than a partly-
        converted visible one. A cell that will not convert is recorded and skipped:
        the whole point of doing this into a scratch column is that a person gets to
        read the failures before anything about the list changes.
        """
        converter = CellConverter(db, lst, source, target, user)
        await converter.prepare()

        skey, tkey = str(source.uuid), str(target.uuid)
        converted = 0
        failed = 0
        errors: list[dict] = []
        after = 0

        while True:
            rows = (
                await db.execute(
                    select(ListItem.id, ListItem.uuid, ListItem.data)
                    .where(ListItem.list_id == lst.id, ListItem.id > after)
                    .order_by(ListItem.id)
                    .limit(CHUNK)
                )
            ).all()
            if not rows:
                break
            after = rows[-1].id

            # One round trip for every label this chunk needs, rather than one per
            # cell — the same reason the export resolves in batches.
            await converter.load([row.data or {} for row in rows])

            writes = []
            for row in rows:
                value = (row.data or {}).get(skey)
                if value is None:
                    # Nothing to carry over. The target key stays absent, which every
                    # reader already treats as empty.
                    continue
                try:
                    new_value = await converter.convert(value)
                except (ValidationError, UnknownReferenceError) as exc:
                    # `UnknownReferenceError` too, and it is not a nicety: a text
                    # cell naming a user or a row that does not exist is the single
                    # most likely failure of a text -> reference conversion, and it
                    # has to cost that cell rather than the whole run.
                    failed += 1
                    if len(errors) < MAX_REPORTED_ERRORS:
                        errors.append(
                            {"record_uuid": str(row.uuid), "message": str(exc)}
                        )
                    continue
                writes.append(
                    {"row_id": row.id, "key": tkey, "value": json.dumps(new_value)}
                )
                converted += 1

            if writes:
                await db.execute(
                    sa.text(
                        "UPDATE list_items "
                        "   SET data = jsonb_set(data, ARRAY[:key], CAST(:value AS jsonb)) "
                        " WHERE id = :row_id"
                    ),
                    writes,
                )
            await db.commit()

        return {
            "converted": converted,
            "failed": failed,
            "errors": errors,
            "errors_truncated": failed > len(errors),
        }

    # -- 2. commit -------------------------------------------------------

    @classmethod
    async def commit(
        cls,
        db: AsyncSession,
        lst: List,
        source: Column,
        target_uuid: Union[str, UUID],
        defer: Optional[backfill.Deferrer] = None,
    ) -> Column:
        """Swap the converted values in and retype the column. The point of no return."""
        target = await ColumnService.get_including_hidden(db, lst.id, target_uuid)
        if target is None or not target.hidden:
            raise ValidationError("That conversion is not in progress")
        if (target.config or {}).get(CONVERSION_OF) != str(source.uuid):
            raise ValidationError(
                f"That conversion does not belong to '{source.name}'"
            )

        await cls._check_gates(db, lst, target)

        if (target.config or {}).get(KEEP_SOURCE):
            # A duplicate: the values are already under this column's own key, so
            # there is nothing to move and no record to rewrite. Making it real is
            # only a matter of letting readers see it.
            target.hidden = False
            target.config = {
                key: value
                for key, value in (target.config or {}).items()
                if key not in (CONVERSION_OF, KEEP_SOURCE)
            }
            await db.commit()
            await db.refresh(target)
            await cls._resettle_formulas(db, lst, target, defer)
            return target

        skey, tkey = str(source.uuid), str(target.uuid)
        await cls._swap_values(db, lst.id, skey, tkey)

        # Retype the *source row*, so the column keeps its uuid, name and position
        # and nothing that references it has to be repointed. `type` is immutable
        # everywhere else in the service by design; this is the one operation whose
        # whole purpose is to change it.
        old_type, old_config = source.type, dict(source.config or {})
        new_config = dict(target.config or {})
        new_config.pop(CONVERSION_OF, None)
        source.type = target.type
        source.config = new_config

        # The scratch column now holds the original values, so it becomes the undo:
        # the old type, a name that says what it is, and straight into the bin.
        target.type = old_type
        target.config = old_config
        target.name = f"{source.name[:100]}__before_conversion"
        target.display_name = f"{source.display_name} (before conversion)"
        target.hidden = False
        target.deleted_at = datetime.now(timezone.utc)

        await db.commit()
        await db.refresh(source)
        await cls._resettle_formulas(db, lst, source, defer)
        return source

    @staticmethod
    async def _resettle_formulas(
        db: AsyncSession,
        lst: List,
        column: Column,
        defer: Optional[backfill.Deferrer] = None,
    ) -> None:
        """Recompute the list's calculated cells if a formula was reading this column.

        A cutover replaces every value in the column at once, which is precisely the
        case a per-record write cannot cover: nothing goes through `_validate_data`, so
        a `total` over a just-converted `price` would keep answering from the values the
        column held before. A duplicate needs it too — the new column shares its name
        with nothing, but going live can complete a formula that was left dangling by
        an earlier rename.
        """
        columns = await backfill.list_columns(db, lst.id)
        if backfill.references_a_formula(columns, column.name):
            await backfill.resettle(db, lst.id, defer=defer)

    @classmethod
    async def _check_gates(cls, db: AsyncSession, lst: List, target: Column) -> None:
        """`required` and `unique`, which the backfill deliberately did not enforce.

        Per cell they would be the wrong question: a blank source cell is ordinary,
        and a duplicate only matters once the column is real. Here they are exactly
        right, because this is the moment the column becomes part of the list.
        """
        config = target.config or {}
        key = str(target.uuid)
        params = {"lid": lst.id, "key": key}

        if config.get("required"):
            missing = await db.scalar(
                sa.text(
                    "SELECT count(*) FROM list_items "
                    " WHERE list_id = :lid AND deleted_at IS NULL "
                    "   AND (NOT (data ? :key) "
                    "        OR jsonb_typeof(data -> :key) = 'null' "
                    "        OR data ->> :key = '')"
                ),
                params,
            )
            if missing:
                raise ConflictError(
                    f"{missing:,} record(s) would have no value for a required "
                    "column. Fix them, or drop `required` from the conversion."
                )

        if config.get("unique"):
            duplicated = await db.scalar(
                sa.text(
                    "SELECT count(*) FROM ("
                    "  SELECT data ->> :key AS v FROM list_items "
                    "   WHERE list_id = :lid AND deleted_at IS NULL "
                    "     AND data ->> :key IS NOT NULL AND data ->> :key <> '' "
                    "   GROUP BY 1 HAVING count(*) > 1) d"
                ),
                params,
            )
            if duplicated:
                raise ConflictError(
                    f"{duplicated:,} value(s) would appear more than once in a "
                    "unique column."
                )

    @staticmethod
    async def _swap_values(
        db: AsyncSession, list_id: int, skey: str, tkey: str
    ) -> None:
        """Exchange two keys' values in every record, in committed chunks.

        Iterated by primary key rather than by a predicate that stops matching: a
        swap is its own inverse, so a statement that re-ran over a row it had
        already touched would put the data back. Every row is visited exactly once.
        """
        swap = sa.text(
            "data || jsonb_build_object(:skey, data -> :tkey, :tkey, data -> :skey)"
        ).bindparams(skey=skey, tkey=tkey)

        after = 0
        while True:
            ids = (
                await db.execute(
                    select(ListItem.id)
                    .where(ListItem.list_id == list_id, ListItem.id > after)
                    .order_by(ListItem.id)
                    .limit(CHUNK)
                )
            ).scalars().all()
            if not ids:
                return
            after = ids[-1]
            await db.execute(
                sa.update(ListItem).where(ListItem.id.in_(ids)).values(data=swap)
            )
            await db.commit()

    # -- 3. abandon ------------------------------------------------------

    @classmethod
    async def abandon(
        cls, db: AsyncSession, lst: List, source: Column, target_uuid: Union[str, UUID]
    ) -> bool:
        """Throw the scratch column away, values and all.

        Hard delete rather than the bin: nothing ever saw this column, so there is
        nothing to restore and no reason to make someone empty a bin of work they
        already decided against.
        """
        target = await ColumnService.get_including_hidden(db, lst.id, target_uuid)
        if target is None or not target.hidden:
            return False
        if (target.config or {}).get(CONVERSION_OF) != str(source.uuid):
            return False

        key = str(target.uuid)
        after = 0
        while True:
            ids = (
                await db.execute(
                    select(ListItem.id)
                    .where(
                        ListItem.list_id == lst.id,
                        ListItem.id > after,
                        ListItem.data.has_key(key),  # noqa: W601 — JSONB `?`
                    )
                    .order_by(ListItem.id)
                    .limit(CHUNK)
                )
            ).scalars().all()
            if not ids:
                break
            after = ids[-1]
            await db.execute(
                sa.update(ListItem)
                .where(ListItem.id.in_(ids))
                .values(data=sa.text("data - :key").bindparams(key=key))
            )
            await db.commit()

        await db.delete(target)
        await db.commit()
        return True
