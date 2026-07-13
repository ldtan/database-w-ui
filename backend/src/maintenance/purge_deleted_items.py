"""Destroy records that have been in the bin longer than the retention window.

Deleting a record only sets `deleted_at` — it stays on disk so it can be recovered
and so nothing that pointed at it breaks. This job is what eventually makes that
permanent, after `PURGE_DELETED_AFTER_DAYS` (default 30).

    uv run python -m src.maintenance.purge_deleted_items --dry-run
    uv run python -m src.maintenance.purge_deleted_items
    uv run python -m src.maintenance.purge_deleted_items --days 90

Chunked and committed per batch, so a purge of a million rows never holds a long
transaction; safe to kill and re-run.

**It deletes each record's permission grants too, and that is not a nicety.**
`permission_grants` points at its resource polymorphically — `(resource_type,
resource_id)` with **no foreign key** — so nothing cleans up a grant when its record
goes away. While the record is only soft-deleted that is harmless: the resolver
walks the live hierarchy, so a deleted record resolves to no context and its grants
are inert. Hard-deleting the row is what turns them into real orphans, pointing at an
id that no longer exists. This is the "grants are not scrubbed on hard delete" gap in
docs/known-gaps.md, and this job is the first thing in the codebase that hard-deletes
anything — so it is the first thing that has to close it.

What it does *not* need to do is scrub references. A `lookup`/`user`/`group` cell
pointing at this record was already cleared when the record was soft-deleted (see
`ListItemService._drop_reference`), so by the time it gets here nothing points at it.
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

# Rows destroyed per transaction — bounded so locks are held for milliseconds.
CHUNK = 5_000


async def _count(db: AsyncSession, cutoff: datetime) -> int:
    return await db.scalar(
        sa.text(
            "SELECT count(*) FROM list_items "
            "WHERE deleted_at IS NOT NULL AND deleted_at < :cutoff"
        ),
        {"cutoff": cutoff},
    )


async def purge(days: int, dry_run: bool = False) -> int:
    """Destroy soft-deleted records older than ``days``. Returns how many went."""
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
                        SELECT id FROM list_items
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

            # Grants first. Nothing else ever removes these, and once the record is
            # gone they point at an id that does not exist.
            await db.execute(
                sa.text(
                    "DELETE FROM permission_grants "
                    "WHERE resource_type = 'item' AND resource_id = ANY(:ids)"
                ),
                {"ids": list(ids)},
            )
            result = await db.execute(
                sa.text("DELETE FROM list_items WHERE id = ANY(:ids)"),
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
        print(f"Would destroy {count:,} record(s) {window}.")
    else:
        print(f"Destroyed {count:,} record(s) {window}, and their permission grants.")


if __name__ == "__main__":
    asyncio.run(_main())
