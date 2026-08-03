"""Recompute a list's stored calculated cells after a schema change.

A calculated cell is written by the item service whenever the record is written, so
editing a *record* keeps it current for free. Editing the *schema* does not: changing a
formula, renaming a column a formula reads, or deleting one, changes what every record's
calculated cells should say without touching a single record. Before the values were
stored that cost nothing — the next read recomputed them — and it is exactly what has to
be paid for now that they are stored, or a formula edit would show up only on rows
somebody happened to save afterwards.

So the column service calls `recompute_records` after any change that can move a
formula's answer, and `sync_orders` after any change that can move a column's place in
the chain. Both are idempotent and safe to re-run, which is what makes
`src/maintenance/recompute_calculated.py` a repair tool rather than a migration.

Chunked and committed per batch, for the reason the conversion backfill is: a single
UPDATE across a large list holds row locks on every record it touches for its whole
duration, inside a web request. A killed run therefore leaves some records recomputed
and some not — the same state a formula edit leaves *before* this runs, and the reason
the maintenance script exists.

**What the chunking does and does not bound.** Measured at ~0.11 ms of app CPU per
record, so 20,000 records cost ~2.2 s of CPU — about 22 s on a 0.1-CPU instance, which
is what a formula edit on a list that size costs the person who made it. What the chunk
size holds down is *memory* (flat at a few MB however long the list is, because a batch
is read, rewritten and released) and *lock duration*; it does nothing about the total,
because the batches run one after another inside one request. It does keep the process
responsive: every batch `await`s on the database, so the event loop is never blocked for
more than one batch's Python — ~40 ms measured, well inside a health check. There is
deliberately **no row ceiling**: a big list makes a slow request, not a broken one.

**Soft-deleted records are included.** A record in the bin can be restored, and it
should not come back showing an answer from a formula that no longer exists — the same
call `reconcile_shapes` makes about shapes.
"""
import hashlib
import json
from typing import Optional

import sqlalchemy as sa
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..items.models import ListItem
from .chain import apply_formulas, build_chain, derive_order, formula_edges, order_of
from .enums import COMPUTED_TYPES
from .formula import column_refs
from .models import Column, in_shape

# Records read, recomputed and written per transaction. Matches the conversion
# backfill's: every row costs Python work, so the transaction stays short.
CHUNK = 500

_WRITE = sa.text(
    "UPDATE list_items SET data = data || CAST(:patch AS jsonb) WHERE id = :row_id"
)


async def list_columns(db: AsyncSession, list_id: int) -> dict[str, Column]:
    """The list's fields as the item service sees them — `in_shape`, keyed by uuid.

    A conversion's hidden scratch column is deliberately not a field of the list, so a
    formula cannot read it and this must not write into it either.

    ``populate_existing`` because this is the one reader that has to see *another*
    session's committed config. Sessions here are built with `expire_on_commit=False`
    (see `infrastructure/database.py` for why — expiring makes a later attribute read
    do lazy IO outside the greenlet and raise), so a column this session has already
    loaded would otherwise come back with the attributes it had when it was loaded,
    however long ago. That is exactly the value a concurrent formula edit changed.
    This refreshes the instances in place rather than expiring them, so nothing the
    caller is holding turns into a deferred load.
    """
    result = await db.execute(
        select(Column)
        .where(Column.list_id == list_id, in_shape())
        .execution_options(populate_existing=True)
    )
    return {str(column.uuid): column for column in result.scalars().all()}


def references_a_formula(columns: dict[str, Column], *names: Optional[str]) -> bool:
    """Does any calculated column of this list read one of ``names``?

    The question the column service asks before paying for a backfill: renaming or
    deleting a column only invalidates stored values if a formula was reading it. Takes
    several names because a rename has two — the old one, which formulas may reference
    today, and the new one, which they may reference *because* of the rename (a formula
    left broken by an earlier rename comes back to life when the name returns).
    """
    wanted = {name for name in names if name}
    if not wanted:
        return False
    edges_by_name = formula_edges(list(columns.values()))
    if wanted & edges_by_name.keys():
        return True  # one of them is itself a calculated column
    # `formula_edges` only reports calculated-to-calculated links, so ask the formulas
    # themselves about the plain columns.
    plan, _ = build_chain(columns)
    return any(
        formula is not None and (column_refs(formula) & wanted)
        for _key, _column, formula in plan
    )


