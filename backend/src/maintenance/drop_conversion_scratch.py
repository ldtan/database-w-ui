"""Destroy the hidden scratch columns the old staged conversion left behind.

A one-off, not a recurring job. Column conversion used to work in two calls: `start`
built a **hidden** target column and backfilled it, and `commit` swapped its values
in. Anything in between — a person who closed the dialog without pressing anything, a
backfill that raised after the column was already committed, a browser that went away
— left that column sitting in the database with its values in every record, invisible
to every reader because `Column.in_shape()` excludes `hidden` rows.

The conversion is one atomic call now (`ConversionService.apply`) and nothing ever
sets `hidden` again, so those rows are unreachable: no code path will ever read them,
finish them, or offer to clean them up. This removes them, and the record keys they
own with them.

    uv run python -m src.maintenance.drop_conversion_scratch --dry-run
    uv run python -m src.maintenance.drop_conversion_scratch

Run it once after deploying the atomic conversion. It is idempotent and safe to run
again — on a database with nothing to clean it reports zero and writes nothing —
which is the only reason it is safe to leave in the maintenance workflow rather than
having to be remembered.

**It is deliberately not driven by `_conversion_of`.** That bookkeeping key marked a
scratch column as belonging to a conversion, and it is gone from the code with the
rest of the staged design. Matching on `hidden` instead catches every stranded row
whether or not it carried the key, and cannot miss one written by a version that
spelled the bookkeeping differently. Nothing else in the schema has ever set `hidden`,
so the predicate is exact rather than merely broad.

**The values go first.** A hidden column's values live under its uuid in every record
of its table, exactly like a visible one's, and dropping the row alone would strand
them with nothing left to name them. `strip_column_keys` is the same helper the column
bin's permanent delete and `purge_deleted_table_config` use, chunked a table at a time.
"""
import argparse
import asyncio

import sqlalchemy as sa

# Registers every model; without it the mappers half-configure (see model_registry).
import src.infrastructure.model_registry  # noqa: F401
from src.infrastructure import database as db_module
from src.tables.purge import strip_column_keys

# Rows destroyed per transaction — bounded so locks are held for milliseconds.
CHUNK = 5_000

# Every hidden column, live or binned. `deleted_at` is not in the predicate on
# purpose: a scratch column that was somehow soft-deleted is just as unreachable and
# just as pointless to keep, and leaving it to the ordinary column purge would mean
# waiting out a retention window for a row nobody can restore into anything useful.
_DUE = "FROM columns WHERE hidden IS TRUE"

# The doomed columns of one batch, as {table_id: [uuid, ...]} — the shape
# `strip_column_keys` takes, so it can batch the records it rewrites per table.
_BATCH_KEYS = "SELECT table_id, uuid::text FROM columns WHERE id = ANY(:ids)"


async def drop_scratch(dry_run: bool = False) -> int:
    """Destroy every hidden column and the record values it owns. Returns the count."""
    async with db_module.SessionLocal() as db:
        if dry_run:
            return await db.scalar(sa.text("SELECT count(*) " + _DUE)) or 0

        destroyed = 0
        while True:
            ids = (
                await db.execute(
                    sa.text("SELECT id " + _DUE + " ORDER BY id LIMIT :chunk"),
                    {"chunk": CHUNK},
                )
            ).scalars().all()
            if not ids:
                break

            by_table: dict[int, list[str]] = {}
            for table_id, uuid in await db.execute(
                sa.text(_BATCH_KEYS), {"ids": list(ids)}
            ):
                by_table.setdefault(table_id, []).append(uuid)
            for table_id, keys in by_table.items():
                # `commit=False` — the keys and the column rows go in one transaction,
                # so a killed run never leaves a column whose values are already gone.
                await strip_column_keys(db, table_id, keys, commit=False)

            await db.execute(
                sa.text("DELETE FROM columns WHERE id = ANY(:ids)"), {"ids": list(ids)}
            )
            await db.commit()
            destroyed += len(ids)
        return destroyed


async def _main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="report what would be destroyed without touching anything",
    )
    args = parser.parse_args()

    count = await drop_scratch(dry_run=args.dry_run)
    what = "hidden scratch column(s) left by the old staged conversion"
    if args.dry_run:
        print(f"Would destroy {count:,} {what}.")
    else:
        print(f"Destroyed {count:,} {what}, and the record values they held.")


if __name__ == "__main__":
    asyncio.run(_main())
