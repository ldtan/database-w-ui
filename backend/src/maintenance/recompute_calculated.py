"""Recompute every list's stored calculated cells from their formulas.

A calculated cell is stored, and the app keeps it current: the item service writes it on
every record save, and the column service backfills after a schema change that moves what
a formula answers (`lists/columns/backfill.py`). This is the repair tool for the cases
that sit outside both — a backfill killed part-way through a huge list, a config edited
straight in the database, a formula that started parsing again because the language's
bounds moved.

Run it from cron / a systemd timer, or by hand after an upgrade:

    uv run python -m src.maintenance.recompute_calculated             # every list
    uv run python -m src.maintenance.recompute_calculated --dry-run
    uv run python -m src.maintenance.recompute_calculated --list <uuid>

Safe to run repeatedly (a second run rewrites nothing), and safe to kill: each chunk
commits on its own, and the next run picks up whatever is still out of date. Nothing
depends on it having run — every value it fixes was already going to be fixed the next
time that record was saved.
"""
import argparse
import asyncio
from typing import Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

# Registers every model, for the reason `reconcile_shapes` spells out: an entry point
# that imports only the models it names leaves the mappers half-configured.
import src.infrastructure.model_registry  # noqa: F401
from src.infrastructure import database as db_module
from src.lists.columns import backfill
from src.lists.columns.chain import apply_formulas, build_chain
from src.lists.columns.enums import COMPUTED_TYPES
from src.lists.columns.models import Column, in_shape
from src.lists.items.models import ListItem
from src.lists.models import List


async def _lists_with_formulas(db: AsyncSession, only: Optional[UUID]) -> list:
    """The lists worth visiting: those that actually hold a calculated column."""
    stmt = (
        select(List.id, List.uuid, List.name)
        .join(Column, Column.list_id == List.id)
        .where(List.deleted_at.is_(None), in_shape(), Column.type.in_(COMPUTED_TYPES))
        .distinct()
        .order_by(List.id)
    )
    if only is not None:
        stmt = stmt.where(List.uuid == only)
    return (await db.execute(stmt)).all()


async def _stale_count(db: AsyncSession, list_id: int) -> int:
    """How many records `recompute_records` would rewrite, without rewriting any.

    Deliberately a separate pass rather than a flag on the backfill: a dry run should
    hold no locks and take no write path at all, and the two loops are short enough
    that sharing them would cost more clarity than it saves.
    """
    columns = await backfill.list_columns(db, list_id)
    plan, key_of_name = build_chain(columns)
    if not plan:
        return 0
    from src.lists.items.services import shape_values

    stale = 0
    after = 0
    while True:
        rows = (
            await db.execute(
                select(ListItem.id, ListItem.data)
                .where(ListItem.list_id == list_id, ListItem.id > after)
                .order_by(ListItem.id)
                .limit(backfill.CHUNK)
            )
        ).all()
        if not rows:
            return stale
        after = rows[-1].id
        for row in rows:
            data = row.data or {}
            values = apply_formulas(plan, key_of_name, shape_values(columns, data))
            if any(values[key] != data.get(key) for key, _c, _f in plan):
                stale += 1


async def recompute(
    dry_run: bool = False, only: Optional[UUID] = None
) -> dict[str, int]:
    """Bring every list's calculated cells up to date. Returns {list: records}."""
    changed: dict[str, int] = {}
    async with db_module.SessionLocal() as db:
        for list_id, list_uuid, list_name in await _lists_with_formulas(db, only):
            if dry_run:
                rows = await _stale_count(db, list_id)
            else:
                # Orders first: the chain a record is computed through should be the
                # one the config claims, so a run that fixes both leaves them agreeing.
                await backfill.sync_orders(db, list_id)
                rows = await backfill.recompute_records(db, list_id)
            if rows:
                changed[f"{list_name} ({list_uuid})"] = rows
    return changed


async def _main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="report what is out of date without changing anything",
    )
    parser.add_argument(
        "--list",
        type=UUID,
        default=None,
        metavar="UUID",
        help="only this list, rather than every list with a formula",
    )
    args = parser.parse_args()

    changed = await recompute(dry_run=args.dry_run, only=args.list)
    verb = "would recompute" if args.dry_run else "recomputed"
    if not changed:
        print("Every calculated cell already matches its formula.")
        return
    for name, rows in changed.items():
        print(f"{verb} {rows:>9,} record(s) — {name}")
    print(f"{verb} {sum(changed.values()):,} record(s) in total.")


if __name__ == "__main__":
    asyncio.run(_main())
