"""Rebuild the stored index behind every table's calculated columns.

A calculated cell is written whenever its record is written, so ordinary editing keeps
the index current for free. A **schema** change does not: editing a formula, or renaming,
deleting or restoring a column some formula reads, changes what every record's calculated
cells should say without touching a record. The app offers a re-index for exactly that
moment (`POST /tables/{uuid}/columns/recompute`), and this is the backstop for the times
nobody pressed it — or a deferred run was truncated by a redeploy or a free-tier
spin-down.

**Nothing depends on it having run.** Reads recompute calculated cells and overwrite the
stored copy (`shape_values`), so the grid, a record dialog and the export are correct
whether or not this has run. What a stale index costs is that `apply_filters`,
`apply_sorts` and `_apply_search` — which run inside Postgres, over the stored bytes —
answer for the formula as it was rather than as it is. A row sorts to the wrong place; it
never shows a wrong number. That is what makes a nightly sweep an adequate backstop
rather than a correctness requirement.

Run it from cron / a systemd timer:

    uv run python -m src.maintenance.recompute_calculated
    uv run python -m src.maintenance.recompute_calculated --dry-run

Safe to run repeatedly (idempotent — a record whose index already agrees is left alone,
not rewritten with identical bytes), and safe to kill (each chunk commits on its own, and
the next run picks up whatever was missed).

A table already being re-indexed by the app is **skipped rather than waited for**: both
take the same per-table advisory lock, and duplicating a scan that is already running is
the one expensive mistake available here.
"""
import argparse
import asyncio

from sqlalchemy import distinct, select

# Registers every model. An entry point that imports only the models it names leaves
# the mappers half-configured — `Column.table` cannot resolve `Table` — so this import
# is load-bearing, exactly as it is for main.py, the tests and Alembic's env.py.
import src.infrastructure.model_registry  # noqa: F401
from src.infrastructure import database as db_module
from src.tables.columns.backfill import recompute_records, try_lock, unlock
from src.tables.columns.enums import COMPUTED_TYPES
from src.tables.columns.models import Column, in_shape


async def _tables_with_formulas(db) -> list[int]:
    """Every table holding a live calculated column.

    `in_shape`, matching the job itself: a hidden column is not a field of the table,
    so it neither feeds a formula nor gets one.
    """
    result = await db.execute(
        select(distinct(Column.table_id)).where(
            Column.type.in_(COMPUTED_TYPES), in_shape()
        )
    )
    return sorted(result.scalars().all())


async def recompute(dry_run: bool = False) -> dict[int, int]:
    """Bring every table's calculated index in line with its formulas.

    Returns {table_id: records changed}, omitting the tables that needed nothing. A table
    whose lock is held is omitted too — it is being done by whoever holds it.
    """
    changed: dict[int, int] = {}
    async with db_module.SessionLocal() as db:
        for table_id in await _tables_with_formulas(db):
            if dry_run:
                rows = await recompute_records(db, table_id, dry_run=True)
            elif not await try_lock(db, table_id):
                continue
            else:
                try:
                    rows = await recompute_records(db, table_id)
                finally:
                    await unlock(db, table_id)
            if rows:
                changed[table_id] = rows
    return changed


async def _main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="report which records are out of date without changing anything",
    )
    args = parser.parse_args()

    changed = await recompute(dry_run=args.dry_run)
    verb = "would rebuild" if args.dry_run else "rebuilt"
    if not changed:
        print("Every calculated column's index already matches its formulas.")
        return
    for table_id, rows in changed.items():
        print(f"{verb} {rows:>9,} record(s) — table_id {table_id}")
    print(f"{verb} {sum(changed.values()):,} record(s) in total.")


if __name__ == "__main__":
    asyncio.run(_main())
