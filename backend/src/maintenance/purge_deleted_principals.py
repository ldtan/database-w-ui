"""Finalize users and groups that have been deleted longer than the window.

Deleting an account leaves every `user` cell naming it intact, so the delete can be
undone and the cell can say "(deleted)" rather than pretend the person never touched
the record. This job is what makes it permanent, after `PURGE_DELETED_AFTER_DAYS`:

    uv run python -m src.maintenance.purge_deleted_principals --dry-run
    uv run python -m src.maintenance.purge_deleted_principals

It clears their references and deletes their permission grants. **It does not delete
the rows.** A user owns workspaces (`workspaces.owner_id` is a real foreign key), so
destroying the row means answering "who owns their data now?" — a question with no
safe default and no UI to ask it. The soft-deleted row stays: it is inert, it costs a
few bytes, and it keeps every foreign key honest. Purging accounts properly needs an
ownership-transfer story first.
"""
import argparse
import asyncio
from datetime import datetime, timedelta, timezone

import sqlalchemy as sa

# Registers every model; without it the mappers half-configure (see model_registry).
import src.infrastructure.model_registry  # noqa: F401
from src.core.config import settings
from src.infrastructure import database as db_module
from src.maintenance._references import clear_expired

_EXPIRED = """
SELECT count(*) FROM {table}
 WHERE deleted_at IS NOT NULL AND deleted_at < :cutoff
"""
# A grant belonging to an expired principal. Nothing else removes these: grants point
# at their resource with no foreign key, and at their principal with one — but nothing
# cascades, so they simply accumulate.
_DROP_GRANTS = """
DELETE FROM permission_grants
 WHERE {column} IN (
     SELECT id FROM {table}
      WHERE deleted_at IS NOT NULL AND deleted_at < :cutoff
 )
"""


async def purge_principals(days: int, dry_run: bool = False) -> dict[str, int]:
    """Clear expired accounts' references and grants. Returns what it found."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    found: dict[str, int] = {}

    async with db_module.SessionLocal() as db:
        for label, table in (("users", "users"), ("groups", "user_groups")):
            count = await db.scalar(
                sa.text(_EXPIRED.format(table=table)), {"cutoff": cutoff}
            )
            if count:
                found[label] = count

        if dry_run:
            return found

        if found:
            for column, table in (("user_id", "users"), ("group_id", "user_groups")):
                await db.execute(
                    sa.text(_DROP_GRANTS.format(column=column, table=table)),
                    {"cutoff": cutoff},
                )
            await db.commit()

        # Cells naming them are cleared here, not when the account was deleted.
        cleared = await clear_expired(db, cutoff)
        if cleared:
            found["references cleared"] = cleared

    return found


async def _main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=settings.PURGE_DELETED_AFTER_DAYS)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.days < 0:
        parser.error("--days cannot be negative")

    found = await purge_principals(args.days, dry_run=args.dry_run)
    if not found:
        print(f"No accounts deleted more than {args.days} day(s) ago.")
        return
    verb = "Would finalize" if args.dry_run else "Finalized"
    for what, n in found.items():
        print(f"{verb}: {n:,} {what}")


if __name__ == "__main__":
    asyncio.run(_main())
