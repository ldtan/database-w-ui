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
them, so each batch strips the keys first (`strip_column_keys`, in src.lists.purge,
shared with the column bin's own permanent delete).

That step is chunked separately, and the batch of columns above does not bound it: one
column's values are spread over as many records as its list has, so 5,000 columns is a
small batch right up until one of their lists holds a million rows. It is grouped by
list and subtracts an array of keys, which is also what makes it correct -- a record
matching two doomed columns of the same list has to lose both, where the join this
replaced would pick a single partner per row and leave one behind.

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
from src.lists.purge import strip_column_keys

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

# The doomed columns of one batch, as {list_id: [uuid, ...]}. The grouping used to
# live in the UPDATE itself; it is here now because `strip_column_keys` works a list at
# a time, which is what lets it batch the records it rewrites.
_BATCH_KEYS = "SELECT list_id, uuid::text FROM columns WHERE id = ANY(:ids)"

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
            # One pass per list, each batching the records it rewrites: a single
            # column's values can be spread over as many records as the list has.
            # It commits as it goes, which is only tidy because the one kind that
            # reaches here is never a grant target — nothing above has written yet.
            keys_by_list: dict[int, list[str]] = {}
            for list_id, key in (
                await db.execute(sa.text(_BATCH_KEYS), {"ids": list(ids)})
            ).all():
                keys_by_list.setdefault(list_id, []).append(key)
            for list_id, keys in keys_by_list.items():
                await strip_column_keys(db, list_id, keys, commit=True)
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
