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

from ..columns.enums import COMPUTED_TYPES, PRINCIPAL_TYPES, ColumnType
from ..columns.models import Column
from ..items.filtering import apply_filters, apply_sorts
from ..items.models import ListItem
from ..items.services import ListItemService, coerce_shape, display_column_key
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
    list it points at, happens to be.

    Labels are cached by **what they resolve against** — the users table, the groups
    table, one target list — not by the column that happens to reference it, so two
    `user` columns on the same list share one cache and one query. A uuid that does
    not resolve is cached as a miss, or every batch would re-ask the same dead
    question. The cache evicts oldest-first past ``_LABEL_CACHE_MAX``, so an
    enormous export stays bounded without throwing away what it just learned.

    It reads on the export's own session, between fetches from the streaming cursor.
    A session of its own would double the connections an export holds, which is how a
    handful of concurrent exports deadlock the pool.
    """

    def __init__(self, db: AsyncSession, columns: list[Column], user):
        self._db = db
        self._user = user
        self._columns = [
            c for c in columns if c.type in PRINCIPAL_TYPES or c.type == ColumnType.lookup
        ]
        # column uuid -> the scope it resolves against ("user" / "group" / a target
        # list id), or None once we know it cannot be resolved at all.
        self._scope_of: dict[str, Optional[str]] = {}
        # scope -> how to query it
        self._plan: dict[str, dict] = {}
        # scope -> {uuid: label or None for a known miss}; insertion-ordered, so the
        # oldest entries are the first to go when it has to be trimmed.
        self._cache: dict[str, dict[str, Optional[str]]] = {}

    async def prepare(self) -> None:
        """Work out, once, how each reference column resolves — and whether it may."""
        from src.permissions.enums import Action, ResourceType
        from src.permissions.services import PermissionService

        for column in self._columns:
            key = str(column.uuid)

            if column.type in PRINCIPAL_TYPES:
                scope = column.type.value  # every user column shares one scope
                self._scope_of[key] = scope
                if scope not in self._plan:
                    model = User if column.type == ColumnType.user else UserGroup
                    label = (
                        User.username
                        if column.type == ColumnType.user
                        else UserGroup.name
                    )
                    # Usernames, not the connected person's real name: person records
                    # are private, and an export is readable by anyone who can read
                    # the list.
                    self._plan[scope] = {"model": model, "label": label}
                    self._cache[scope] = {}
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
                self._scope_of[key] = None
                continue

            display_key = await display_column_key(self._db, target.id, config)
            if display_key is None:
                self._scope_of[key] = None
                continue

            # Two lookups at the same list with the same display column resolve
            # identically, so they share a scope.
            scope = f"list:{target.id}:{display_key}"
            self._scope_of[key] = scope
            if scope not in self._plan:
                self._plan[scope] = {
                    "list_id": target.id,
                    "display_key": display_key,
                    "visible": await PermissionService.item_view_filter(
                        self._db, self._user, target
                    ),
                }
                self._cache[scope] = {}

    async def load(self, batch: list[dict]) -> None:
        """Fetch the labels this batch of records needs and not one row more."""
        wanted: dict[str, set[str]] = {}
        for column in self._columns:
            key = str(column.uuid)
            scope = self._scope_of.get(key)
            if scope is None:
                continue

            known = self._cache[scope]
            for row in batch:
                value = (row or {}).get(key)
                for uuid in value if isinstance(value, list) else [value]:
                    if uuid is not None and str(uuid) not in known:
                        wanted.setdefault(scope, set()).add(str(uuid))

        for scope, uuids in wanted.items():
            parsed = [u for u in (coerce_uuid(w) for w in uuids) if u is not None]
            if not parsed:
                continue

            plan = self._plan[scope]
            # Deleted targets are resolved too. The cell holds a real relationship
            # that has not been cleaned up yet, and the person exporting could see
            # its label yesterday — writing the uuid instead helps nobody.
            if "model" in plan:
                rows = await self._db.execute(
                    select(plan["model"].uuid, plan["label"]).where(
                        plan["model"].uuid.in_(parsed),
                    )
                )
            else:
                rows = await self._db.execute(
                    select(
                        ListItem.uuid, ListItem.data[plan["display_key"]].astext
                    ).where(
                        ListItem.list_id == plan["list_id"],
                        ListItem.uuid.in_(parsed),
                        plan["visible"],
                    )
                )

            found = {str(uuid): label for uuid, label in rows if label}
            known = self._cache[scope]
            # Misses are cached too (as None): a cell pointing at a deleted row, or a
            # row this caller cannot see, must not be re-queried on every batch.
            known.update({uuid: found.get(uuid) for uuid in uuids})
            self._trim(known)

    @staticmethod
    def _trim(known: dict[str, Optional[str]]) -> None:
        """Evict oldest-first, rather than clearing: an export whose references
        exceed the cap would otherwise refill from scratch on every batch."""
        excess = len(known) - _LABEL_CACHE_MAX
        for uuid in [*known][:excess]:
            del known[uuid]

    def cell(self, column: Column, value: object) -> object:
        """The value to write: the label if we have one, else the uuid — which for a
        row with an empty label is the only thing that identifies it."""
        scope = self._scope_of.get(str(column.uuid))
        labels = self._cache.get(scope) if scope else None
        if not labels or value is None:
            return value
        # `or str(v)`, not `.get(v, str(v))`: a cached miss is stored as None, and it
        # means the same as never having asked — write the uuid.
        if isinstance(value, list):
            return [labels.get(str(v)) or str(v) for v in value]
        return labels.get(str(value)) or value


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
        # Calculated columns store no value; exporting them would write blanks, and
        # a re-import would then reject the header as a read-only column. Leave them
        # out so a file this app writes imports cleanly back into it.
        columns = [c for c in columns if c.type not in COMPUTED_TYPES]
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

        # Labels resolve on this same session, deliberately. Giving the resolver a
        # session of its own would have one export holding two pooled connections for
        # its whole duration — and enough concurrent exports, each holding one and
        # waiting on a second, exhaust the pool and stall until they time out. The
        # label queries run *between* fetches from the server-side cursor, never
        # during one, which asyncpg serves happily on the same connection.
        labels = _LabelResolver(db, columns, user)
        await labels.prepare()

        # Select only the JSONB payload, not whole ORM entities: Core rows do not
        # enter the session identity map, so memory stays flat over a large
        # export. yield_per bounds the client-side row buffer too.
        result = await db.stream_scalars(
            stmt.with_only_columns(ListItem.data).execution_options(yield_per=1000)
        )

        async def batches() -> AsyncIterator[list[dict]]:
            """The stream in fixed-size chunks, each with its labels loaded.

            Batching exists to resolve labels in one round trip per chunk; the
            rows are still serialized (and yielded) one at a time.
            """
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
                        c.name: labels.cell(c, coerce_shape(c, row.get(str(c.uuid))))
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
                            _csv_cell(
                                labels.cell(c, coerce_shape(c, row.get(str(c.uuid))))
                            )
                            for c in columns
                        ]
                    )
                    # Per row, not per batch: the buffer holds one row, and the
                    # client starts receiving bytes immediately.
                    yield flush()


# -- import --------------------------------------------------------------

def _parse_cell(column: Column, raw: object):
    """Coerce a raw file value (string from CSV, native from JSON) into the
    value the API expects. Returns _SKIP to omit the cell."""
    if raw is None:
        return _SKIP
    if isinstance(raw, str) and raw.strip() == "":
        return _SKIP
    if column.type in COMPUTED_TYPES:
        # Derived on read and read-only on write: a value in the file for one is
        # ignored rather than an error, so a hand-made file is forgiving.
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
    user=None,
) -> dict:
    """Validate and insert rows through the item service. ``rows`` yields
    ``(row_number, values)`` pairs so the caller controls how a row maps to a
    file line. Each row runs in a savepoint so a failure is isolated; commits
    happen in batches and the committed rows are expunged so memory stays flat
    over a large import. ``user`` is the importer, attributed to any
    `current_user` default/onupdate. Returns a summary dict {created, errors}."""
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
                    db, lst, columns, incoming, existing=None, user=user
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
