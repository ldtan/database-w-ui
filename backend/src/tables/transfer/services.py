"""Streaming export and bulk import of table records.

Export streams rows straight from an async DB cursor so server memory stays
flat regardless of table size. Import parses the upload row by row, validating
and inserting through the record service inside a per-row savepoint (one bad row
never sinks the batch) with periodic commits.
"""
import csv
import io
import json
import logging
from typing import AsyncIterator, Iterable, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.auth.users.models import User, UserGroup
from src.core.config import settings
from src.core.exceptions import ConflictError, UnknownReferenceError, ValidationError
from src.core.utils import coerce_uuid
from src.infrastructure import database as db_module

from ..columns.enums import (
    COMPUTED_TYPES,
    MULTIPLE_TYPES,
    NUMERIC_TYPES,
    PRINCIPAL_TYPES,
    ColumnType,
)
from ..columns.models import Column, in_shape
from ..columns.validation import format_location, parse_duration, parse_location
from ..events.broker import broker
from ..records.filtering import apply_filters, apply_sorts
from ..records.models import TableRecord
from ..records.references import label_sources
from ..records.services import (
    TableRecordService,
    coerce_shape,
    computed_columns,
    display_column_key,
    shape_values,
)
from ..models import Table

# Commit every N successfully-prepared rows to bound the transaction size.
_IMPORT_BATCH = 500
# Rows whose labels are resolved in one round trip while streaming an export.
_EXPORT_BATCH = 500
# Cap on labels held across batches, so an enormous export stays bounded.
_LABEL_CACHE_MAX = 50_000

_SKIP = object()  # sentinel: leave this cell out of the record entirely

logger = logging.getLogger(__name__)


# -- export --------------------------------------------------------------

async def _ordered_columns(
    db: AsyncSession, table_id: int, names: list[str]
) -> list[Column]:
    result = await db.execute(
        select(Column)
        .where(Column.table_id == table_id, in_shape())
        .order_by(Column.position, Column.id)
    )
    cols = list(result.scalars().all())
    if names:
        by_name = {c.name: c for c in cols}
        return [by_name[n] for n in names if n in by_name]
    return cols


