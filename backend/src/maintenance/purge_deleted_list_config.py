"""Destroy a list's deleted views, forms, metrics and columns once past the window.

Deleting one of the four only sets `deleted_at`, so it can be recovered from the
list's bin (`/lists/{uuid}/views/deleted` and its three siblings,
src.lists.bin.services behind them). This job is what eventually makes that permanent,
after `PURGE_DELETED_AFTER_DAYS` (default 30). Until it existed nothing ever reclaimed
these rows: a view deleted on its own was unrecoverable *and* kept forever, which is
the worst of both.

    uv run python -m src.maintenance.purge_deleted_list_config --dry-run
    uv run python -m src.maintenance.purge_deleted_list_config
    uv run python -m src.maintenance.purge_deleted_list_config --days 90

Chunked and committed per batch, so a purge of a million rows never holds a long
transaction; safe to kill and re-run.

**A column takes its record data with it.** The other three own nothing; a column's
values live under its uuid in every record's `data`, and the delete never touched
them. Dropping the row alone would strand those values with nothing left to name
them, so each batch strips the keys first -- one set-based UPDATE per batch rather
than one per column, and grouped by list so a batch holding two columns of the same
list removes both keys rather than whichever the planner picked.

**Records are not here.** They have their own job (`purge_deleted_items`) because
they need their inbound references cleared.

**It only touches children of a *live* list.** A list delete stamps every child with
the list's own `deleted_at`, and those come due at the same moment the list does, so
either this job or `purge_deleted_lists` could destroy them correctly. Leaving them
entirely to the list purge is what keeps that true no matter how the two are run: a
`--days 0` here must not be able to strip the views off a list that is still sitting
restorable in its workspace's bin.

It clears the grants of the views and forms it destroys, for the same reason
`purge_deleted_items` does -- `permission_grants` has no foreign key to its resource,
so nothing cascades and a hard delete is exactly what turns an inert grant into an
orphan. Metrics and columns are never grant targets, so they have none to clear.
"""
import argparse
import asyncio
from datetime import datetime, timedelta, timezone
from typing import Optional

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

# Registers every model; without it the mappers half-configure (see model_registry).
import src.infrastructure.model_registry  # noqa: F401
from src.core.config import settings
from src.infrastructure import database as db_module

# Rows destroyed per transaction — bounded so locks are held for milliseconds.
CHUNK = 5_000

# Table, how `permission_grants.resource_type` names it (None when it is never a grant
# target), and whether its rows own data inside records. These are literals from this
# tuple and never user input, which is what makes the f-strings below safe.
_TABLES: tuple[tuple[str, Optional[str], bool], ...] = (
    ("views", "view", False),
    ("forms", "form", False),
    ("metrics", None, False),
    ("columns", None, True),
)

# Strips every doomed column's key from the records that hold it, for one batch.
# Grouped by list and removing an array of keys at once: `data - text[]` drops them
# all, where a plain join would leave a record matching two doomed columns with only
# one of them gone (UPDATE ... FROM picks a single join partner per target row).
# `jsonb_exists_any` rather than `data ?| keys` so no bare `?` reaches the driver.
_STRIP_COLUMN_KEYS = """
    UPDATE list_items i
       SET data = i.data - k.keys
      FROM (SELECT list_id, array_agg(uuid::text) AS keys
              FROM columns
             WHERE id = ANY(:ids)
             GROUP BY list_id) k
     WHERE i.list_id = k.list_id
       AND jsonb_exists_any(i.data, k.keys)
"""

# Soft-deleted, past the window, and hanging off a list that is still live. The
# `JOIN lists` is the whole of the "leave the list purge alone" rule above.
_DUE = """
      FROM {table} c
      JOIN lists l ON l.id = c.list_id
     WHERE c.deleted_at IS NOT NULL AND c.deleted_at < :cutoff
       AND l.deleted_at IS NULL
"""


async def _count(db: AsyncSession, cutoff: datetime) -> int:
    total = 0
    for table, _, _ in _TABLES:
        total += await db.scalar(
            sa.text("SELECT count(*) " + _DUE.format(table=table)),
            {"cutoff": cutoff},
        )
    return total


async def _purge_table(
    db: AsyncSession,
    table: str,
    resource_type: Optional[str],
    owns_record_data: bool,
    cutoff: datetime,
) -> int:
    total = 0
    while True:
        # Take a batch of ids first: the grants have to go before the rows they point
        # at, and both statements need the same set.
        ids = (
            await db.execute(
                sa.text(
                    "SELECT c.id "
                    + _DUE.format(table=table)
                    + " ORDER BY c.id LIMIT :chunk"
                ),
                {"cutoff": cutoff, "chunk": CHUNK},
            )
        ).scalars().all()
        if not ids:
            return total

        if resource_type is not None:
            await db.execute(
                sa.text(
                    "DELETE FROM permission_grants "
                    "WHERE resource_type = :rt AND resource_id = ANY(:ids)"
                ),
                {"rt": resource_type, "ids": list(ids)},
            )
        if owns_record_data:
            # Before the rows go, while their uuids are still resolvable from :ids.
            await db.execute(sa.text(_STRIP_COLUMN_KEYS), {"ids": list(ids)})
        result = await db.execute(
            sa.text(f"DELETE FROM {table} WHERE id = ANY(:ids)"),
            {"ids": list(ids)},
        )
        await db.commit()
        total += result.rowcount


async def purge(days: int, dry_run: bool = False) -> int:
    """Destroy soft-deleted views/forms/metrics/columns older than ``days``. Returns
    how many went, across all four."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    async with db_module.SessionLocal() as db:
        if dry_run:
            return await _count(db, cutoff)

        total = 0
        for table, resource_type, owns_record_data in _TABLES:
            total += await _purge_table(
                db, table, resource_type, owns_record_data, cutoff
            )
        return total


async def _main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--days",
        type=int,
        default=settings.PURGE_DELETED_AFTER_DAYS,
        help="retention window; older soft-deleted rows are destroyed "
        f"(default {settings.PURGE_DELETED_AFTER_DAYS}, from PURGE_DELETED_AFTER_DAYS)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="report what would be destroyed without touching anything",
    )
    args = parser.parse_args()

    if args.days < 0:
        parser.error("--days cannot be negative")

    count = await purge(args.days, dry_run=args.dry_run)
    window = f"deleted more than {args.days} day(s) ago"
    kinds = "view(s)/form(s)/metric(s)/column(s)"
    if args.dry_run:
        print(f"Would destroy {count:,} {kinds} {window}.")
    else:
        print(
            f"Destroyed {count:,} {kinds} {window}, their permission grants, "
            "and the record values the columns held."
        )


if __name__ == "__main__":
    asyncio.run(_main())
