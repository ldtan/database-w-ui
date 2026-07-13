"""Clearing references to things that are gone for good.

This runs at **purge** time, not at delete time. Soft-deleting a record, a user or a
group deliberately leaves the cells pointing at it intact — that is what makes the
delete reversible, and what lets the cell render as "(deleted)" rather than pretend
the relationship never existed. Only once the target is destroyed, or has sat in the
bin past the retention window, is the pointer worth nothing and cleared.

Set-based on purpose: one UPDATE per referencing column, however many records or
targets are involved. The obvious alternative — scrub per purged row — is one UPDATE
per column *per row*, which turns purging a million records into millions of
statements.
"""
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.lists.columns.enums import PRINCIPAL_TYPES, ColumnType
from src.lists.columns.models import Column

# `live` is the set of uuids still worth pointing at. Everything else is dropped from
# the cell: nulled if the column holds one value, filtered out of the array if it
# holds several. Shape-agnostic, like the scrub it replaces — the CASE rewrites each
# row according to the shape it actually has, never according to what the column's
# `multiple` currently claims.
_CLEAR = """
UPDATE list_items
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
 WHERE list_id = :list_id
   AND (
         (jsonb_typeof(data -> :key) = 'array'
          AND EXISTS (SELECT 1
                        FROM jsonb_array_elements(data -> :key) AS elem
                       WHERE elem #>> '{}' NOT IN (SELECT uuid FROM live)))
      OR (jsonb_typeof(data -> :key) = 'string'
          AND (data ->> :key) NOT IN (SELECT uuid FROM live))
       )
"""

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
      FROM list_items i
      JOIN lists l ON l.id = i.list_id
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
        target = config.get("target_list_uuid")

        if column.type == ColumnType.user:
            live = _LIVE_USERS
        elif column.type == ColumnType.group:
            live = _LIVE_GROUPS
        elif target:
            live = _LIVE_ROWS
        else:
            continue  # a lookup with no target: nothing to resolve against

        result = await db.execute(
            sa.text(live + _CLEAR),
            {
                "list_id": column.list_id,
                "key": str(column.uuid),
                "cutoff": cutoff,
                "target": str(target) if target else None,
            },
        )
        touched += result.rowcount

    await db.commit()
    return touched