def _csv_cell(value: object, column: Optional[Column] = None) -> str:
    """One cell as the file writes it.

    ``column`` is optional because `_error_values` calls this on a *raw uploaded
    row*, where there is no column to attribute a cell to — every header is kept,
    including ones this table has no column for. With one, a type whose value is not
    a scalar gets its own rendering: today only `location`, whose cell is an object
    and whose `str()` would be a Python dict repr in a spreadsheet.
    """
    if value is None:
        return ""
    if column is not None and column.type == ColumnType.location:
        return format_location(value) or ""
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
    never by loading the target table (or the users table) up front. Export exists to
    stream: server memory has to stay flat regardless of how big the table, or the
    table it points at, happens to be.

    Labels are cached by **what they resolve against** — the users table, the groups
    table, one target table — not by the column that happens to reference it, so two
    `user` columns on the same table share one cache and one query. A uuid that does
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
        # table id), or None once we know it cannot be resolved at all.
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
                    # the table.
                    self._plan[scope] = {"model": model, "label": label}
                    self._cache[scope] = {}
                continue

            # Resolving a lookup label means *reading the target table*, so it obeys
            # the target's permissions, not the exported table's. A caller who cannot
            # read the target gets uuids — what they saw before labels existed.
            # Granting `view` on a table must not hand over the contents of a
            # *different* table it happens to point at.
            config = column.config or {}
            target = (
                await self._db.execute(
                    select(Table)
                    .options(selectinload(Table.workspace))
                    .where(
                        Table.uuid == coerce_uuid(config.get("target_table_uuid")),
                        Table.deleted_at.is_(None),
                    )
                )
            ).scalars().first()
            if target is None or not await PermissionService.has(
                self._db, self._user, ResourceType.table, target.id, Action.lookup
            ):
                self._scope_of[key] = None
                continue

            display_key = await display_column_key(self._db, target.id, config)
            if display_key is None:
                self._scope_of[key] = None
                continue

            # Two lookups at the same table with the same display column resolve
            # identically, so they share a scope.
            scope = f"table:{target.id}:{display_key}"
            self._scope_of[key] = scope
            if scope not in self._plan:
                self._plan[scope] = {
                    "table_id": target.id,
                    "display_key": display_key,
                    "visible": await PermissionService.record_view_filter(
                        self._db, self._user, target, Action.lookup
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
                        TableRecord.uuid, TableRecord.data[plan["display_key"]].astext
                    ).where(
                        TableRecord.table_id == plan["table_id"],
                        TableRecord.uuid.in_(parsed),
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

    def resolves(self, column: Column) -> bool:
        """Whether this column's uuids can be turned into labels at all.

        False when the target is gone, has no display column, or the caller may not
        read it. An export shrugs and writes uuids; a *conversion* must not, because
        it writes the result down permanently and for everyone — so it asks first.
        """
        return self._scope_of.get(str(column.uuid)) is not None

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


def json_row(
    columns: list[Column], labels: "_LabelResolver", row: dict
) -> dict:
    """One shaped record as the JSON export writes it: column **name** -> value.

    Extracted rather than inlined because it has a second caller — `qr_payloads` below,
    which builds the JSON a QR code carries. That is not a tidiness argument. A QR code
    generated by this app is meant to be readable by `_parse_cell` on the way back in, and
    "the code round-trips through import" is only a *property* if the code and the export
    are the same bytes. Two expressions that happened to agree today would be two that
    could stop agreeing, silently, in the one direction nobody re-reads: a group's label,
    for instance, is its slug `name` here and its `display_name` on screen (see
    `_LabelResolver.prepare`), so a second implementation written from the screen would
    produce codes that do not import.

    `coerce_shape` before `labels.cell`, because a cell may hold the other shape than its
    column currently declares (`multiple` is toggleable and converges in the background),
    and the resolver reads a table as a table.
    """
    return {
        c.name: labels.cell(c, coerce_shape(c, row.get(str(c.uuid)))) for c in columns
    }


async def export_rows(
    table_id: int,
    user,
    fmt: str,
    column_names: list[str],
    filters: Optional[dict],
    sorts: Optional[list],
    limit: Optional[int],
    include_archived: bool = False,
) -> AsyncIterator[str]:
    """Yield the export file in chunks. Opens its own session so it is
    independent of the request's dependency lifecycle while streaming.
    ``db_module.SessionLocal`` is read at call time so tests that repoint it at
    the test database are honored."""
    # Deferred like the other permission call sites: importing PermissionService at
    # module level would cycle back through auth/workspaces services.
    from src.permissions.services import PermissionService

    async with db_module.SessionLocal() as db:
        columns = await _ordered_columns(db, table_id, column_names)
        all_columns = await TableRecordService._columns_by_key(db, table_id)
        # Calculated columns are exported like any other now that they hold a value: a
        # spreadsheet of orders without the line totals is not the export anyone asked
        # for. The file still imports cleanly back, because `_parse_cell` skips the
        # column rather than erroring and the formula rewrites the cell from the row's
        # own values on the way in — the only correct source for it.
        #
        # The value written is **computed here, not read from the record.** An export
        # leaves the app into a file nothing can correct afterwards, so it is the last
        # place that should trust the stored copy, which is a query index and may lag a
        # formula edit. `shape_values` is shared with the grid for exactly this reason,
        # and takes plain dicts precisely so a streaming reader can use it. `all_columns`
        # rather than `columns`: a formula may read a column the caller did not tick.
        computed, key_of_name = computed_columns(all_columns)

        # An export is a read of the records, so it must honor exactly the same
        # per-record visibility as the grid — otherwise a table-viewer could export
        # rows whose inheritance was broken and never granted to them.
        table = (
            await db.execute(
                select(Table)
                .where(Table.id == table_id)
                .options(selectinload(Table.workspace))
            )
        ).scalars().first()
        if table is None:
            return
        visible = await PermissionService.record_view_filter(db, user, table)

        stmt = select(TableRecord).where(
            TableRecord.deleted_at.is_(None), TableRecord.table_id == table_id, visible
        )
        # Archived rows are excluded by default, like every other read of the table —
        # but on request they are included, because an export is also the way data
        # leaves the app and archiving must not be able to strand records here.
        if not include_archived:
            stmt = stmt.where(TableRecord.archived_at.is_(None))
        stmt = apply_filters(stmt, filters, all_columns, user)
        # The same label ordering the grid gets. An export is a snapshot of a view, and
        # a file whose rows came out in a different order from the screen it was taken
        # from is the sort of discrepancy nobody can debug after the fact.
        stmt = apply_sorts(
            stmt,
            sorts,
            all_columns,
            sources=await label_sources(db, user, all_columns, sorts),
        )
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
            stmt.with_only_columns(TableRecord.data).execution_options(yield_per=1000)
        )

        async def batches() -> AsyncIterator[list[dict]]:
            """The stream in fixed-size chunks, shaped, each with its labels loaded.

            Batching exists to resolve labels in one round trip per chunk; the
            rows are still serialized (and yielded) one at a time.

            Shaping happens here rather than in the writers so both formats get it
            from one place, and before `labels.load` so the resolver sees the same
            record the file will.
            """
            batch: list[dict] = []
            async for data in result:
                batch.append(shape_values(all_columns, computed, key_of_name, data or {}))
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
                    obj = json_row(columns, labels, row)
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
                                labels.cell(c, coerce_shape(c, row.get(str(c.uuid)))),
                                c,
                            )
                            for c in columns
                        ]
                    )
                    # Per row, not per batch: the buffer holds one row, and the
                    # client starts receiving bytes immediately.
                    yield flush()


