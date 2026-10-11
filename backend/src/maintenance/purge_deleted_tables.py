"""Destroy tables that have been in the bin longer than the retention window.

Deleting a table only sets `deleted_at` — it stays on disk so it can be recovered
and so nothing that pointed at it breaks. This job is what eventually makes that
permanent, after `PURGE_DELETED_AFTER_DAYS` (default 30).

    uv run python -m src.maintenance.purge_deleted_tables --dry-run
    uv run python -m src.maintenance.purge_deleted_tables
    uv run python -m src.maintenance.purge_deleted_tables --days 90

Chunked and committed per batch, so a purge of a million rows never holds a long
transaction; safe to kill and re-run.

Chunked twice over, in fact: a batch of tables, and within it a batch of records. A
table is the only thing here that carries an unbounded number of anything, so batching
by table alone still left one `DELETE FROM table_records` sized by the largest table in the
batch. That inner loop lives in src.tables.purge, shared with the three other cascades
that destroy a table.

It deletes each table's columns, records (table_records), views, forms, metrics, and
their permission grants too.
"""
import argparse
import asyncio
from datetime import datetime, timedelta, timezone

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

# Registers every model; without it the mappers half-configure (see model_registry).
import src.infrastructure.model_registry  # noqa: F401
from src.core.config import settings
from src.infrastructure import database as db_module
from src.tables.purge import drain_records

# Tables destroyed per transaction — bounded so locks are held for milliseconds. Their
# records are bounded separately, by `drain_records`: 5,000 tables is a small batch
# only until one of them holds a million rows.
CHUNK = 5_000


async def _count(db: AsyncSession, cutoff: datetime) -> int:
    return await db.scalar(
        sa.text(
            "SELECT count(*) FROM tables "
            "WHERE deleted_at IS NOT NULL AND deleted_at < :cutoff"
        ),
        {"cutoff": cutoff},
    )


async def purge(days: int, dry_run: bool = False) -> int:
    """Destroy soft-deleted tables older than ``days``. Returns how many went."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    async with db_module.SessionLocal() as db:
        if dry_run:
            return await _count(db, cutoff)

        total = 0
        while True:
            # Take a batch of ids first: the grants have to go before the records
            # they point at, and both statements need the same set.
            ids = (
                await db.execute(
                    sa.text(
                        """
                        SELECT id FROM tables
                         WHERE deleted_at IS NOT NULL AND deleted_at < :cutoff
                         ORDER BY id
                         LIMIT :chunk
                        """
                    ),
                    {"cutoff": cutoff, "chunk": CHUNK},
                )
            ).scalars().all()
            if not ids:
                return total

            # Records first, and in batches of their own with their own grants — the
            # only part of the cascade a single table can make arbitrarily large. It
            # commits as it goes, so this runs before anything else in the batch has
            # written; see src.tables.purge for why committing here is safe.
            await drain_records(db, ids, commit=True)

            # Break the circular dependency first by nullifying tables' primary_column_id
            await db.execute(
                sa.text(
                    "UPDATE tables SET primary_column_id = NULL WHERE id = ANY(:ids)"
                ),
                {"ids": list(ids)},
            )

            # Grants for the tables and their views and forms. The records' went with
            # them above, which is the only place they can go: once a batch of records
            # is deleted nothing is left to find their grants by.
            await db.execute(
                sa.text(
                    """
                    DELETE FROM permission_grants
                     WHERE (resource_type = 'table' AND resource_id = ANY(:ids))
                        OR (resource_type = 'view' AND resource_id IN (SELECT id FROM views WHERE table_id = ANY(:ids)))
                        OR (resource_type = 'form' AND resource_id IN (SELECT id FROM forms WHERE table_id = ANY(:ids)))
                    """
                ),
                {"ids": list(ids)},
            )

            # The remaining children
            await db.execute(
                sa.text("DELETE FROM views WHERE table_id = ANY(:ids)"),
                {"ids": list(ids)},
            )
            await db.execute(
                sa.text("DELETE FROM forms WHERE table_id = ANY(:ids)"),
                {"ids": list(ids)},
            )
            # Metrics have no grants of their own — they are always their table's —
            # so unlike views and forms there is nothing above to clear first.
            await db.execute(
                sa.text("DELETE FROM metrics WHERE table_id = ANY(:ids)"),
                {"ids": list(ids)},
            )
            await db.execute(
                sa.text("DELETE FROM columns WHERE table_id = ANY(:ids)"),
                {"ids": list(ids)},
            )

            # The tables themselves
            result = await db.execute(
                sa.text("DELETE FROM tables WHERE id = ANY(:ids)"),
                {"ids": list(ids)},
            )
            await db.commit()
            total += result.rowcount


async def _main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--days",
        type=int,
        default=settings.PURGE_DELETED_AFTER_DAYS,
        help="retention window; older soft-deleted records are destroyed "
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
    if args.dry_run:
        print(f"Would destroy {count:,} list(s) {window}.")
    else:
        print(f"Destroyed {count:,} list(s) {window}, and their columns, records, views, forms, metrics, and permission grants.")


if __name__ == "__main__":
    asyncio.run(_main())
