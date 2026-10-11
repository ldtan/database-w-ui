"""Where a reference column's *label* comes from, as something SQL can order by.

A `lookup` cell holds the target row's uuid; a `user`/`group` cell holds a principal's.
Ordering by the stored value therefore orders by uuid7 text — creation order scrambled by
hex — while the grid shows a name. Sorting them meaningfully means reaching into another
table, which the record listing has never done: cross-table access here has always been a
separate query (`_reference_matches`) or an `IN (subquery)` (`record_view_filter`).

So this module resolves, per column, a **derived table of (uuid text, label)** that
`apply_sorts` LEFT JOINs. Three reasons it is a derived table rather than a correlated
scalar subquery:

* `PermissionService.record_view_filter` returns a predicate bound to the `TableRecord`
  *class*. Inside a derived table it scopes to that table's own FROM and can be reused
  verbatim; inside a correlated subquery over an alias it would silently bind to the
  outer row and hand every caller the unfiltered label.
* Postgres flattens a simple derived table, so the planner still gets to choose between
  an index nested loop on the unique `uuid` and a hash join, on the real relative sizes.
* `uuid` is unique, so the join cannot fan out. A listing whose row count depended on its
  ORDER BY would be a worse bug than the one this fixes.

Resolution is async and permission-dependent, which is exactly why it lives here rather
than in `filtering.py`: that module is a synchronous pure translation of a view's config
into clauses, and importing `permissions` into it would cycle back through the record
service. The split is "resolve here, order there".

**When in doubt, resolve to nothing.** A missing target table, no `lookup` on it, or no
column to read a label from all return no source, and the caller falls back to ordering
by the raw uuid. That is not a shrug: someone who cannot `lookup` the target sees
`Row a1b2c3d4` stubs in every cell of that column (`LookupControls.tsx`), so uuid order
*is* the order on their screen. Sorting must never be the thing that 500s a listing.
"""
from dataclasses import dataclass
from typing import Optional

import sqlalchemy as sa
from sqlalchemy import cast, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.auth.users.models import User, UserGroup
from src.core.utils import coerce_uuid

from ..columns.enums import REFERENCE_TYPES, ColumnType
from ..columns.models import Column
from ..models import Table
from .filtering import first_value, ordered_keys
from .models import TableRecord


@dataclass(frozen=True)
class LabelSource:
    """One reference column's labels, ready to join onto the record query."""

    # The derived table. Aliased per source, so two columns pointing at the same
    # target table can both be joined without colliding.
    table: sa.Subquery
    # The column of `table` to put in ORDER BY.
    label: sa.ColumnElement
    # How a record's cell finds its row in `table`.
    onclause: sa.ColumnElement


def _principal_source(column: Column) -> LabelSource:
    """A `user`/`group` column's labels: the name the cell renders.

    `username` for a user — never the connected person's real name, which is private
    (the same rule the export and the picker follow). `display_name` for a group, which
    is what `PrincipalControls.labelOf` puts in the cell; the export deliberately writes
    a group's `name` instead, because that slug is a wire identifier a saved CSV resolves
    against, and a cosmetic rename must not invalidate the file. Ordering is about what
    is on screen, so the two differ on purpose.

    No permission gate and no lifecycle filter. Anyone who can read the table already
    reads these names in the cells, and a cell holding a deleted principal still renders
    its name — resolving principals *by uuid* includes deleted rows (`_select_any` in
    UserService.list), so filtering them here would sort a row as blank while the screen
    reads "ada".
    """
    is_user = column.type == ColumnType.user
    model = User if is_user else UserGroup
    label = User.username if is_user else UserGroup.display_name
    table = (
        select(cast(model.uuid, sa.Text).label("ref"), label.label("label"))
        .correlate(None)
        .subquery()
    )
    return LabelSource(table, table.c.label, table.c.ref == first_value(column))