# -- QR payloads ---------------------------------------------------------

# The key the record's own uuid is written under, when it is asked for.
#
# **The underscore is load-bearing.** A column `name` is a `NameSlug`
# (`^[a-z][a-z0-9_]*$`), so `uuid` is a perfectly legal column name — a table may already
# have one, and writing the record's id under that key would overwrite the column's value
# with no way to tell. A leading underscore cannot be a column name, which makes `_` the
# reserved namespace for payload metadata and this collision impossible rather than
# unlikely. `qrFill.ts` skips `_`-prefixed keys for the same reason, so a generated code
# scans back without reporting one.
QR_UUID_KEY = "_uuid"


async def qr_payloads(
    db: AsyncSession,
    table: Table,
    user,
    record_uuids: list,
    column_names: list[str],
    include_uuid: bool = True,
) -> list[dict]:
    """The JSON a QR code should carry, per record: ``[{record_uuid, payload}]``.

    **The same bytes a one-row JSON export would write**, because it is the same
    `json_row` and the same `_LabelResolver` — see `json_row` for why that is a property
    worth paying for rather than a coincidence. A code this produces is therefore readable
    by `_parse_cell`, which is what lets a printed badge be scanned back into a record.

    Takes a session rather than opening one, unlike `export_rows`: there is no streaming
    here, so there is no response outliving the request's dependency lifecycle and no
    reason to hold a second connection.

    **A record the caller cannot see is omitted, not refused.** A sheet of badges for a
    selection must not fail wholesale because one row among forty carries its own grants —
    the same shrug `FileService.metadata` makes for a batch of file ids.

    Archived records are included. The selection comes from what is on screen, and an
    archived record is still viewable; only the bin is excluded, because a code for a
    record somebody deleted is a badge for a person who is not coming.
    """
    from src.permissions.services import PermissionService

    if not record_uuids:
        return []

    # **An empty `columns` means *no* columns here, where it means *every* column to an
    # export.** The same input, read two opposite ways, so it is short-circuited rather
    # than passed to `_ordered_columns` (which would return the lot).
    #
    # The readings differ because the requests do. Asking for a file with no columns named
    # is asking for the data; asking for a *badge* with no columns named is asking for a
    # code that identifies the record and says nothing else — a perfectly sensible badge,
    # and the only one that is safe as a default. Inheriting "all" would put every column
    # on it including the four audit fields, which blows the size budget a code actually
    # has and prints more than anyone asked for.
    columns = (
        await _ordered_columns(db, table.id, column_names) if column_names else []
    )
    all_columns = await TableRecordService._columns_by_key(db, table.id)
    # Computed per row rather than read from the record, exactly as the export does: the
    # stored copy of a `calculated` cell is a query index that may lag a formula edit, and
    # a printed code is as uncorrectable afterwards as a file is.
    computed, key_of_name = computed_columns(all_columns)

    visible = await PermissionService.record_view_filter(db, user, table)
    rows = (
        await db.execute(
            select(TableRecord.uuid, TableRecord.data).where(
                TableRecord.table_id == table.id,
                TableRecord.deleted_at.is_(None),
                TableRecord.uuid.in_(record_uuids),
                visible,
            )
        )
    ).all()

    shaped = [
        (uuid, shape_values(all_columns, computed, key_of_name, data or {}))
        for uuid, data in rows
    ]
    labels = _LabelResolver(db, columns, user)
    await labels.prepare()
    # One round trip for every reference in the whole batch, not one per record — a sheet
    # of forty badges with two lookup columns would otherwise be eighty queries.
    await labels.load([row for _, row in shaped])

    # Returned in the order asked for rather than the order the database answered, so a
    # printed sheet matches the selection on screen. `in_()` gives no ordering, and a
    # caller cannot reorder what it cannot match up.
    by_uuid = {
        str(uuid): json_row(columns, labels, row) for uuid, row in shaped
    }
    out = []
    for wanted in record_uuids:
        payload = by_uuid.get(str(wanted))
        if payload is None:
            continue  # not visible, binned, or not in this table
        if include_uuid:
            # First in the dict, so a person reading the JSON sees what it identifies
            # before what it says. Python preserves insertion order and `json.dumps`
            # honours it.
            payload = {QR_UUID_KEY: str(wanted), **payload}
        out.append({"record_uuid": str(wanted), "payload": payload})
    return out


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
    if column.type == ColumnType.location:
        # The canonical one-line form the export writes, read straight back — which
        # is what makes a location column round-trip through a file. A JSON upload
        # may also hand over the object itself, and does not need parsing.
        if isinstance(raw, dict):
            return raw
        parsed = parse_location(str(raw))
        return parsed if parsed is not None else _SKIP
    if column.type == ColumnType.duration:
        # Read before the generic numeric branch below, and a superset of it: a bare
        # number is seconds (which is what the export writes, so a file round-trips),
        # and "1:30" or "90m" are the forms somebody hand-writing a file will use.
        if isinstance(raw, bool):
            raise ValidationError(f"column '{column.name}': '{raw}' is not a duration")
        if isinstance(raw, (int, float)):
            return raw
        seconds = parse_duration(column.config or {}, str(raw))
        if seconds is None:
            raise ValidationError(
                f"column '{column.name}': '{raw}' is not a duration "
                f"(try 5400, 1:30, or 90m)"
            )
        return seconds
    if column.type in NUMERIC_TYPES:
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
    # The same set the config validator now enforces `multiple` against, named once
    # rather than spelled out again: choice, lookup, user, group. `_csv_cell` joins a
    # table with `;` for *any* column, which was a latent disagreement while the flag
    # could be set on a `text` column — the export would join and the import would
    # not split. It cannot be set there now, so the two agree by construction, and
    # this reads as the reason rather than as a coincidence.
    if column.type in MULTIPLE_TYPES and multiple:
        if isinstance(raw, list):
            return raw
        return [p.strip() for p in str(raw).split(";") if p.strip()]
    return raw if isinstance(raw, str) else str(raw)


