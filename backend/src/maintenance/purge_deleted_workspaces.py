"""Destroy workspaces that have been in the bin longer than the retention window.

Deleting a workspace only sets `deleted_at` — it stays on disk so it can be recovered.
This job is what eventually makes that permanent, after `PURGE_DELETED_AFTER_DAYS` (default 30).

    uv run python -m src.maintenance.purge_deleted_workspaces --dry-run
    uv run python -m src.maintenance.purge_deleted_workspaces
    uv run python -m src.maintenance.purge_deleted_workspaces --days 90

Chunked and committed per batch, so a purge of a million rows never holds a long
transaction; safe to kill and re-run.

It deletes each workspace's lists, columns, records, views, forms, metrics, and
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

# Rows destroyed per transaction — bounded so locks are held for milliseconds.
CHUNK = 5_000


async def _count(db: AsyncSession, cutoff: datetime) -> int:
    return await db.scalar(
        sa.text(
            "SELECT count(*) FROM workspaces "
            "WHERE deleted_at IS NOT NULL AND deleted_at < :cutoff"
        ),
        {"cutoff": cutoff},
    )


async def purge(days: int, dry_run: bool = False) -> int:
    """Destroy soft-deleted workspaces older than ``days``. Returns how many went."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    async with db_module.SessionLocal() as db:
        if dry_run:
            return await _count(db, cutoff)

        total = 0
        while True:
            workspace_ids = (
                await db.execute(
                    sa.text(
                        """
                        SELECT id FROM workspaces
                         WHERE deleted_at IS NOT NULL AND deleted_at < :cutoff
                         ORDER BY id
                         LIMIT :chunk
                        """
                    ),
                    {"cutoff": cutoff, "chunk": CHUNK},
                )
            ).scalars().all()
            if not workspace_ids:
                return total

            # Find all lists that belong to these workspaces
            list_ids = (
                await db.execute(
                    sa.text(
                        "SELECT id FROM lists WHERE workspace_id = ANY(:workspace_ids)"
                    ),
                    {"workspace_ids": list(workspace_ids)},
                )
            ).scalars().all()

            list_ids = list(list_ids)
            if list_ids:
                # Break the circular dependency first
                await db.execute(
                    sa.text(
                        "UPDATE lists SET primary_column_id = NULL WHERE id = ANY(:list_ids)"
                    ),
                    {"list_ids": list_ids},
                )

                # Grants for the lists and all their descendants
                await db.execute(
                    sa.text(
                        """
                        DELETE FROM permission_grants
                         WHERE (resource_type = 'list' AND resource_id = ANY(:list_ids))
                            OR (resource_type = 'item' AND resource_id IN (SELECT id FROM list_items WHERE list_id = ANY(:list_ids)))
                            OR (resource_type = 'view' AND resource_id IN (SELECT id FROM views WHERE list_id = ANY(:list_ids)))
                            OR (resource_type = 'form' AND resource_id IN (SELECT id FROM forms WHERE list_id = ANY(:list_ids)))
                        """
                    ),
                    {"list_ids": list_ids},
                )

                # Children records
                await db.execute(
                    sa.text("DELETE FROM list_items WHERE list_id = ANY(:list_ids)"),
                    {"list_ids": list_ids},
                )
                await db.execute(
                    sa.text("DELETE FROM views WHERE list_id = ANY(:list_ids)"),
                    {"list_ids": list_ids},
                )
                await db.execute(
                    sa.text("DELETE FROM forms WHERE list_id = ANY(:list_ids)"),
                    {"list_ids": list_ids},
                )
                # Metrics have no grants of their own — they are always their list's —
                # so unlike views and forms there is nothing above to clear first.
                await db.execute(
                    sa.text("DELETE FROM metrics WHERE list_id = ANY(:list_ids)"),
                    {"list_ids": list_ids},
                )
                await db.execute(
                    sa.text("DELETE FROM columns WHERE list_id = ANY(:list_ids)"),
                    {"list_ids": list_ids},
                )

                # The lists themselves
                await db.execute(
                    sa.text("DELETE FROM lists WHERE id = ANY(:list_ids)"),
                    {"list_ids": list_ids},
                )

            # Workspace-level permission grants
            await db.execute(
                sa.text(
                    "DELETE FROM permission_grants "
                    "WHERE resource_type = 'workspace' AND resource_id = ANY(:workspace_ids)"
                ),
                {"workspace_ids": list(workspace_ids)},
            )

            # Workspaces themselves
            result = await db.execute(
                sa.text("DELETE FROM workspaces WHERE id = ANY(:workspace_ids)"),
                {"workspace_ids": list(workspace_ids)},
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
        print(f"Would destroy {count:,} workspace(s) {window}.")
    else:
        print(f"Destroyed {count:,} workspace(s) {window}, and their lists, columns, records, views, forms, metrics, and permission grants.")


if __name__ == "__main__":
    asyncio.run(_main())
