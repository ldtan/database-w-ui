"""Bring a text column's stored values back under the sanitiser when it turns rich.

`TableRecordService._validate_data` sanitises a `text` cell on the way in — but only when
the column is in `rich` mode *at the moment of that write* (`richtext.is_rich`). That is
the whole guarantee the renderer leans on, and on its own it is not enough, because the
mode is not fixed for the life of a column.

A plain text column stores exactly what it was sent, angle brackets and all — that is
deliberate and asserted (`test_a_plain_text_column_is_left_alone`), because `<` is
ordinary text and sanitising it would corrupt data nobody asked us to touch. Two
operations then make those same bytes renderable as HTML:

- `ColumnService.update` turning `text_mode` on, which the column dialog offers for an
  existing column; and
- `ConversionService.apply`, which retypes a column into the conversion's target config
  (`text -> text` is a supported pair, so the target may simply be the rich version of
  the same column) over values the conversion pass wrote without ever sanitising them.
  It calls this inside its own transaction, so the column never reads as rich while an
  unsanitised value is still stored.

Neither rewrites a record, so without this the stored value stays as the plain column
accepted it and the grid then hands it to `dangerouslySetInnerHTML`. Sanitising on
*read* would also close that, and was rejected: it puts an nh3 pass on every rich cell
of every page render and every exported row, to repair a state that only two rare
schema operations can create. Repair it where it is created instead.

**This runs inside the caller's transaction, and that is load-bearing.** The config
change and the sweep commit together, so there is no instant at which the column reads
as rich while an unsanitised value is still stored. Rows are taken `FOR UPDATE` (and
deliberately *not* `SKIP LOCKED` — a row being edited right now is a row that must not
be missed), which also settles the race with a concurrent record edit in both
directions: `TableRecordService.update` takes its row lock *before* it reads the column
config, so either it committed first and this reads and cleans the value it wrote, or
this locked first and the writer resumes against the committed rich config and sanitises
itself.

The one gap that leaves is a concurrent **insert**, which has no row to lock: a `create`
that read the old plain config microseconds before this commits, and inserts after it,
stores one unsanitised value under a now-rich column. Closing that would mean
serialising every insert against a schema change. It is left open knowingly, and it is
a millisecond-wide window on a rare admin action rather than the days-wide one this
module exists to close.

Soft-deleted records are swept too: a record in the bin can be restored, and it must not
come back holding markup that the column now renders.
"""
import sqlalchemy as sa
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..records.models import TableRecord
from .enums import ColumnType
from .richtext import is_rich, sanitize

# Rows read, cleaned and written per round trip. Matches the conversion backfill's and
# the re-index's, for the same reason: every row costs Python work, so a chunk bounds
# the memory a sweep holds rather than the total work it does.
CHUNK = 500

_WRITE = sa.text(
    "UPDATE table_records "
    "   SET data = jsonb_set(data, ARRAY[:key], to_jsonb(CAST(:value AS text))) "
    " WHERE id = :row_id"
)


def turns_rich(column_type: ColumnType, before: dict, after: dict) -> bool:
    """Whether this config change is the one that makes stored values renderable.

    False when the column was already rich — those values went through `sanitize` on
    their own writes — so an ordinary edit to a rich column's length limits does not
    drag the whole table through a sweep.
    """
    return (
        column_type == ColumnType.text
        and is_rich(after or {})
        and not is_rich(before or {})
    )


async def resanitize_column(db: AsyncSession, table_id: int, column_uuid) -> int:
    """Rewrite every stored value of this column through `sanitize`. Does not commit.

    Returns how many records changed. A value already clean is left alone rather than
    rewritten with identical bytes — most of a table is ordinary prose that nh3 returns
    untouched, so the common case writes nothing.

    Only cells holding a JSON **string** are considered: those are exactly the ones the
    renderer stringifies into markup. A text column is never `multiple`, so an array
    here would be a legacy shape that `coerce_shape` already drops to its first element
    on read; sweeping it is not this function's job and guessing at it would be a
    silent rewrite.
    """
    key = str(column_uuid)
    cell = TableRecord.data[key]

    changed = 0
    after_id = 0
    while True:
        rows = (
            await db.execute(
                select(TableRecord.id, cell.astext)
                .where(
                    TableRecord.table_id == table_id,
                    TableRecord.id > after_id,
                    func.jsonb_typeof(cell) == "string",
                )
                .order_by(TableRecord.id)
                .limit(CHUNK)
                .with_for_update()
            )
        ).all()
        if not rows:
            return changed
        after_id = rows[-1][0]

        writes = [
            {"row_id": row_id, "key": key, "value": cleaned}
            for row_id, stored, cleaned in (
                (row_id, stored, sanitize(stored)) for row_id, stored in rows
            )
            if cleaned != stored
        ]
        if writes:
            await db.execute(_WRITE, writes)
            changed += len(writes)
