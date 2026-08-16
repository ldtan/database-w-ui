"""Rebuild the stored index behind every list's calculated columns.

A calculated cell is written whenever its record is written, so ordinary editing keeps
the index current for free. A **schema** change does not: editing a formula, or renaming,
deleting or restoring a column some formula reads, changes what every record's calculated
cells should say without touching a record. The app offers a re-index for exactly that
moment (`POST /lists/{uuid}/columns/recompute`), and this is the backstop for the times
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

A list already being re-indexed by the app is **skipped rather than waited for**: both
take the same per-list advisory lock, and duplicating a scan that is already running is
the one expensive mistake available here.
"""
import argparse
import asyncio

from sqlalchemy import distinct, select

# Registers every model. An entry point that imports only the models it names leaves
# the mappers half-configured — `Column.list` cannot resolve `List` — so this import
# is load-bearing, exactly as it is for main.py, the tests and Alembic's env.py.
import src.infrastructure.model_registry  # noqa: F401
from src.infrastructure import database as db_module
from src.lists.columns.backfill import recompute_records, try_lock, unlock
from src.lists.columns.enums import COMPUTED_TYPES
from src.lists.columns.models import Column, in_shape


async def _lists_with_formulas(db) -> list[int]:
    """Every list holding a live calculated column.

    `in_shape`, matching the job itself: a hidden column is not a field of the list,
    so it neither feeds a formula nor gets one.
    """
    result = await db.execute(
        select(distinct(Column.list_id)).where(
            Column.type.in_(COMPUTED_TYPES), in_shape()
        )
    )
    return sorted(result.scalars().all())


async def recompute(dry_run: bool = False) -> dict[int, int]:
    """Bring every list's calculated index in line with its formulas.

    Returns {list_id: records changed}, omitting the lists that needed nothing. A list
    whose lock is held is omitted too — it is being done by whoever holds it.
    """
    changed: dict[int, int] = {}
    async with db_module.SessionLocal() as db:
        for list_id in await _lists_with_formulas(db):
            if dry_run:
                rows = await recompute_records(db, list_id, dry_run=True)
            elif not await try_lock(db, list_id):
                continue
            else:
                try:
                    rows = await recompute_records(db, list_id)
                finally:
                    await unlock(db, list_id)
            if rows:
                changed[list_id] = rows
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
    for list_id, rows in changed.items():
        print(f"{verb} {rows:>9,} record(s) — list_id {list_id}")
    print(f"{verb} {sum(changed.values()):,} record(s) in total.")


if __name__ == "__main__":
    asyncio.run(_main())
