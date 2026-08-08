"""Rebuild the stored copies a merged form keeps of its linked records.

A mirror is written when the referring record is written, and pushed out again when the
linked record is edited (`ListItemService.refresh_mirrors_of`), so ordinary use keeps
the copies current for free. What does not: a merged form gaining or losing a borrowed
field, a linked column renamed or restored, a record written by something that bypassed
the service layer, and — the one that needs no event at all — a fan-out truncated by a
redeploy or a free-tier spin-down mid-transaction.

**Nothing depends on it having run**, which is the whole reason a nightly sweep is an
adequate backstop rather than a correctness requirement. Reads recompute every mirror
and overwrite the stored copy (`shape_values`), so the grid, the record dialog and a
merged form are correct whether or not this has run. What a stale copy costs is that
`apply_filters` and `apply_sorts` — which run inside Postgres, over the stored bytes —
answer for the linked record as it was rather than as it is. A row sorts to the wrong
place; nobody is shown a wrong value.

The same bargain `recompute_calculated` makes, one list further away: there the index
falls behind a *schema* change, here it falls behind somebody else's *record*.

Run it from cron / a systemd timer:

    uv run python -m src.maintenance.reconcile_mirrors
    uv run python -m src.maintenance.reconcile_mirrors --dry-run

Safe to run repeatedly — a record whose copies already agree is left alone rather than
rewritten with identical bytes — and safe to kill, since each chunk commits on its own
and the next run picks up whatever was missed.
"""
import argparse
import asyncio

from sqlalchemy import select

# Registers every model. An entry point that imports only the models it names leaves
# the mappers half-configured, exactly as for the sibling jobs.
import src.infrastructure.model_registry  # noqa: F401
from src.infrastructure import database as db_module
from src.lists.forms.models import Form
from src.lists.items import mirror
from src.lists.items.models import ListItem

# Rows held in memory at once. Matches `reconcile_shapes`: big enough that the round
# trips disappear, small enough that a killed run loses little and no lock is held
# across the whole list.
_BATCH = 500


async def _lists_with_mirrors(db) -> list[int]:
    """Every list whose forms declare a merge block."""
    result = await db.execute(
        select(Form.list_id)
        .where(Form.deleted_at.is_(None), Form.config["merge"].isnot(None))
        .distinct()
    )
    return sorted(result.scalars().all())


async def _reconcile_list(db, list_id: int, dry_run: bool) -> int:
    """Rewrite the mirrors of one list's records. Returns how many changed."""
    specs = await mirror.specs_for(db, list_id)
    if not specs:
        return 0

    changed = 0
    last_id = 0
    while True:
        rows = (
            await db.execute(
                select(ListItem.id, ListItem.data)
                .where(ListItem.list_id == list_id, ListItem.id > last_id)
                .order_by(ListItem.id)
                .limit(_BATCH)
            )
        ).all()
        if not rows:
            break
        last_id = rows[-1][0]

        # One fetch of the linked records for the whole batch, not one per row. `user`
        # is None on the read path for a reason that does not apply here: this is not
        # rendering anything for anybody, so it resolves every linked record and writes
        # the true value. Permission is a *read-time* question, asked again by
        # `shape_values` for whoever eventually looks.
        datas = [d or {} for _, d in rows]
        linked = await _fetch_all(db, specs, datas)

        for item_id, data in rows:
            data = data or {}
            wanted = mirror.resolve(specs, linked, data)
            if all(data.get(k) == v for k, v in wanted.items()):
                continue  # already agrees — do not rewrite identical bytes
            changed += 1
            if not dry_run:
                await db.execute(
                    ListItem.__table__.update()
                    .where(ListItem.id == item_id)
                    .values(data={**data, **wanted})
                )
        if not dry_run:
            await db.commit()

    return changed


async def _fetch_all(db, specs, datas) -> dict:
    """Every linked record this batch points at, unfiltered — see the note above."""
    found: dict = {}
    for spec in specs:
        uuids = {u for u in (mirror.linked_uuid(spec, d) for d in datas) if u}
        if not uuids:
            continue
        rows = await db.execute(
            select(ListItem.uuid, ListItem.data).where(
                ListItem.list_id == spec.target.id,
                ListItem.uuid.in_(uuids),
                ListItem.deleted_at.is_(None),
            )
        )
        for uuid, data in rows.all():
            found[(spec.link_key, str(uuid))] = data or {}
    return found


async def reconcile(dry_run: bool = False) -> dict[int, int]:
    """Bring every list's mirrors in line with the records they copy.

    Returns {list_id: records changed}, omitting lists that needed nothing.
    """
    changed: dict[int, int] = {}
    async with db_module.SessionLocal() as db:
        for list_id in await _lists_with_mirrors(db):
            rows = await _reconcile_list(db, list_id, dry_run)
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

    changed = await reconcile(dry_run=args.dry_run)
    verb = "would rebuild" if args.dry_run else "rebuilt"
    if not changed:
        print("Every linked copy already matches the record it copies.")
        return
    for list_id, rows in changed.items():
        print(f"{verb} {rows:>9,} record(s) — list_id {list_id}")
    print(f"{verb} {sum(changed.values()):,} record(s) in total.")


if __name__ == "__main__":
    asyncio.run(_main())
