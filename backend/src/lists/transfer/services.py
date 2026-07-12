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
# Rows whose labels are resolved in one round trip while streaming an export.
_EXPORT_BATCH = 500
# Cap on labels held across batches, so an enormous export stays bounded.
_LABEL_CACHE_MAX = 50_000

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


class _LabelResolver:
    """Turns the reference uuids in a batch of rows into labels.

    A file full of uuids is useless to a human, so reference cells (`lookup`,
    `user`, `group`) export the label, and import resolves the label back to the
    uuid so the file round-trips.

    Resolved **a batch at a time, for only the uuids that batch actually holds** —
    never by loading the target list (or the users table) up front. Export exists to
    stream: server memory has to stay flat regardless of how big the list, or the
    list it points at, happens to be. Labels are cached across batches, and the
    cache is dropped if it grows past ``_LABEL_CACHE_MAX``, so a pathological export
    stays bounded too.

    It reads on its own session: the export's cursor is open on the caller's, and
    issuing a query there mid-stream would disturb the in-flight result.
    """

    def __init__(self, db: AsyncSession, columns: list[Column], user):
        self._db = db
        self._user = user
        self._columns = [
            c for c in columns if c.type in PRINCIPAL_TYPES or c.type == ColumnType.lookup
        ]
        # column uuid -> how to resolve it (None once we know we cannot)
        self._plan: dict[str, Optional[dict]] = {}
        # column uuid -> {row/principal uuid: label}
        self._cache: dict[str, dict[str, str]] = {}

    async def prepare(self) -> None:
        """Work out, once, how each reference column resolves — and whether it may."""
        from src.permissions.enums import Action, ResourceType
        from src.permissions.services import PermissionService

        for column in self._columns:
            key = str(column.uuid)
            self._cache[key] = {}

            if column.type in PRINCIPAL_TYPES:
                model = User if column.type == ColumnType.user else UserGroup
                label = (
                    User.username if column.type == ColumnType.user else UserGroup.name
                )
                # Usernames, not the connected person's real name: person records are
                # private, and an export is readable by anyone who can read the list.
                self._plan[key] = {"model": model, "label": label}
                continue

            # Resolving a lookup label means *reading the target list*, so it obeys
            # the target's permissions, not the exported list's. A caller who cannot
            # read the target gets uuids — what they saw before labels existed.
            # Granting `view` on a list must not hand over the contents of a
            # *different* list it happens to point at.
            config = column.config or {}
            target = (
                await self._db.execute(
                    select(List)
                    .options(selectinload(List.workspace))
                    .where(
                        List.uuid == coerce_uuid(config.get("target_list_uuid")),
                        List.deleted_at.is_(None),
                    )
                )
            ).scalars().first()
            if target is None or not await PermissionService.has(
                self._db, self._user, ResourceType.list, target.id, Action.view
            ):
                self._plan[key] = None
                continue

            display_key = await display_column_key(self._db, target.id, config)
            if display_key is None:
                self._plan[key] = None
                continue

            self._plan[key] = {
                "list_id": target.id,
                "display_key": display_key,
                "visible": await PermissionService.item_view_filter(
                    self._db, self._user, target
                ),
            }

    async def load(self, batch: list[dict]) -> None:
        """Fetch the labels this batch of records needs and not one row more."""
        for column in self._columns:
            key = str(column.uuid)
            plan = self._plan.get(key)
            if plan is None:
                continue

            known = self._cache[key]
            if len(known) > _LABEL_CACHE_MAX:
                known.clear()  # bound the memory an enormous export can hold

            wanted: set[str] = set()
            for row in batch:
                value = (row or {}).get(key)
                for uuid in value if isinstance(value, list) else [value]:
                    if uuid is not None and str(uuid) not in known:
                        wanted.add(str(uuid))
            if not wanted:
                continue

            parsed = [u for u in (coerce_uuid(w) for w in wanted) if u is not None]
            if not parsed:
                continue

            if "model" in plan:
                rows = await self._db.execute(
                    select(plan["model"].uuid, plan["label"]).where(
                        plan["model"].deleted_at.is_(None),
                        plan["model"].uuid.in_(parsed),
                    )
                )
            else:
                rows = await self._db.execute(
                    select(
                        ListItem.uuid, ListItem.data[plan["display_key"]].astext
                    ).where(
                        ListItem.list_id == plan["list_id"],
                        ListItem.deleted_at.is_(None),
                        ListItem.uuid.in_(parsed),
                        plan["visible"],
                    )
                )
            known.update({str(uuid): label for uuid, label in rows if label})

    def cell(self, column: Column, value: object) -> object:
        """The value to write: the label if we have one, else the uuid — which for a
        row with an empty label is the only thing that identifies it."""
        labels = self._cache.get(str(column.uuid))
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

        # Select only the JSONB payload, not whole ORM entities: Core rows do not
        # enter the session identity map, so memory stays flat over a large
        # export. yield_per bounds the client-side row buffer too.
        result = await db.stream_scalars(
            stmt.with_only_columns(ListItem.data).execution_options(yield_per=1000)
        )

        # Labels are resolved per batch, on their own session: this one has a cursor
        # open, and querying it mid-stream would disturb the in-flight result.
        async with db_module.SessionLocal() as label_db:
            labels = _LabelResolver(label_db, columns, user)
            await labels.prepare()

            async def batches() -> AsyncIterator[list[dict]]:
                """The stream in fixed-size chunks, each with its labels loaded."""
                batch: list[dict] = []
                async for data in result:
                    batch.append(data or {})
                    if len(batch) >= _EXPORT_BATCH:
                        await labels.load(batch)
                        yield batch
                        batch = []
                if batch:
                    await labels.load(batch)
                    yield batch

            if fmt == "json":
                first = True
                yield "["
                async for batch in batches():
                    for row in batch:
                        obj = {
                            c.name: labels.cell(c, row.get(str(c.uuid)))
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
                async for batch in batches():
                    for row in batch:
                        writer.writerow(
                            [
                                _csv_cell(labels.cell(c, row.get(str(c.uuid))))
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
