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
import logging
from typing import Optional, Protocol

import sqlalchemy as sa
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import settings

from ..items.models import ListItem
from .chain import apply_formulas, build_chain, derive_order, formula_edges, order_of
from .enums import COMPUTED_TYPES
from .formula import column_refs
from .models import Column, in_shape

logger = logging.getLogger(__name__)

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


async def recompute_records(db: AsyncSession, list_id: int, *, commit: bool = True) -> int:
    """Rewrite every record's calculated cells from the current formulas.

    Returns how many records actually changed. Rows whose values already agree are
    left alone rather than rewritten with identical bytes: a schema change usually
    moves one column on some of the rows, and rewriting the whole list's JSONB to
    restate what it already says is the expensive half of this job.

    **It stops the moment it is superseded.** Two formula edits overlap more easily
    than they sound like they would — the second PATCH arrives while the first is still
    scanning — and a pass captures its plan once at the start. Left alone, the older
    edit would carry on writing its own answers into rows the newer one had already
    passed: not an error, but a list quietly holding two formulas' answers at once, in
    a column that now filters and sorts.

    The check gates the **commit**, not the computation, and that distinction is the
    whole of its correctness. Checking before writing leaves a window — the schema can
    move between the check and the write, and that chunk lands stale — whereas a chunk
    whose signature no longer matches is rolled back and never existed. So no
    superseded write is ever visible, rather than merely unlikely.

    Stopping is safe because every schema change routes through
    `ColumnService._resettle_formulas`, so whatever moved the signature is already
    running its own backfill over the whole list from row 1. If that one dies too, no
    stamp is written, the columns stay `recompute_pending`, and
    `src/maintenance/recompute_calculated.py` is the backstop. There is deliberately no
    retry loop here: retrying would duplicate the work the superseding run is already
    doing, which on a large list is the expensive mistake this exists to avoid.

    **A completed pass stamps `COMPUTED_FOR` on every calculated column**, which is what
    lets a reader tell current values from stale ones (`pending_keys`). Only a completed
    one: a superseded pass has no version to claim, and a run that dies partway never
    gets here — both of which leave the columns reading as pending, which is exactly
    what they are.
    """
    # Deferred: `items.services` imports this package's modules, so a module-level
    # import here would close the loop.
    from ..items.services import shape_values

    columns = await list_columns(db, list_id)
    started_as = schema_signature(columns)
    plan, key_of_name = build_chain(columns)
    if not plan:
        return 0

    keys = [key for key, _column, _formula in plan]
    changed = 0
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

        # Between the write and the commit, deliberately. The read sees other sessions'
        # committed config (READ COMMITTED), so a formula edit that landed while this
        # chunk was being computed is visible here — and rolling back discards the
        # chunk rather than publishing answers from a formula that no longer exists.
        if schema_signature(await list_columns(db, list_id)) != started_as:
            if commit:
                await db.rollback()
            return changed

        changed += len(writes)
        if commit:
            await db.commit()

    # Nothing was written since the last chunk's check, but the schema can still have
    # moved after it — and stamping then would claim a version these values do not have.
    if schema_signature(await list_columns(db, list_id)) != started_as:
        return changed

    stamped = False
    for key, column in columns.items():
        if column.type not in COMPUTED_TYPES:
            continue
        if (column.config or {}).get(COMPUTED_FOR) != started_as:
            # Reassign rather than mutate, so SQLAlchemy sees the JSONB change.
            column.config = {**(column.config or {}), COMPUTED_FOR: started_as}
            stamped = True
    if stamped and commit:
        await db.commit()

    return changed


class Deferrer(Protocol):
    """Something that will run a coroutine after the response has been sent.

    Structurally a FastAPI/Starlette `BackgroundTasks`, but typed as a protocol so the
    service layer does not import the web framework to describe "later".
    """

    def add_task(self, func, /, *args, **kwargs) -> None: ...


async def _run_deferred(list_id: int) -> None:
    """A deferred backfill, on its own session.

    Its **own**, and that is not a style choice: FastAPI runs a `yield` dependency's
    teardown *before* background tasks (since 0.106), so the request's session is
    already closed by the time this runs. Closing over it would fail on the first
    query, after the response had gone out reporting success.

    Nothing is raised out of here — there is no caller left to catch it. A failure has
    to be logged and then left to the flag, which is already true: no stamp means the
    columns keep reporting `recompute_pending`, and the daily maintenance run finishes
    what this could not.
    """
    from src.infrastructure.database import SessionLocal

    try:
        async with SessionLocal() as db:
            await recompute_records(db, list_id)
    except Exception:
        logger.exception(
            "deferred calculated backfill failed for list_id=%s; the columns stay "
            "pending until the maintenance job runs",
            list_id,
        )


async def record_count(db: AsyncSession, list_id: int) -> int:
    """How many records the backfill would have to walk.

    Soft-deleted rows included, because the backfill visits them too — a record that
    comes back out of the bin should not come back holding an answer from a formula
    that no longer exists.
    """
    return (
        await db.scalar(
            select(sa.func.count()).select_from(ListItem).where(
                ListItem.list_id == list_id
            )
        )
        or 0
    )


async def resettle(
    db: AsyncSession, list_id: int, *, defer: Optional[Deferrer] = None
) -> bool:
    """Bring the list's calculated values back in step. Returns True if deferred.

    The one decision point for "is anyone waiting for this?". Under
    `FORMULA_INLINE_MAX_RECORDS` the work happens here, in the request, and the caller's
    response is already correct when it goes out — which is the behaviour every list in
    a small deployment gets, and the reason the pending flag never flickers on them.

    Over it, the work is handed to `defer` and the response returns immediately. Note
    what is *not* capped: the deferred run still does the whole list. Stopping partway
    would leave the column holding two formulas' answers at once, which filters and
    sorts incoherently — strictly worse than a column that is uniformly one version
    behind and says so.

    With no `defer` — the maintenance script, a test, any non-request caller — size is
    irrelevant and the work is simply done. There is nowhere to defer *to*, and nobody
    waiting.
    """
    await sync_orders(db, list_id)
    if defer is not None and await record_count(db, list_id) > (
        settings.FORMULA_INLINE_MAX_RECORDS
    ):
        defer.add_task(_run_deferred, list_id)
        return True
    await recompute_records(db, list_id)
    return False


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