async def _lookup_source(
    db: AsyncSession, user, column: Column
) -> Optional[LabelSource]:
    """A `lookup` column's labels: the display column of the row each cell points at.

    The same three questions the export resolver and the label search already ask, in the
    same order — which table, may this caller `lookup` it, and which of its columns is the
    label. `display_column_key` is the shared answer to the third (configured column ->
    the target's primary -> its first), and this is deliberately a fourth caller of it
    rather than a fourth copy of the chain: a sort that disagreed with the cell it sorts
    would be indistinguishable from a broken sort.

    Two absences are load-bearing.

    **No `deleted_at`/`archived_at` filter.** A held reference to a binned or archived row
    still *reads* its label — only search excludes them, because matching a label is
    search and rendering one is a uuid resolution. Filtering them here would sort a row as
    empty while the screen shows "Acme".

    **No `option_filters`.** Those narrow what the picker *offers*; they say nothing about
    what a held value means, and a cell can legitimately point at a row that has since
    stopped passing them.

    The per-record predicate *is* applied, and that is the security boundary: a target row
    this caller may not `lookup` drops out of the derived table, the LEFT JOIN yields
    NULL, and it sorts as empty. Ordering by its real label instead would leak the one
    thing the row-level grant withholds — ask for the sorted page, bisect, read the label
    you were never given.
    """
    # Deferred like every other permission call site under `records/` — a module-level
    # import cycles back through the auth and workspace services.
    from src.permissions.enums import Action, ResourceType
    from src.permissions.services import PermissionService

    # `display_column_key` lives with the record service, which imports this module.
    from .services import display_column_key

    if user is None:
        return None

    config = column.config or {}
    target = (
        await db.execute(
            select(Table)
            # record_view_filter reads the owner off the workspace.
            .options(selectinload(Table.workspace))
            .where(
                Table.uuid == coerce_uuid(config.get("target_table_uuid")),
                Table.deleted_at.is_(None),
            )
        )
    ).scalars().first()
    if target is None or not await PermissionService.has(
        db, user, ResourceType.table, target.id, Action.lookup
    ):
        return None

    display_key = await display_column_key(db, target.id, config)
    if display_key is None:
        return None

    visible = await PermissionService.record_view_filter(
        db, user, target, Action.lookup
    )
    table = (
        select(
            # Cast the *uuid column*, never the cell. `(data ->> key)::uuid` raises
            # InvalidTextRepresentationError on the first cell holding drifted text,
            # and an unguarded cast in ORDER BY fails the whole statement rather than
            # the row — the same 500 `_numeric`'s jsonb_typeof guard exists to avoid.
            # Comparing as text means junk simply fails to match and sorts as empty.
            cast(TableRecord.uuid, sa.Text).label("ref"),
            TableRecord.data[display_key].astext.label("label"),
        )
        .where(TableRecord.table_id == target.id, visible)
        # This selects from table_records and so does the query it will be joined into.
        # Belt and braces against auto-correlation quietly turning it into a
        # correlated subquery over the outer row.
        .correlate(None)
        .subquery()
    )
    return LabelSource(table, table.c.label, table.c.ref == first_value(column))


async def label_sources(
    db: AsyncSession,
    user,
    columns: dict[str, Column],
    sorts,
    group_by: Optional[dict] = None,
) -> dict[str, LabelSource]:
    """Label sources for the reference columns this request orders by.

    Keyed by column uuid, and sparse on purpose: a column with no entry is ordered the
    way it always was, by the uuid in the cell. Pass the result to `apply_sorts`.

    Every entry is a table to join and a target table to scan, so only the columns this
    request actually orders by are resolved — an unsorted listing and the count endpoint
    walk an empty key table and never touch the database.
    """
    sources: dict[str, LabelSource] = {}
    for key in ordered_keys(sorts, group_by):
        column = columns.get(key)
        if column is None or column.type not in REFERENCE_TYPES:
            continue
        if column.type == ColumnType.lookup:
            source = await _lookup_source(db, user, column)
        else:
            source = _principal_source(column)
        if source is not None:
            sources[key] = source
    return sources