# Which schema version a calculated column's stored values were computed for. Written
# by `recompute_records` when a pass completes, and compared against the list's current
# signature to answer "are these values current?" (`pending_keys`).
#
# A service-owned config key, underscore-prefixed for the same reason the conversion's
# `_conversion_of` is: it is bookkeeping, not a setting, and `ColumnConfig` forbids
# extra fields so a client cannot set it — while a config PATCH preserves unknown keys,
# so it survives an ordinary edit.
COMPUTED_FOR = "_computed_for"


def schema_signature(columns: dict[str, Column]) -> str:
    """A short digest of everything that decides what the formulas answer.

    Two jobs, deliberately one function. `recompute_records` compares it before and
    after a pass to notice that the schema moved underneath it, and it is *stored* on
    each calculated column so a reader can tell whether the values were computed for
    the schema as it stands now. Those have to be the same question, or a pass could
    declare itself finished under a definition of "unchanged" that a reader disagrees
    with.

    What goes in is only what changes an answer: each calculated column's formula and
    place in the chain, and — for every *name* any formula mentions — which column that
    name currently resolves to and what type it is. That second half is what makes a
    rename, a delete, a restore or a conversion of a referenced column register, while
    adding an unrelated column to the list does not. Getting that scope wrong in the
    generous direction would mark every formula stale every time somebody added a text
    column, and a warning that cries wolf is one people learn to click past.

    A name no column answers to is included as such, so a formula left dangling by a
    rename comes back to life — visibly — when a column takes that name again.
    """
    plan, key_of_name = build_chain(columns)
    calculated = sorted(
        (key, str((column.config or {}).get("formula")), order_of(column))
        for key, column, _formula in plan
    )

    referenced: set[str] = set()
    for _key, _column, formula in plan:
        if formula is not None:
            referenced |= column_refs(formula)
    resolved = []
    for name in sorted(referenced):
        key = key_of_name.get(name)
        target = columns.get(key) if key is not None else None
        resolved.append((name, key or "", target.type.value if target else ""))

    payload = json.dumps([calculated, resolved], sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def pending_keys(columns: dict[str, Column]) -> set[str]:
    """The calculated columns whose stored values are not known to be current.

    "Not known to be", not "are not": the answer is deliberately pessimistic. A pass
    that finished but died before stamping reads as pending though its values are fine,
    which is the safe direction to be wrong in — an unnecessary warning costs a glance,
    and a missing one costs somebody trusting a number that moved.

    A calculated column that has never been through a backfill has no stamp at all and
    is pending, which is correct: nothing has ever established what its values match.
    """
    signature = schema_signature(columns)
    return {
        key
        for key, column in columns.items()
        if column.type in COMPUTED_TYPES
        and (column.config or {}).get(COMPUTED_FOR) != signature
    }


# How many times a single call will re-scan after finding the schema moved underneath
# it. Two concurrent edits need one extra pass; a burst of them is better served by
# giving up and letting the *next* edit's backfill (or the maintenance script) settle
# it than by looping in a request for as long as somebody keeps typing.
MAX_PASSES = 5


async def recompute_records(db: AsyncSession, list_id: int, *, commit: bool = True) -> int:
    """Rewrite every record's calculated cells from the current formulas.

    Returns how many records actually changed. Rows whose values already agree are
    left alone rather than rewritten with identical bytes: a schema change usually
    moves one column on some of the rows, and rewriting the whole list's JSONB to
    restate what it already says is the expensive half of this job.

    **A pass repeats if the schema moved while it ran.** Two formula edits overlap more
    easily than they sound like they would — the second PATCH arrives while the first is
    still scanning — and a pass captures its plan once at the start, so without this the
    older edit would carry on writing its own answers into rows the newer one had
    already passed. The result was not an error but something worse: a list quietly
    holding two formulas' answers at once, in a column that now filters and sorts.

    Re-checking the signature at the end of a pass is enough to converge, and costs one
    small query. Each edit's own backfill always scans the whole list from the start
    with the config it committed, so a stale pass only has to notice it was stale and go
    again. Bounded by `MAX_PASSES`; past that the next write or
    `src/maintenance/recompute_calculated.py` settles it.

    **A settled pass stamps `COMPUTED_FOR` on every calculated column**, which is what
    lets a reader tell current values from stale ones (`pending_keys`). Only a settled
    one: a run that exhausts `MAX_PASSES` without the schema holding still has no
    version to claim, and a run that dies partway never gets here — both of which leave
    the columns reading as pending, which is exactly what they are.
    """
    # Deferred: `items.services` imports this package's modules, so a module-level
    # import here would close the loop.
    from ..items.services import shape_values

    changed = 0
    settled: Optional[tuple[dict[str, Column], str]] = None
    for _pass in range(MAX_PASSES):
        columns = await list_columns(db, list_id)
        started_as = schema_signature(columns)
        plan, key_of_name = build_chain(columns)
        if not plan:
            return changed

        keys = [key for key, _column, _formula in plan]
        after = 0
        while True:
            rows = (
                await db.execute(
                    select(ListItem.id, ListItem.data)
                    .where(ListItem.list_id == list_id, ListItem.id > after)
                    .order_by(ListItem.id)
                    .limit(CHUNK)
                )
            ).all()
            if not rows:
                break
            after = rows[-1].id

            writes = []
            for row in rows:
                data = row.data or {}
                values = apply_formulas(plan, key_of_name, shape_values(columns, data))
                patch = {key: values[key] for key in keys if values[key] != data.get(key)}
                if patch:
                    writes.append({"row_id": row.id, "patch": json.dumps(patch)})

            if writes:
                # `data || patch` rather than a whole-row assignment: this job owns the
                # calculated keys and nothing else, so merging leaves a concurrent write
                # to some other column of the same record alone.
                await db.execute(_WRITE, writes)
                changed += len(writes)
            if commit:
                await db.commit()

        latest = await list_columns(db, list_id)
        if schema_signature(latest) == started_as:
            settled = (latest, started_as)
            break

    if settled is not None:
        columns, signature = settled
        stamped = False
        for key, column in columns.items():
            if column.type not in COMPUTED_TYPES:
                continue
            if (column.config or {}).get(COMPUTED_FOR) != signature:
                # Reassign rather than mutate, so SQLAlchemy sees the JSONB change.
                column.config = {**(column.config or {}), COMPUTED_FOR: signature}
                stamped = True
        if stamped and commit:
            await db.commit()

    return changed


async def sync_orders(db: AsyncSession, list_id: int, *, commit: bool = True) -> int:
    """Bring every calculated column's `compute_order` back in line with the graph.

    Saving one column can move others: giving `total` a dependency on `tax` pushes it
    past `tax`, and anything reading `total` has to move too. Walking the chain in
    evaluation order settles them in one pass, because every column's dependencies are
    already final by the time it is reached.

    An order is only ever raised. A stored value is indistinguishable from one a person
    set by hand (see `chain.derive_order`), so lowering it would silently discard an
    explicit choice; leaving gaps in the numbering costs nothing, since only the
    relative order means anything.
    """
    columns = await list_columns(db, list_id)
    plan, _ = build_chain(columns)
    by_name = {column.name: column for column in columns.values()}
    edges = formula_edges(list(columns.values()))

    changed = 0
    for _key, column, _formula in plan:
        if column.type not in COMPUTED_TYPES:
            continue
        config = column.config or {}
        wanted = derive_order(edges.get(column.name, set()), by_name, order_of(column))
        # `not in config`, not just a value comparison: a column at the front of the
        # chain derives to 0, which is also what an *absent* key reads as, so a value
        # check alone would leave the key missing forever on exactly the columns whose
        # place a reader most wants stated outright.
        if wanted != order_of(column) or "compute_order" not in config:
            # Reassign rather than mutate, so SQLAlchemy sees the JSONB change.
            column.config = {**config, "compute_order": wanted}
            changed += 1
    if changed and commit:
        await db.commit()
    return changed
