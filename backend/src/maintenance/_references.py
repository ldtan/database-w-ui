"""Clearing references to things that are gone for good.

This runs at **purge** time, not at delete time. Soft-deleting a record, a user or a
group deliberately leaves the cells pointing at it intact — that is what makes the
delete reversible, and what lets the cell render as "(deleted)" rather than pretend
the relationship never existed. Only once the target is destroyed, or has sat in the
bin past the retention window, is the pointer worth nothing and cleared.

Set-based on purpose: one pass per referencing column, however many records or targets
are involved. The obvious alternative — scrub per purged row — is one UPDATE per column
*per row*, which turns purging a million records into millions of statements.

Set-based is not the same as unbounded, though, and it used to be both: one statement
per column rewriting every record of the table that held a stale reference. Each column
now works through its records in batches, committed as it goes. Only the purge jobs
call this, and they work on targets already past the retention window — so there is
nothing here that a half-finished pass makes wrong, and re-running simply finds fewer
references left to clear.
"""
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.tables.columns.enums import PRINCIPAL_TYPES, ColumnType
from src.tables.columns.models import Column

# Records rewritten per transaction, matching the purge jobs that call this.
CHUNK = 5_000

# `live` is the set of uuids still worth pointing at. Everything else is dropped from
# the cell: nulled if the column holds one value, filtered out of the array if it
# holds several. Shape-agnostic, like the scrub it replaces — the CASE rewrites each
# row according to the shape it actually has, never according to what the column's
# `multiple` currently claims.
_CLEAR = """
UPDATE table_records
   SET data = jsonb_set(
           data,
           ARRAY[:key],
           CASE WHEN jsonb_typeof(data -> :key) = 'array'
                THEN COALESCE(
                         (SELECT jsonb_agg(elem)
                            FROM jsonb_array_elements(data -> :key) AS elem
                           WHERE elem #>> '{}' IN (SELECT uuid FROM live)),
                         '[]'::jsonb)
                ELSE 'null'::jsonb
           END)
 WHERE id = ANY(:ids)
"""

# The records whose cell still names something outside `live`. This used to be the
# UPDATE's own WHERE; it is separate now so the same predicate can pick a batch of ids
# first. It is also what makes the loop terminate: the rewrite above is exactly what
# turns this false for the rows it touched, so the next pass finds the next batch and
# eventually none.
_STALE = """
       (
         (jsonb_typeof(data -> :key) = 'array'
          AND EXISTS (SELECT 1
                        FROM jsonb_array_elements(data -> :key) AS elem
                       WHERE elem #>> '{}' NOT IN (SELECT uuid FROM live)))
      OR (jsonb_typeof(data -> :key) = 'string'
          AND (data ->> :key) NOT IN (SELECT uuid FROM live))
       )
"""

# Concatenated rather than formatted: the predicate contains `'{}'` (jsonb's "whole
# document" path), which str.format would read as a placeholder.
_BATCH = (
    "SELECT id FROM table_records WHERE table_id = :table_id AND "
    + _STALE
    + " ORDER BY id LIMIT :chunk"
)

# Targets still worth pointing at: alive, or in the bin but inside the window, where a
# restore is still promised and the reference has to be waiting for it.
_LIVE_USERS = """
WITH live AS (
    SELECT uuid::text AS uuid FROM users
     WHERE deleted_at IS NULL OR deleted_at >= :cutoff
)
"""
_LIVE_GROUPS = """
WITH live AS (
    SELECT uuid::text AS uuid FROM user_groups
     WHERE deleted_at IS NULL OR deleted_at >= :cutoff
)
"""
_LIVE_ROWS = """
WITH live AS (
    SELECT i.uuid::text AS uuid
      FROM table_records i
      JOIN tables l ON l.id = i.table_id
     WHERE l.uuid = CAST(:target AS uuid)
       AND (i.deleted_at IS NULL OR i.deleted_at >= :cutoff)
)
"""


async def clear_expired(db: AsyncSession, cutoff: datetime) -> int:
    """Clear every reference whose target is destroyed, or was deleted before
    ``cutoff``. Returns the number of records rewritten."""
    columns = (
        await db.execute(
            select(Column).where(
                Column.deleted_at.is_(None),
                Column.type.in_(PRINCIPAL_TYPES | {ColumnType.lookup}),
            )
        )
    ).scalars().all()

    touched = 0
    for column in columns:
        config = column.config or {}
        target = config.get("target_table_uuid")

        if column.type == ColumnType.user:
            live = _LIVE_USERS
        elif column.type == ColumnType.group:
            live = _LIVE_GROUPS
        elif target:
            live = _LIVE_ROWS
        else:
            continue  # a lookup with no target: nothing to resolve against

        params = {
            "table_id": column.table_id,
            "key": str(column.uuid),
            "cutoff": cutoff,
            "target": str(target) if target else None,
        }
        while True:
            ids = (
                await db.execute(
                    sa.text(live + _BATCH), {**params, "chunk": CHUNK}
                )
            ).scalars().all()
            if not ids:
                break

            result = await db.execute(
                sa.text(live + _CLEAR), {**params, "ids": list(ids)}
            )
            await db.commit()
            touched += result.rowcount

    await db.commit()
    return touched
