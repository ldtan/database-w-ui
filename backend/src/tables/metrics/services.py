from datetime import datetime, timezone
from typing import Optional, Sequence, Union
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions import ConflictError, ValidationError

from src.core.utils import coerce_uuid

from ..columns.enums import NUMERIC_TYPES, ColumnType
from ..columns.models import Column, in_shape
from ..records.models import TableRecord
from ..records.services import TableRecordService, computed_columns, shape_values
from ..models import Table
from . import aggregate, pushdown
from .models import Metric
from .schemas import MetricConfig, MetricCreate, MetricUpdate, MetricValue, MetricValues

# What a metric may aggregate. Deliberately **narrower** than what a row formula may
# name (`columns.enums.FORMULA_REFERENCEABLE_TYPES`, which also covers choice and the
# two temporal types): numbers and text are what an aggregate has an answer for, and
# `calculated` is added because a metric is a **leaf** — nothing derives from a metric,
# so there is no cycle to detect, and totalling a per-record formula
# (`SUM(line_total)`) is one of the things people most want. A choice or a date is
# reachable from here anyway, through a calculated column that reads one — which keeps
# the pushdown question (`pushdown._PUSHABLE_TYPES`) to one place rather than two.
#
# The whole of NUMERIC_TYPES is here rather than `number` alone: `SUM(hours)` over a
# `duration` column and `AVG(score)` over a `rating` one are the two things people
# open the metrics strip to ask, and a cell in either holds a plain JSON number that
# aggregates exactly as a `number` cell does. What their totals *mean* is a display
# question — 5400 seconds shows as 1:30 through the metric's `suffix`/`precision` and
# the same formatter the column uses — and this module has never formatted anything.
REFERENCEABLE_TYPES = frozenset(
    NUMERIC_TYPES | {ColumnType.text, ColumnType.calculated}
)

# How many records one *scan* will read before it stops and says so. A metric is
# recomputed on every request — and the frontend re-asks on its refresh interval —
# so an unbounded scan is a way to spend the whole server on a table nobody thought
# was large. Past this the answers describe the first `METRIC_ROW_LIMIT` records
# and `truncated` says so, which is a number a reader can judge; refusing outright
# would leave them nothing at all.
#
# It bounds the Python path only. A request `pushdown` can compile is answered by
# Postgres over every matching record, with nothing accumulating in Python to cap —
# so the cap is what a formula falls back *to*, not a property of metrics.
METRIC_ROW_LIMIT = 50_000
# Rows per round trip while streaming. Large enough that a scan is not chatty,
# small enough that a wide table does not land in memory at once.
_SCAN_BATCH = 1_000


