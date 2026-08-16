"""Clear the config of lookup columns whose target list is already gone.

A one-off, not a recurring job. Deleting a list scrubs the lookup *values* pointing
into it and — since `ListService._scrub_references_to_lists` was fixed — the
referencing column's *config* as well. Columns that lost their target before that fix
still hold the dead uuid, and by design that is the case the read path still refuses:
a target that is **named and missing** raises rather than answering "no options",
because it means something got out of step. Which is true, and this is the sweep that
puts it back in step.

    uv run python -m src.maintenance.clear_dangling_lookup_targets --dry-run
    uv run python -m src.maintenance.clear_dangling_lookup_targets

Until it runs, such a column is stuck exactly as the delete-path fix describes in the
past tense: the options endpoint 404s, so the picker, the record dialog and every
label resolution fail; a config PATCH is refused because the target cannot be
resolved, which is the one repair the dialog offers; and a ticked `required` refuses
every write to the list that holds the column. Nothing in the UI gets out of it.

Idempotent — a second run finds nothing and writes nothing.

**The values are already gone.** They were scrubbed when the list was deleted; that
half was never broken. This only clears config, so it cannot destroy anything a
person could still see, and there is nothing here to un-do by halves.

**It clears the same fields the delete path does, deliberately.** Keeping the two
lists in step is what stops a column repaired by this sweep from behaving differently
to one scrubbed at delete time — including `required`, whose whole point is that a
constraint nothing can satisfy freezes writes to an entire list. The one thing it
does *not* do is un-nest dependents: a child pairing against one of these columns has
been dangling for as long as the target has, and `_clear_dependent_nesting` is run
here for the same reason the delete path runs it.
"""
import argparse
import asyncio

import sqlalchemy as sa

# Registers every model; without it the mappers half-configure (see model_registry).
import src.infrastructure.model_registry  # noqa: F401
from src.infrastructure import database as db_module
from src.lists.columns.enums import ColumnType
from src.lists.columns.models import Column
from src.lists.columns.services import ColumnService
from src.lists.models import List

# Cleared to `None`, matching `_scrub_references_to_lists`.
_BLANKED = (
    "target_list_uuid",
    "display_column_uuid",
    "parent_column_uuid",
    "target_filter_column_uuid",
    "option_filters",
)

# Live lookup columns naming a target that is not a live list. The `IS NOT NULL`
# guard is what keeps the columns the delete path already cleared out of scope —
# they are correct as they stand and must not be blanked a second time.
#
# The correlation compares `List.uuid` cast to text rather than casting the config
# value to uuid, so a field holding something that is not a uuid at all — a
# hand-edited config, an import that guessed — matches nothing and is treated as
# dangling, which is what it is. Casting the other way would raise instead.
_DUE = sa.select(Column.id).where(
    Column.deleted_at.is_(None),
    Column.type == ColumnType.lookup,
    Column.config["target_list_uuid"].astext.is_not(None),
    ~sa.exists(
        sa.select(sa.literal(1)).where(
            sa.cast(List.uuid, sa.Text) == Column.config["target_list_uuid"].astext,
            List.deleted_at.is_(None),
        )
    ),
)


async def clear_dangling(dry_run: bool = False) -> int:
    """Blank every dangling lookup's config. Returns the number of columns changed."""
    async with db_module.SessionLocal() as db:
        ids = (await db.execute(_DUE)).scalars().all()
        if dry_run or not ids:
            return len(ids)

        columns = (
            await db.execute(sa.select(Column).where(Column.id.in_(list(ids))))
        ).scalars().all()
        for column in columns:
            # Reassigned rather than mutated, so SQLAlchemy sees the JSONB change.
            config = dict(column.config or {})
            for field in _BLANKED:
                config[field] = None
            config["required"] = False
            column.config = config
            await ColumnService._clear_dependent_nesting(db, column)
        await db.commit()
        return len(columns)


async def _main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="report what would be cleared without touching anything",
    )
    args = parser.parse_args()

    count = await clear_dangling(dry_run=args.dry_run)
    what = "lookup column(s) pointing at a list that no longer exists"
    if args.dry_run:
        print(f"Would clear {count:,} {what}.")
    else:
        print(f"Cleared {count:,} {what}.")


if __name__ == "__main__":
    asyncio.run(_main())
