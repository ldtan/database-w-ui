"""Streaming export and bulk import of list records.

Export streams rows straight from an async DB cursor so server memory stays
flat regardless of list size. Import parses the upload row by row, validating
and inserting through the item service inside a per-row savepoint (one bad row
never sinks the batch) with periodic commits.
"""
import csv
import io
import json
from typing import AsyncIterator, Iterable, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions import ConflictError, UnknownReferenceError, ValidationError
from src.infrastructure import database as db_module

from ..columns.enums import ColumnType
from ..columns.models import Column
from ..items.filtering import apply_filters, apply_sorts
from ..items.models import ListItem
from ..items.services import ListItemService
from ..models import List

# Commit every N successfully-prepared rows to bound the transaction size.
_IMPORT_BATCH = 500

_SKIP = object()  # sentinel: leave this cell out of the record entirely


# -- export --------------------------------------------------------------

async def _ordered_columns(
    db: AsyncSession, list_id: int, names: list[str]
) -> list[Column]:
    result = await db.execute(
        select(Column)
        .where(Column.list_id == list_id, Column.deleted_at.is_(None))
        .order_by(Column.position, Column.id)
    )
    cols = list(result.scalars().all())
    if names:
        by_name = {c.name: c for c in cols}
        return [by_name[n] for n in names if n in by_name]
    return cols


def _csv_cell(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return ";".join(str(v) for v in value)
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


async def export_rows(
    list_id: int,
    fmt: str,
    column_names: list[str],
    filters: Optional[dict],
    sorts: Optional[list],
    limit: Optional[int],
) -> AsyncIterator[str]:
    """Yield the export file in chunks. Opens its own session so it is
    independent of the request's dependency lifecycle while streaming.
    ``db_module.SessionLocal`` is read at call time so tests that repoint it at
    the test database are honored."""
    async with db_module.SessionLocal() as db:
        columns = await _ordered_columns(db, list_id, column_names)
        all_columns = await ListItemService._columns_by_key(db, list_id)

        stmt = select(ListItem).where(
            ListItem.deleted_at.is_(None), ListItem.list_id == list_id
        )
        stmt = apply_filters(stmt, filters, all_columns)
        stmt = apply_sorts(stmt, sorts, all_columns)
        if limit is not None:
            stmt = stmt.limit(limit)

        result = await db.stream_scalars(stmt)

        if fmt == "json":
            first = True
            yield "["
            async for item in result:
                obj = {c.name: (item.data or {}).get(str(c.uuid)) for c in columns}
                yield ("" if first else ",") + json.dumps(obj, default=str)
                first = False
            yield "]"
        else:
            buffer = io.StringIO()
            writer = csv.writer(buffer)

            def flush() -> str:
                text = buffer.getvalue()
                buffer.seek(0)
                buffer.truncate(0)
                return text

            writer.writerow([c.name for c in columns])
            yield flush()
            async for item in result:
                data = item.data or {}
                writer.writerow([_csv_cell(data.get(str(c.uuid))) for c in columns])
                yield flush()


# -- import --------------------------------------------------------------

def _parse_cell(column: Column, raw: object):
    """Coerce a raw file value (string from CSV, native from JSON) into the
    value the API expects. Returns _SKIP to omit the cell."""
    if raw is None:
        return _SKIP
    if isinstance(raw, str) and raw.strip() == "":
        return _SKIP

    multiple = bool((column.config or {}).get("multiple"))
    if column.type == ColumnType.number:
        if isinstance(raw, bool):
            raise ValidationError(f"column '{column.name}': '{raw}' is not a number")
        if isinstance(raw, (int, float)):
            return raw
        s = str(raw).strip()
        try:
            return int(s)
        except ValueError:
            try:
                return float(s)
            except ValueError:
                raise ValidationError(
                    f"column '{column.name}': '{raw}' is not a number"
                )
    if column.type == ColumnType.checkbox:
        if isinstance(raw, bool):
            return raw
        s = str(raw).strip().lower()
        if s in ("true", "1", "yes", "y"):
            return True
        if s in ("false", "0", "no", "n"):
            return False
        raise ValidationError(f"column '{column.name}': '{raw}' is not true/false")
    if column.type in (ColumnType.choice, ColumnType.lookup) and multiple:
        if isinstance(raw, list):
            return raw
        return [p.strip() for p in str(raw).split(";") if p.strip()]
    return raw if isinstance(raw, str) else str(raw)


async def import_records(
    db: AsyncSession,
    lst: List,
    rows: Iterable[dict],
    row_offset: int,
) -> dict:
    """Validate and insert rows through the item service. Each row runs in a
    savepoint so a failure is isolated; commits happen in batches. Returns a
    summary dict {created, errors}."""
    columns = await ListItemService._columns_by_key(db, lst.id)
    by_name = {c.name: c for c in columns.values()}

    created = 0
    pending = 0
    errors: list[dict] = []

    for index, raw_row in enumerate(rows, start=1):
        row_number = index + row_offset
        try:
            incoming: dict = {}
            for key, value in raw_row.items():
                column = by_name.get(key)
                if column is None:
                    continue  # ignore columns not on this list
                parsed = _parse_cell(column, value)
                if parsed is not _SKIP:
                    incoming[str(column.uuid)] = parsed

            async with db.begin_nested():
                data = await ListItemService._validate_data(
                    db, lst, columns, incoming, existing=None
                )
                await ListItemService._enforce_unique(
                    db, lst.id, columns, data, exclude_id=None
                )
                db.add(ListItem(list_id=lst.id, data=data))

            created += 1
            pending += 1
            if pending >= _IMPORT_BATCH:
                await db.commit()
                pending = 0
        except (ValidationError, ConflictError, UnknownReferenceError) as exc:
            errors.append({"row": row_number, "message": str(exc)})
        except Exception as exc:  # pragma: no cover - defensive
            errors.append({"row": row_number, "message": f"unexpected error: {exc}"})

    await db.commit()
    return {"created": created, "errors": errors}