class MetricService:
    @staticmethod
    def _select(table_id: int):
        return select(Metric).where(
            Metric.deleted_at.is_(None), Metric.table_id == table_id
        )

    @classmethod
    async def get_by_uuid(
        cls, db: AsyncSession, table_id: int, uuid: Union[str, UUID]
    ) -> Optional[Metric]:
        parsed = coerce_uuid(uuid)
        if parsed is None:
            return None
        result = await db.execute(cls._select(table_id).where(Metric.uuid == parsed))
        return result.scalars().first()

    @classmethod
    async def list(cls, db: AsyncSession, table_id: int) -> Sequence[Metric]:
        """Every metric on the table, in display order.

        No per-row visibility pass, unlike views and forms: a metric has no
        permissions of its own, so reaching this at all is the whole check.
        """
        result = await db.execute(
            cls._select(table_id).order_by(Metric.position, Metric.id)
        )
        return result.scalars().all()

    @classmethod
    async def create(
        cls, db: AsyncSession, table: Table, payload: MetricCreate
    ) -> Metric:
        await cls._validate_config(db, table.id, payload.config)
        metric = Metric(
            table_id=table.id,
            name=payload.name,
            display_name=payload.display_name,
            position=payload.position,
            description=payload.description,
            config=payload.config.model_dump(mode="json"),
        )
        db.add(metric)
        try:
            await db.flush()
            new_uuid = metric.uuid
            await db.commit()
        except IntegrityError as exc:
            await db.rollback()
            raise ConflictError(
                "A metric with this name already exists on the table"
            ) from exc
        return await cls.get_by_uuid(db, table.id, new_uuid)

    @classmethod
    async def update(
        cls,
        db: AsyncSession,
        table: Table,
        uuid: Union[str, UUID],
        payload: MetricUpdate,
    ) -> Optional[Metric]:
        metric = await cls.get_by_uuid(db, table.id, uuid)
        if metric is None:
            return None

        data = payload.model_dump(exclude_unset=True)
        if "config" in data and payload.config is not None:
            await cls._validate_config(db, table.id, payload.config)
            metric.config = payload.config.model_dump(mode="json")
        data.pop("config", None)
        for field in ("name", "display_name", "position"):
            if data.get(field) is None:
                data.pop(field, None)

        for field, value in data.items():
            setattr(metric, field, value)

        try:
            await db.commit()
        except IntegrityError as exc:
            await db.rollback()
            raise ConflictError(
                "A metric with this name already exists on the table"
            ) from exc
        return await cls.get_by_uuid(db, table.id, uuid)

    @classmethod
    async def delete(
        cls, db: AsyncSession, table_id: int, uuid: Union[str, UUID]
    ) -> bool:
        metric = await cls.get_by_uuid(db, table_id, uuid)
        if metric is None:
            return False
        metric.deleted_at = datetime.now(timezone.utc)
        await db.commit()
        return True

    @staticmethod
    async def _validate_config(
        db: AsyncSession, table_id: int, config: MetricConfig
    ) -> None:
        """Ensure the formula parses and reads only columns this table can offer."""
        formula = aggregate.parse(config.formula)
        columns = (
            await db.execute(
                select(Column).where(
                    Column.table_id == table_id, in_shape()
                )
            )
        ).scalars().all()
        by_name = {c.name: c for c in columns}
        for name in sorted(aggregate.column_refs(formula)):
            target = by_name.get(name)
            if target is None:
                raise ValidationError(f"Formula references unknown column '{name}'")
            if target.type not in REFERENCEABLE_TYPES:
                allowed = ", ".join(sorted(t.value for t in REFERENCEABLE_TYPES))
                raise ValidationError(
                    f"A metric can only aggregate {allowed} columns; "
                    f"'{name}' is {target.type.value}"
                )

    @classmethod
    async def evaluate(
        cls,
        db: AsyncSession,
        table: Table,
        user,
        view_uuid: Optional[Union[str, UUID]] = None,
        q: Optional[str] = None,
        filters: Optional[dict] = None,
    ) -> MetricValues:
        """Answer every metric on the table in one pass over the records in scope.

        The records are whatever the caller may see through this view, search and
        filter — `TableRecordService.visible_stmt` is the same statement the grid reads
        — so two people with different grants correctly get different numbers from
        the same card, and a filtered view reports on what it shows.

        ``filters`` is here for the same reason `q` is: a total sitting above a grid
        has to be a total *of that grid*. Narrowing the records without narrowing the
        cards would leave a "Total owed" reading the whole table while the rows under
        it show one customer, which is worse than showing nothing.

        The pass is either one SQL statement Postgres aggregates (`pushdown`) or one
        stream folded in Python (`_scan`), decided by the formulas alone and never by
        the data. Which one ran is invisible in the answer — that is the property the
        equivalence tests exist to hold — except that a compiled request is not
        subject to `METRIC_ROW_LIMIT` and so never reports `truncated`.
        """
        metrics = await cls.list(db, table.id)
        if not metrics:
            return MetricValues(row_count=0, values=[])

        columns = await TableRecordService._columns_by_key(db, table.id)
        live_names = {column.name for column in columns.values()}

        # Parse first and pair each metric with its formula or its reason. A
        # formula that no longer matches the table — a column it read was renamed
        # or deleted — reports that instead of a number, because a broken SUM
        # silently reading 0 is the one answer nobody can catch.
        parsed: list[tuple[Metric, Optional[aggregate.MetricFormula], Optional[str]]] = []
        for metric in metrics:
            try:
                formula = aggregate.parse((metric.config or {}).get("formula"))
            except ValidationError as exc:
                parsed.append((metric, None, str(exc)))
                continue
            missing = sorted(aggregate.column_refs(formula) - live_names)
            if missing:
                named = ", ".join(f"'{name}'" for name in missing)
                label = "column" if len(missing) == 1 else "columns"
                parsed.append((metric, None, f"Unknown {label} {named}"))
                continue
            parsed.append((metric, formula, None))

        usable = [formula for _, formula, _ in parsed if formula is not None]
        results: dict[int, object] = {}
        row_count = 0
        truncated = False

        if usable:
            stmt, _sorts, _group_by = await TableRecordService.visible_stmt(
                db, table, user, columns, view_uuid, q, filters
            )
            # Postgres answers these itself where the formulas allow it, and then no
            # record becomes a Python object at all — which is the whole cost of this
            # endpoint on a large table. All-or-nothing: `compile_all` returns None if
            # any one aggregate will not go, so the cards can never describe two
            # different record sets. See `pushdown` for what compiles and why.
            by_name = {column.name: column for column in columns.values()}
            compiled = pushdown.compile_all(usable, by_name)
            if compiled is not None:
                results, row_count = await pushdown.run(db, stmt, compiled)
            else:
                results, row_count, truncated = await cls._scan(db, columns, stmt, usable)

        return MetricValues(
            row_count=row_count,
            truncated=truncated,
            values=[
                MetricValue(
                    uuid=metric.uuid,
                    name=metric.name,
                    value=None if formula is None else aggregate.fold(formula, results),
                    error=error,
                )
                for metric, formula, error in parsed
            ],
        )

    @staticmethod
    async def _scan(
        db: AsyncSession,
        columns: dict[str, Column],
        stmt,
        usable: Sequence[aggregate.MetricFormula],
    ) -> tuple[dict[int, object], int, bool]:
        """The general path: fold every visible record in Python, one pass, bounded.

        What `pushdown` cannot compile ends up here, which is every formula whose
        argument is more than arithmetic over columns — and, deliberately, every
        formula reading a `calculated` column, whose stored value may lag and so has
        to be recomputed per record rather than totalled where it sits.
        """
        evaluator = aggregate.MetricEvaluator(usable)
        computed, key_of_name = computed_columns(columns)
        # Only the keys the formulas actually read, resolved once rather than
        # per record.
        wanted = {
            name: key_of_name[name]
            for name in evaluator.column_names
            if name in key_of_name
        }

        row_count = 0
        truncated = False
        # `data` alone, ordered by id: an aggregate has no use for the view's
        # sort, and hydrating whole records would cost 50k ORM instances for
        # values that are thrown away a row later.
        stmt = stmt.with_only_columns(TableRecord.data).order_by(TableRecord.id)
        result = await db.stream(stmt.execution_options(yield_per=_SCAN_BATCH))
        async for (data,) in result:
            shaped = shape_values(columns, computed, key_of_name, data or {})
            evaluator.add_row({name: shaped.get(key) for name, key in wanted.items()})
            row_count += 1
            if row_count >= METRIC_ROW_LIMIT:
                truncated = True
                break
        await result.close()
        return evaluator.results(), row_count, truncated
