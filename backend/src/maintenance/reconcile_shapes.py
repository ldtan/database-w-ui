"""Converge stored records on the shape their columns declare.

Toggling a column's `multiple` deliberately does **not** rewrite its records: on a
million-row table that is a ~25s UPDATE holding row locks on every record, inside a
web request. Instead the service tolerates both shapes at its edges (`coerce_shape`
on the way out, `_validate_data` on the way in) and this job converges the data
afterwards, out of band.

Nothing depends on it having run — a record left in the old shape reads, writes,
exports and scrubs correctly. It exists so the tolerance stays a *migration aid*
rather than permanent scaffolding, and so the stored data eventually says what the
schema says.

Run it from cron / a systemd timer:

    uv run python -m src.maintenance.reconcile_shapes          # every table
    uv run python -m src.maintenance.reconcile_shapes --dry-run

Safe to run repeatedly (idempotent), safe to kill (each chunk commits on its own,
and the next run picks up where it stopped).
"""
import argparse
import asyncio

import sqlalchemy as sa
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

# Registers every model. An entry point that imports only the models it names leaves
# the mappers half-configured — `Column.table` cannot resolve `Table` — so this import
# is load-bearing, exactly as it is for main.py, the tests and Alembic's env.py.
import src.infrastructure.model_registry  # noqa: F401
from src.infrastructure import database as db_module
from src.tables.columns.enums import REFERENCE_TYPES, ColumnType
from src.tables.columns.models import Column

# Rows rewritten per transaction. Small enough that locks are held for
# milliseconds; large enough that a big table does not take all day.
CHUNK = 5_000

# The column types where `multiple` means anything.
SHAPED_TYPES = REFERENCE_TYPES | {ColumnType.choice}

# Deliberately no `deleted_at IS NULL`: a soft-deleted record can be restored, and
# it should not come back holding a shape its column no longer accepts.
_MISMATCHED = {
    # A single-valued column whose cell holds an array.
    False: "jsonb_typeof(data -> :key) = 'array'",
    # A multi-valued column whose cell holds a bare value.
    True: "jsonb_typeof(data -> :key) NOT IN ('array', 'null')",
}
_REWRITE = {
    False: "COALESCE(data -> :key -> 0, 'null'::jsonb)",
    True: "jsonb_build_array(data -> :key)",
}


async def _reconcile_column(
    db: AsyncSession, column: Column, dry_run: bool
) -> int:
    multiple = bool((column.config or {}).get("multiple"))
    params = {"table_id": column.table_id, "key": str(column.uuid), "chunk": CHUNK}
    where = f"table_id = :table_id AND {_MISMATCHED[multiple]}"

    if dry_run:
        return await db.scalar(
            sa.text(f"SELECT count(*) FROM table_records WHERE {where}"), params
        )

    # Chunked by primary key so each transaction is bounded: a long UPDATE over a
    # huge table would hold locks on every row it touches for its whole duration.
    total = 0
    while True:
        result = await db.execute(
            sa.text(
                f"""
                WITH batch AS (
                    SELECT id FROM table_records
                     WHERE {where}
                     ORDER BY id
                     LIMIT :chunk
                     FOR UPDATE SKIP LOCKED
                )
                UPDATE table_records
                   SET data = jsonb_set(data, ARRAY[:key], {_REWRITE[multiple]})
                  FROM batch
                 WHERE table_records.id = batch.id
                """
            ),
            params,
        )
        await db.commit()
        total += result.rowcount
        if result.rowcount < CHUNK:
            return total


async def reconcile(dry_run: bool = False) -> dict[str, int]:
    """Bring every record's shape in line with its column. Returns {column: rows}."""
    changed: dict[str, int] = {}
    async with db_module.SessionLocal() as db:
        columns = (
            await db.execute(
                select(Column).where(
                    Column.deleted_at.is_(None), Column.type.in_(SHAPED_TYPES)
                )
            )
        ).scalars().all()

        for column in columns:
            rows = await _reconcile_column(db, column, dry_run)
            if rows:
                changed[f"{column.name} ({column.uuid})"] = rows
    return changed


async def _main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="report what is out of shape without changing anything",
    )
    args = parser.parse_args()

    changed = await reconcile(dry_run=args.dry_run)
    verb = "would rewrite" if args.dry_run else "rewrote"
    if not changed:
        print("Every record already matches its column's shape.")
        return
    for column, rows in changed.items():
        print(f"{verb} {rows:>9,} record(s) — {column}")
    print(f"{verb} {sum(changed.values()):,} record(s) in total.")


if __name__ == "__main__":
    asyncio.run(_main())