def _error_values(raw_row: object) -> dict[str, str]:
    """The row as the file gave it, ready to be written back out as a file.

    Every header is kept, including ones this table has no column for: the point is
    that the failures can be corrected and re-uploaded, and a file that quietly lost
    the columns import ignores is not the file the person uploaded.

    Cells are stringified through `_csv_cell`, the same function export writes with —
    so a JSON upload's tables and booleans come back in the spelling `_parse_cell`
    reads (`a;b`, `true`), and the fix file round-trips whichever format it came from.
    Non-string keys are dropped: `csv.DictReader` files a row's surplus cells under
    ``None``, and there is no header to give them back under.
    """
    if not isinstance(raw_row, dict):
        # A JSON file whose elements are not objects. The row failed for that reason
        # and the message says so; there are no cells to hand back.
        return {}
    return {
        key: _csv_cell(value)
        for key, value in raw_row.items()
        if isinstance(key, str)
    }


async def import_records(
    db: AsyncSession,
    table: Table,
    rows: Iterable[Tuple[int, dict]],
    user=None,
) -> dict:
    """Validate and insert rows through the record service. ``rows`` yields
    ``(row_number, values)`` pairs so the caller controls how a row maps to a
    file line. Each row runs in a savepoint so a failure is isolated; commits
    happen in batches and the committed rows are expunged so memory stays flat
    over a large import. ``user`` is the importer, attributed to any
    `current_user` default/onupdate. Returns a summary dict {created, errors}.

    More than ``settings.IMPORT_MAX_ROWS`` rows raises `ValidationError` and writes
    nothing. The check lives here rather than in the router so both upload formats
    inherit it from one place."""
    # Counted before a single row is written. Checking as we went would leave a file
    # one row over the cap having imported the thousand rows before it, which is the
    # one outcome the caller cannot easily undo. Stops one row past the limit, so an
    # enormous file is refused rather than held in order to be measured.
    max_rows = settings.IMPORT_MAX_ROWS
    counted: list[Tuple[int, dict]] = []
    for row in rows:
        counted.append(row)
        if len(counted) > max_rows:
            raise ValidationError(
                f"This file has more than {max_rows} rows; split it and import "
                "each part separately"
            )

    columns = await TableRecordService._columns_by_key(db, table.id)
    by_name = {c.name: c for c in columns.values()}

    created = 0
    errors: list[dict] = []
    batch: list[TableRecord] = []

    for row_number, raw_row in counted:
        try:
            # A JSON file whose elements are not objects — `["just a string"]`. It
            # used to reach `raw_row.items()` and be reported by the catch-all as
            # `unexpected error: 'str' object has no attribute 'records'`, which is a
            # Python traceback fragment shown to somebody who uploaded a file. Said
            # here instead, in the file's own terms, and as a `ValidationError` so it
            # travels the same path as every other thing wrong with a row.
            # `_error_values` already knows to hand back `{}` for one of these: there
            # are no headers to correct and re-upload.
            if not isinstance(raw_row, dict):
                raise ValidationError(
                    "each row must be an object of column names to values"
                )

            incoming: dict = {}
            for key, value in raw_row.items():
                column = by_name.get(key)
                if column is None:
                    continue  # ignore columns not on this table
                parsed = _parse_cell(column, value)
                if parsed is not _SKIP:
                    incoming[str(column.uuid)] = parsed

            async with db.begin_nested():
                data = await TableRecordService._validate_data(
                    db, table, columns, incoming, existing=None, user=user
                )
                await TableRecordService._enforce_unique(
                    db, table.id, columns, data, exclude_id=None
                )
                record = TableRecord(table_id=table.id, data=data)
                db.add(record)

            created += 1
            batch.append(record)
        except (ValidationError, ConflictError, UnknownReferenceError) as exc:
            # The three the record service raises to say what is wrong with a *row*.
            # Their messages are written for the person who uploaded the file and
            # are the whole point of reporting per row rather than failing the
            # upload, so they go back verbatim.
            errors.append(
                {
                    "row": row_number,
                    "message": str(exc),
                    "values": _error_values(raw_row),
                }
            )
            continue
        except (SQLAlchemyError, ValueError):
            # Anything else this row can raise: a driver-level refusal the savepoint
            # has just rolled back, or a parse that got past `_parse_cell`. It used
            # to be `except Exception` echoing `str(exc)` into the response, which
            # for a SQLAlchemy error is the failing statement, its parameters, and
            # the table and constraint names — an internal detail handed to whoever
            # can upload a CSV. The detail belongs in the log, where it is useful and
            # not published; the row gets a message that says which row and no more.
            #
            # Narrow rather than bare, so a genuine defect (a missing attribute, a
            # bad type in this function) still crashes the request instead of being
            # written into the report as though the file were at fault.
            logger.exception(
                "import: row %s of table %s failed", row_number, table.id
            )
            errors.append(
                {
                    "row": row_number,
                    "message": "could not be imported (see server logs)",
                    "values": _error_values(raw_row),
                }
            )
            continue

        # Outside the `try`, and that is the point. This commit is not the row's
        # business: it writes the whole batch, so a failure here is not something to
        # record against `row_number` and go on from. It used to sit inside, where
        # the bare `except` caught it, left the session in a failed state, and let
        # the loop run on — every subsequent row then failed too, and the final
        # commit below raised out of the endpoint as a 500 after the summary had
        # already been half-built. Letting it propagate is the honest answer: the
        # import cannot continue, and nothing pretends it did.
        if len(batch) >= _IMPORT_BATCH:
            await db.commit()
            # Drop the committed rows from the identity map (but not `table`)
            # so the session does not retain the whole import.
            for committed in batch:
                db.expunge(committed)
            batch.clear()

    await db.commit()
    # Once for the import, not once per row. The rows are committed in batches, so
    # a per-row notify would also be honest — but the granularity a watcher cares
    # about is "this import happened", and the broker's mailbox would coalesce a
    # thousand of them into this same single wake-up anyway.
    if created:
        broker.notify(table.id)
    return {"created": created, "errors": errors}
