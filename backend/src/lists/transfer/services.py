"""Streaming export and bulk import of list records.

Export streams rows straight from an async DB cursor so server memory stays
flat regardless of list size. Import parses the upload row by row, validating
and inserting through the item service inside a per-row savepoint (one bad row
never sinks the batch) with periodic commits.
"""
import csv
import io
import json
from typing import AsyncIterator, Iterable, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.auth.users.models import User, UserGroup
from src.core.exceptions import ConflictError, UnknownReferenceError, ValidationError
from src.core.utils import coerce_uuid
from src.infrastructure import database as db_module

from ..columns.enums import PRINCIPAL_TYPES, ColumnType
from ..columns.models import Column
from ..items.filtering import apply_filters, apply_sorts
from ..items.models import ListItem
from ..items.services import ListItemService, display_column_key
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


async def _principal_names(
    db: AsyncSession, columns: list[Column]
) -> dict[str, str]:
    """uuid -> username / group name, for the principal columns being exported.

    Names, not the connected person's real name: person records are private, and
    an export is readable by anyone who can read the list.
    """
    types = {c.type for c in columns} & PRINCIPAL_TYPES
    names: dict[str, str] = {}
    for column_type in types:
        model = User if column_type == ColumnType.user else UserGroup
        label = User.username if column_type == ColumnType.user else UserGroup.name
        rows = await db.execute(
            select(model.uuid, label).where(model.deleted_at.is_(None))
        )
        names.update({str(uuid): name for uuid, name in rows})
    return names


async def _lookup_row_labels(
    db: AsyncSession, column: Column, user
) -> dict[str, str]:
    """row uuid -> label, for one lookup column's target list.

    Resolving a label means *reading the target list*, so it obeys the target's
    permissions, not the exported list's: `view` on the target, then the same
    per-record filter every other read path applies. A caller who cannot read the
    target gets no labels and the cells export as uuids — which is what they saw
    before labels existed, and leaks nothing. Granting `view` on a list must not
    hand over the contents of a *different* list it happens to point at.

    The label comes from the same display column the item service matches on when
    importing (`display_column_key`), so what we write is what we can read back. A
    row whose label is empty keeps its uuid: it is the only value that identifies
    it.
    """
    from src.permissions.enums import Action, ResourceType
    from src.permissions.services import PermissionService

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
        return {}
    if not await PermissionService.has(
        db, user, ResourceType.list, target.id, Action.view
    ):
        return {}

    key = await display_column_key(db, target.id, config)
    if key is None:
        return {}

    rows = await db.execute(
        select(ListItem.uuid, ListItem.data[key].astext).where(
            ListItem.list_id == target.id,
            ListItem.deleted_at.is_(None),
            await PermissionService.item_view_filter(db, user, target),
        )
    )
    return {str(uuid): label for uuid, label in rows if label}


async def _label_maps(
    db: AsyncSession, columns: list[Column], user
) -> dict[str, dict[str, str]]:
    """column uuid -> {stored uuid: label}, for every reference column exported.

    A file full of uuids is useless to a human, so reference cells (`lookup`,
    `user`, `group`) export the label. Import resolves the label back to the uuid,
    so the file round-trips. Built once per export rather than per row.
    """
    principals = await _principal_names(db, columns)
    maps: dict[str, dict[str, str]] = {}
    for column in columns:
        if column.type in PRINCIPAL_TYPES:
            maps[str(column.uuid)] = principals
        elif column.type == ColumnType.lookup:
            maps[str(column.uuid)] = await _lookup_row_labels(db, column, user)
    return maps


def _export_cell(
    column: Column, value: object, maps: dict[str, dict[str, str]]
) -> object:
    labels = maps.get(str(column.uuid))
    if not labels or value is None:
        return value
    if isinstance(value, list):
        return [labels.get(str(v), str(v)) for v in value]
    return labels.get(str(value), value)


async def export_rows(
    list_id: int,
    user,
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
    # Deferred like the other permission call sites: importing PermissionService at
    # module level would cycle back through auth/workspaces services.
    from src.permissions.services import PermissionService

    async with db_module.SessionLocal() as db:
        columns = await _ordered_columns(db, list_id, column_names)
        all_columns = await ListItemService._columns_by_key(db, list_id)

        # An export is a read of the records, so it must honor exactly the same
        # per-record visibility as the grid — otherwise a list-viewer could export
        # rows whose inheritance was broken and never granted to them.
        lst = (
            await db.execute(
                select(List)
                .where(List.id == list_id)
                .options(selectinload(List.workspace))
            )
        ).scalars().first()
        if lst is None:
            return
        visible = await PermissionService.item_view_filter(db, user, lst)

        stmt = select(ListItem).where(
            ListItem.deleted_at.is_(None), ListItem.list_id == list_id, visible
        )
        stmt = apply_filters(stmt, filters, all_columns)
        stmt = apply_sorts(stmt, sorts, all_columns)
        if limit is not None:
            stmt = stmt.limit(limit)

        # Resolved before the cursor opens: issuing another query on this session
        # mid-stream would disturb the in-flight result.
        maps = await _label_maps(db, columns, user)

        # Select only the JSONB payload, not whole ORM entities: Core rows do not
        # enter the session identity map, so memory stays flat over a large
        # export. yield_per bounds the client-side row buffer too.
        result = await db.stream_scalars(
            stmt.with_only_columns(ListItem.data).execution_options(yield_per=1000)
        )

        if fmt == "json":
            first = True
            yield "["
            async for data in result:
                obj = {
                    c.name: _export_cell(c, (data or {}).get(str(c.uuid)), maps)
                    for c in columns
                }
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
            async for data in result:
                row = data or {}
                writer.writerow(
                    [
                        _csv_cell(_export_cell(c, row.get(str(c.uuid)), maps))
                        for c in columns
                    ]
                )
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
    if (
        column.type in (ColumnType.choice, ColumnType.lookup) or
        column.type in PRINCIPAL_TYPES
    ) and multiple:
        if isinstance(raw, list):
            return raw
        return [p.strip() for p in str(raw).split(";") if p.strip()]
    return raw if isinstance(raw, str) else str(raw)


async def import_records(
    db: AsyncSession,
    lst: List,
    rows: Iterable[Tuple[int, dict]],
) -> dict:
    """Validate and insert rows through the item service. ``rows`` yields
    ``(row_number, values)`` pairs so the caller controls how a row maps to a
    file line. Each row runs in a savepoint so a failure is isolated; commits
    happen in batches and the committed rows are expunged so memory stays flat
    over a large import. Returns a summary dict {created, errors}."""
    columns = await ListItemService._columns_by_key(db, lst.id)
    by_name = {c.name: c for c in columns.values()}

    created = 0
    errors: list[dict] = []
    batch: list[ListItem] = []

    for row_number, raw_row in rows:
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
                item = ListItem(list_id=lst.id, data=data)
                db.add(item)

            created += 1
            batch.append(item)
            if len(batch) >= _IMPORT_BATCH:
                await db.commit()
                # Drop the committed rows from the identity map (but not `lst`)
                # so the session does not retain the whole import.
                for committed in batch:
                    db.expunge(committed)
                batch.clear()
        except (ValidationError, ConflictError, UnknownReferenceError) as exc:
            errors.append({"row": row_number, "message": str(exc)})
        except Exception as exc:  # pragma: no cover - defensive
            errors.append({"row": row_number, "message": f"unexpected error: {exc}"})

    await db.commit()
    return {"created": created, "errors": errors}
