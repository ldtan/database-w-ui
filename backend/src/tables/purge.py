"""The two steps of a purge that are sized by the records, a batch at a time.

Everything else a purge touches is bounded by the schema: a table has a handful of
views, forms and metrics and a few dozen columns. Two things are bounded only by how
much data somebody put in, and each used to be a single statement over all of it:

- ``drain_records`` -- destroying a table means emptying `table_records` first.
- ``strip_column_keys`` -- destroying a *column* means removing its key from every
  record that holds one, since the records themselves survive.

Between them they have six callers: the `purge_deleted_tables`,
`purge_deleted_workspaces` and `purge_deleted_table_config` jobs, and the `permanent`
routes behind `TableService.purge`, `WorkspaceService.purge` and the column bin's
`purge`. The rest of each cascade is still written out at its own call site; only the
parts worth bounding are here.

**Each record batch clears its own record grants.** `permission_grants` names its
resource polymorphically with no foreign key, so nothing cascades and the grants of a
destroyed record become orphans pointing at an id that will be handed out again. They
cannot be cleared up-front in one statement either: the subquery that finds them
(`resource_id IN (SELECT id FROM table_records ...)`) stops matching the moment the first
batch is gone. So the grants for a batch go immediately before the batch does, which
is the same order `purge_deleted_records` uses for the same reason.

**Whether to commit per batch is the caller's to make, and the two answers are not
interchangeable.**

A job works on rows already past the retention window, and the bins only hand back
something whose ``deleted_at`` is still *inside* it -- ``TableService.get_deleted_by_uuid``
and ``bin.services._select_deleted`` both say so. Half-finished work there is not
restorable-and-wrong, it is unreachable, and the next run picks it up where this one
stopped. That is what the jobs' "safe to kill and re-run" has always meant.

The `permanent` routes work strictly inside the window, where restore still succeeds.
Committing there would let an interrupted purge leave a table that comes back short of
records, or a column that comes back short of values, with nothing to say so -- worse
than the long transaction it avoids. They pass ``commit=False`` and keep the cascade
atomic: bounded statements, but not bounded locks. That costs them less than it sounds,
since the records of a soft-deleted table, and the hidden values of a soft-deleted
column, are not reachable by any reader to contend with.

**Neither loop uses `FOR UPDATE SKIP LOCKED`**, though `reconcile_shapes` does over the
same table. That job converges and can run again, so passing over a locked row costs
nothing. These two get one chance: the row they were about to clean is being destroyed
in the same breath, and a skipped record would keep values no column will ever name
again. Both re-select instead, and stop when the select comes back empty.
"""
from typing import Sequence

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

# Records destroyed per statement, so no single DELETE is sized by the table. Matches
# the chunk the purge jobs use for their own top-level batches.
CHUNK = 5_000

_BATCH = """
    SELECT id FROM table_records
     WHERE table_id = ANY(:table_ids)
     ORDER BY id
     LIMIT :chunk
"""

_GRANTS = """
    DELETE FROM permission_grants
     WHERE resource_type = 'record' AND resource_id = ANY(:ids)
"""

_RECORDS = "DELETE FROM table_records WHERE id = ANY(:ids)"


async def drain_records(
    db: AsyncSession, table_ids: Sequence[int], *, commit: bool
) -> int:
    """Destroy every record of ``table_ids`` in batches. Returns how many went.

    Takes live and soft-deleted records alike: the caller is destroying the tables
    themselves, so there is nothing left for a record to belong to either way.

    ``commit=True`` ends each batch's transaction -- for callers whose work is already
    unreachable and resumable. ``commit=False`` leaves every batch in the caller's
    transaction, for callers that must stay all-or-nothing. See the module docstring
    for why that is not a preference.

    Re-selecting rather than paging by offset is what makes the loop terminate under
    both: within one transaction each statement sees the deletes the previous ones
    did, so the next batch is always the next 5,000 still standing.
    """
    if not table_ids:
        return 0

    targets = list(table_ids)
    total = 0
    while True:
        ids = (
            await db.execute(
                sa.text(_BATCH), {"table_ids": targets, "chunk": CHUNK}
            )
        ).scalars().all()
        if not ids:
            return total

        batch = list(ids)
        await db.execute(sa.text(_GRANTS), {"ids": batch})
        result = await db.execute(sa.text(_RECORDS), {"ids": batch})
        if commit:
            await db.commit()
        total += result.rowcount


# The records of one table still holding any of the given keys. `jsonb_exists_any`
# rather than `data ?| keys` so no bare `?` reaches the driver, and the casts because
# `data - $1` is ambiguous between the text and text[] operators until the parameter
# has a type.
_KEY_BATCH = """
    SELECT id FROM table_records
     WHERE table_id = :table_id
       AND jsonb_exists_any(data, CAST(:keys AS text[]))
     ORDER BY id
     LIMIT :chunk
"""

_STRIP = """
    UPDATE table_records
       SET data = data - CAST(:keys AS text[])
     WHERE id = ANY(:ids)
"""


async def strip_column_keys(
    db: AsyncSession, table_id: int, keys: Sequence[str], *, commit: bool
) -> int:
    """Remove ``keys`` from every record of one table. Returns how many were rewritten.

    The counterpart to ``drain_records`` for the case where the records *stay*. A
    column's values live under its uuid in each record's `data` and deleting the
    column never touched them -- `shape_values` stops serving them, which is what makes
    them hidden, but nothing removes them. Only a purge does, and it has to happen
    while the uuid is still known, since once the column row is gone nothing names
    those keys again.

    Takes an array of keys rather than one, because a job's batch can hold several
    doomed columns of the same table. Subtracting an array is also what makes that
    correct: the join it replaced (`UPDATE ... FROM columns`) picks a single partner
    per target row, so a record matching two doomed columns kept whichever the planner
    happened to pick.

    Rewriting a row is heavier than deleting one -- the whole `data` blob is written
    again -- so this is batched for the same reason and by the same rule as
    ``drain_records``; see the module docstring for ``commit``.
    """
    if not keys:
        return 0

    params = {"table_id": table_id, "keys": list(keys)}
    total = 0
    while True:
        ids = (
            await db.execute(sa.text(_KEY_BATCH), {**params, "chunk": CHUNK})
        ).scalars().all()
        if not ids:
            return total

        result = await db.execute(
            sa.text(_STRIP), {"keys": params["keys"], "ids": list(ids)}
        )
        if commit:
            await db.commit()
        total += result.rowcount
