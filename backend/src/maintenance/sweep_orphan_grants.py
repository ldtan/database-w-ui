"""Delete permission grants that can never grant anything again.

    uv run python -m src.maintenance.sweep_orphan_grants --dry-run
    uv run python -m src.maintenance.sweep_orphan_grants

`permission_grants` points at its resource polymorphically — `(resource_type,
resource_id)` with **no foreign key**, because `resource_id` means a different table
depending on the type. So nothing cascades, and grants outlive the things they were
about. They are inert (the resolver walks the live hierarchy, so a grant on a deleted
resource resolves to nothing) but they accumulate, and they make "who has access to
this?" harder to answer honestly.

A grant is swept when it can never apply again:

- its **resource is gone** — the row no longer exists at all, which is what a purge
  leaves behind; or
- its **resource has been in the bin past the retention window**, so it is on its way
  out and will not be restored; or
- its **principal** — the user or group it was granted to — is in the same position.

A grant on something still *inside* the window is left alone: that resource can still
be restored, and its sharing should come back with it. This is the same rule the
purges follow, and the reason `purge_deleted_records` deletes a record's grants itself
rather than leaving them to this job — by the time it has destroyed the row, the
grant's resource is unidentifiable.

Kept separate from the purges on purpose: destroying records is not the same decision
as tidying config that already has no effect, and they deserve their own cadence.

Chunked and committed per batch like the purges, and for the same reason twice over: a
grant is created per shared record, so a busy table's worth of them is not a small
number, and the predicate that finds them is a NOT EXISTS against five tables. Nothing
here is ever half-wrong — every grant this deletes was already inert.
"""
import argparse
import asyncio
from datetime import datetime, timedelta, timezone

import sqlalchemy as sa

# Registers every model; without it the mappers half-configure (see model_registry).
import src.infrastructure.model_registry  # noqa: F401
from src.core.config import settings
from src.infrastructure import database as db_module

# Grants deleted per transaction — bounded so locks are held for milliseconds.
CHUNK = 5_000

# The table behind each resource_type. The whole point of the polymorphic pointer is
# that the database cannot know this — so it is written down here, once.
_RESOURCE_TABLES = {
    "workspace": "workspaces",
    "table": "tables",
    "view": "views",
    "form": "forms",
    "record": "table_records",
}

# A grant is dead when its resource, or its principal, is gone or expired. "Gone"
# means the row is absent entirely; "expired" means soft-deleted before the cutoff
# (for resources/principals) or a grant timestamp already in the past.
_DEAD_RESOURCE = """
    resource_type = '{rt}'
AND NOT EXISTS (
        SELECT 1 FROM {table} r
         WHERE r.id = permission_grants.resource_id
           AND (r.deleted_at IS NULL OR r.deleted_at >= :cutoff)
    )
"""
_DEAD_PRINCIPAL = """
   (user_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM users u
         WHERE u.id = permission_grants.user_id
           AND (u.deleted_at IS NULL OR u.deleted_at >= :cutoff)))
OR (group_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM user_groups g
         WHERE g.id = permission_grants.group_id
           AND (g.deleted_at IS NULL OR g.deleted_at >= :cutoff)))
"""
_DEAD_EXPIRY = """
    expires_at IS NOT NULL
AND expires_at <= :now
"""


def _predicate() -> str:
    dead_resources = " OR ".join(
        f"({_DEAD_RESOURCE.format(rt=rt, table=table)})"
        for rt, table in _RESOURCE_TABLES.items()
    )
    return f"(({dead_resources}) OR ({_DEAD_PRINCIPAL}) OR ({_DEAD_EXPIRY}))"


async def sweep(days: int, dry_run: bool = False) -> int:
    """Delete grants whose resource/principal is gone or whose expiry has passed."""
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=days)
    where = _predicate()
    params = {"cutoff": cutoff, "now": now}

    async with db_module.SessionLocal() as db:
        if dry_run:
            return await db.scalar(
                sa.text(f"SELECT count(*) FROM permission_grants WHERE {where}"),
                params,
            )
        # Chunked, like the purges: the predicate is a NOT EXISTS against five tables,
        # so on a backlog this is both a lot of rows and an expensive row. Deleting the
        # batch is what makes the next pass find the next one, and eventually none.
        total = 0
        while True:
            ids = (
                await db.execute(
                    sa.text(
                        f"SELECT id FROM permission_grants WHERE {where} "
                        "ORDER BY id LIMIT :chunk"
                    ),
                    {**params, "chunk": CHUNK},
                )
            ).scalars().all()
            if not ids:
                return total

            result = await db.execute(
                sa.text("DELETE FROM permission_grants WHERE id = ANY(:ids)"),
                {"ids": list(ids)},
            )
            await db.commit()
            total += result.rowcount


async def _main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=settings.PURGE_DELETED_AFTER_DAYS)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.days < 0:
        parser.error("--days cannot be negative")

    count = await sweep(args.days, dry_run=args.dry_run)
    verb = "Would delete" if args.dry_run else "Deleted"
    print(f"{verb} {count:,} grant(s) whose resource or principal is gone.")


if __name__ == "__main__":
    asyncio.run(_main())
