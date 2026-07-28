from datetime import datetime, timezone
from typing import Optional, Sequence, Union
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions import ConflictError, ValidationError

from src.core.utils import coerce_uuid

from ..columns.enums import ColumnType
from ..columns.models import Column
from ..items.models import ListItem
from ..items.services import ListItemService, computed_columns, shape_values
from ..models import List
from ..refs import get_by_ref
from . import aggregate
from .models import Metric
from .schemas import MetricConfig, MetricCreate, MetricUpdate, MetricValue, MetricValues

# What a metric may aggregate. Numbers and text are the row language's own
# referenceable set; `calculated` is added because a metric is a **leaf** —
# nothing derives from a metric, so there is no cycle to detect, and totalling a
# per-record formula (`SUM(line_total)`) is one of the things people most want.
# The remaining types store ids or ISO strings whose text form would surprise.
REFERENCEABLE_TYPES = frozenset(
    {ColumnType.number, ColumnType.text, ColumnType.calculated}
)

# How many records one evaluation will read before it stops and says so. A
# metric is recomputed on every request — and the frontend re-asks on the live
# refresh interval — so an unbounded scan is a way to spend the whole server on a
# list nobody thought was large. Past this the answers describe the first
# `METRIC_ROW_LIMIT` records and `truncated` says so, which is a number a reader
# can judge; refusing outright would leave them nothing at all.
METRIC_ROW_LIMIT = 50_000
# Rows per round trip while streaming. Large enough that a scan is not chatty,
# small enough that a wide list does not land in memory at once.
_SCAN_BATCH = 1_000


class MetricService:
    @staticmethod
    def _select(list_id: int):
        return select(Metric).where(
            Metric.deleted_at.is_(None), Metric.list_id == list_id
        )

    @classmethod
    async def get_by_uuid(
        cls, db: AsyncSession, list_id: int, uuid: Union[str, UUID]
    ) -> Optional[Metric]:
        parsed = coerce_uuid(uuid)
        if parsed is None:
            return None
        result = await db.execute(cls._select(list_id).where(Metric.uuid == parsed))
        return result.scalars().first()

    @classmethod
    async def get_by_ref(
        cls, db: AsyncSession, list_id: int, ref: Union[str, UUID]
    ) -> Optional[Metric]:
        """A metric addressed by uuid or by name — see src.lists.refs."""
        return await get_by_ref(db, Metric, list_id, ref)

    @classmethod
    async def list(cls, db: AsyncSession, list_id: int) -> Sequence[Metric]:
        """Every metric on the list, in display order.

        No per-row visibility pass, unlike views and forms: a metric has no
        permissions of its own, so reaching this at all is the whole check.
        """
        result = await db.execute(
            cls._select(list_id).order_by(Metric.position, Metric.id)
        )
        return result.scalars().all()

    @classmethod
    async def create(
        cls, db: AsyncSession, lst: List, payload: MetricCreate
    ) -> Metric:
        await cls._validate_config(db, lst.id, payload.config)
        metric = Metric(
            list_id=lst.id,
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
                "A metric with this name already exists on the list"
            ) from exc
        return await cls.get_by_uuid(db, lst.id, new_uuid)

    @classmethod
    async def update(
        cls,
        db: AsyncSession,
        lst: List,
        uuid: Union[str, UUID],
        payload: MetricUpdate,
    ) -> Optional[Metric]:
        metric = await cls.get_by_uuid(db, lst.id, uuid)
        if metric is None:
            return None

        data = payload.model_dump(exclude_unset=True)
        if "config" in data and payload.config is not None:
            await cls._validate_config(db, lst.id, payload.config)
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
                "A metric with this name already exists on the list"
            ) from exc
        return await cls.get_by_uuid(db, lst.id, uuid)

    @classmethod
    async def delete(
        cls, db: AsyncSession, list_id: int, uuid: Union[str, UUID]
    ) -> bool:
        metric = await cls.get_by_uuid(db, list_id, uuid)
        if metric is None:
            return False
        metric.deleted_at = datetime.now(timezone.utc)
        await db.commit()
        return True

    @staticmethod
    async def _validate_config(
        db: AsyncSession, list_id: int, config: MetricConfig
    ) -> None:
        """Ensure the formula parses and reads only columns this list can offer."""
        formula = aggregate.parse(config.formula)
        columns = (
            await db.execute(
                select(Column).where(
                    Column.list_id == list_id, Column.deleted_at.is_(None)
                )
            )
        ).scalars().all()
        by_name = {c.name: c for c in columns}
        for name in sorted(aggregate.column_refs(formula)):
            target = by_name.get(name)
            if target is None:
                raise ValidationError(f"Formula references unknown column '{name}'")
            if target.type not in REFERENCEABLE_TYPES:
                raise ValidationError(
                    f"A metric can only aggregate number, text or calculated "
                    f"columns; '{name}' is {target.type.value}"
                )

    @classmethod
    async def evaluate(
        cls,
        db: AsyncSession,
        lst: List,
        user,
        view_uuid: Optional[Union[str, UUID]] = None,
        q: Optional[str] = None,
    ) -> MetricValues:
        """Answer every metric on the list in one pass over its records.

        The records are whatever the caller may see through this view and search
        — `ListItemService.visible_stmt` is the same statement the grid reads —
        so two people with different grants correctly get different numbers from
        the same card, and a filtered view reports on what it shows.
        """
        metrics = await cls.list(db, lst.id)
        if not metrics:
            return MetricValues(row_count=0, values=[])

        columns = await ListItemService._columns_by_key(db, lst.id)
        live_names = {column.name for column in columns.values()}

        # Parse first and pair each metric with its formula or its reason. A
        # formula that no longer matches the list — a column it read was renamed
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
        evaluator: Optional[aggregate.MetricEvaluator] = None
        row_count = 0
        truncated = False

        if usable:
            evaluator = aggregate.MetricEvaluator(usable)
            computed, key_of_name = computed_columns(columns)
            # Only the keys the formulas actually read, resolved once rather than
            # per record.
            wanted = {
                name: key_of_name[name]
                for name in evaluator.column_names
                if name in key_of_name
            }

            stmt, _sorts, _group_by = await ListItemService.visible_stmt(
                db, lst, user, columns, view_uuid, q
            )
            # `data` alone, ordered by id: an aggregate has no use for the view's
            # sort, and hydrating whole records would cost 50k ORM instances for
            # values that are thrown away a row later.
            stmt = stmt.with_only_columns(ListItem.data).order_by(ListItem.id)
            result = await db.stream(stmt.execution_options(yield_per=_SCAN_BATCH))
            async for (data,) in result:
                shaped = shape_values(columns, computed, key_of_name, data or {})
                evaluator.add_row({name: shaped.get(key) for name, key in wanted.items()})
                row_count += 1
                if row_count >= METRIC_ROW_LIMIT:
                    truncated = True
                    break
            await result.close()

        return MetricValues(
            row_count=row_count,
            truncated=truncated,
            values=[
                MetricValue(
                    uuid=metric.uuid,
                    name=metric.name,
                    value=None if formula is None else evaluator.result(formula),
                    error=error,
                )
                for metric, formula, error in parsed
            ],
        )
