"""Rebuild a list's stored calculated cells — the query index — from its formulas.

A calculated cell is written by the item service whenever the record is written, so
editing a *record* keeps it current for free. Editing the *schema* does not: changing a
formula, renaming a column a formula reads, or deleting one, changes what every record's
calculated cells should say without touching a single record.

**Nothing a person reads depends on this having run.** `shape_values` recomputes on
every read and overwrites the stored copy, so the grid, a record dialog and the export
are correct whether or not the index is current. What a stale index costs is that
`apply_filters`, `apply_sorts` and `_apply_search` — which run in Postgres, over the
stored bytes — answer for the formula as it was rather than as it is. A row sorts into
the wrong place; it never shows a wrong number.

That is the whole reason this is a job somebody asks for rather than machinery that
watches for drift. There is no schema digest and no "pending" flag here: the two entry
points are a prompt after a formula edit (the app just handled the edit; it does not
need to detect it) and a standing action, with the nightly sweep as the backstop.

Chunked and committed per batch, for the reason the conversion backfill is: a single
UPDATE across a large list holds row locks on every record it touches for its whole
duration. A killed run therefore leaves some records recomputed and some not — the same
state a formula edit leaves *before* this runs, which is what makes it safe to be
interrupted and safe to re-run.

**What the chunking does and does not bound.** Measured at ~0.035 ms of app CPU per
record (two calculated columns per record, linear from 20k to 50k rows), so 20,000
records cost ~0.7 s of CPU — about 7 s on a 0.1-CPU instance. What the chunk size holds
down is *memory* (measured flat at **2.8 MB** above baseline at both 20k and 50k rows,
because a batch is read, rewritten and released) and *lock duration*; it does nothing
about the total, because the batches run one after another. It does keep the process
responsive: every batch `await`s on the database, so the event loop is never blocked for
more than one batch's Python — **~23 ms** measured, well inside a health check. There is
deliberately **no row ceiling**: a big list makes a slow job, not a broken one.

A second pass over an already-current list costs ~0.026 ms per record and writes nothing,
which is what makes the nightly sweep cheap enough to run unconditionally.

**Soft-deleted records are included.** A record in the bin can be restored, and it
should not come back indexed under a formula that no longer exists — the same call
`reconcile_shapes` makes about shapes.
"""
import json
import logging
from typing import Optional, Protocol

import sqlalchemy as sa
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..items.models import ListItem
from .chain import build_chain
from .models import Column, in_shape

logger = logging.getLogger(__name__)

# Records read, recomputed and written per transaction. Matches the conversion
# backfill's: every row costs Python work, so the transaction stays short.
CHUNK = 500

# Namespace for the per-list advisory lock that admits one re-index at a time.
# Distinct from `_UNIQUE_LOCK_NS` in the item service so the two never collide on a
# list id.
RECOMPUTE_LOCK_NS = 0x43414C43  # "CALC"

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
    loaded would otherwise come back with the attributes it had when it was loaded.
    That is exactly the value a concurrent formula edit changed. This refreshes the
    instances in place rather than expiring them, so nothing the caller is holding
    turns into a deferred load.
    """
    result = await db.execute(
        select(Column)
        .where(Column.list_id == list_id, in_shape())
        .execution_options(populate_existing=True)
    )
    return {str(column.uuid): column for column in result.scalars().all()}


async def count_records(db: AsyncSession, list_id: int) -> int:
    """How many records a re-index would walk — the bin included, as the job is.

    The route answers with this so the UI can put a number on the action, and uses it
    to decide whether anybody is going to be kept waiting.
    """
    return await db.scalar(
        select(func.count()).select_from(ListItem).where(ListItem.list_id == list_id)
    ) or 0


async def try_lock(db: AsyncSession, list_id: int) -> bool:
    """Claim the right to re-index this list, or report that someone already has.

    **Session-scoped, not transaction-scoped**, and both halves of that matter. It
    survives the per-chunk `COMMIT`, so one connection holds it for the whole run and
    no second connection is pinned (see the two-connection caution in known-gaps). And
    Postgres drops it when the connection goes, so a killed process or a restarted
    instance releases it with no stale-lock cleanup, no TTL, and no unstick path to
    write — which is exactly why this beats a flag in a table or a set in memory.

    Verified rather than assumed: an `AsyncSession` keeps its connection until it is
    closed, not until it commits, so the lock genuinely spans the chunks.
    """
    return bool(
        await db.scalar(select(func.pg_try_advisory_lock(RECOMPUTE_LOCK_NS, list_id)))
    )


async def unlock(db: AsyncSession, list_id: int) -> None:
    await db.execute(select(func.pg_advisory_unlock(RECOMPUTE_LOCK_NS, list_id)))


async def recompute_records(
    db: AsyncSession, list_id: int, *, commit: bool = True, dry_run: bool = False
) -> int:
    """Rewrite every record's calculated cells from the current formulas.

    Returns how many records actually changed. Rows whose values already agree are left
    alone rather than rewritten with identical bytes: a schema change usually moves one
    column on some of the rows, and rewriting the whole list's JSONB to restate what it
    already says is the expensive half of this job.

    **The evaluation plan is captured once, at the start.** Re-reading the formulas per
    chunk would leave early chunks indexed under the old formula and later ones under
    the new — a *mixture*, which filters incoherently. Capturing once leaves the index
    uniformly one version behind, which filters coherently and is merely out of date,
    and the next run settles it. Being uniformly stale is a version; being half-updated
    is neither.

    Rows are taken `FOR UPDATE SKIP LOCKED`, so a record somebody is editing right now
    is passed over rather than waited on. That leaves no gap: a write goes through
    `_validate_data`, which computes the cell itself, so the row that was skipped is
    the one row guaranteed not to need this.

    ``dry_run`` counts what would change and writes nothing. It also takes no row locks
    — there is no write to protect, and a report should not make an editor wait.
    """
    # Deferred: `items.services` imports this package's modules, so a module-level
    # import here would close the loop.
    from ..items.services import shape_values

    columns = await list_columns(db, list_id)
    plan, key_of_name = build_chain(columns)
    if not plan:
        return 0

    keys = [key for key, _column, _formula in plan]
    changed = 0
    after = 0
    while True:
        stmt = (
            select(ListItem.id, ListItem.data)
            .where(ListItem.list_id == list_id, ListItem.id > after)
            .order_by(ListItem.id)
            .limit(CHUNK)
        )
        if not dry_run:
            stmt = stmt.with_for_update(skip_locked=True)
        rows = (await db.execute(stmt)).all()
        if not rows:
            break
        after = rows[-1].id

        writes = []
        for row in rows:
            data = row.data or {}
            values = shape_values(columns, plan, key_of_name, data)
            patch = {key: values[key] for key in keys if values[key] != data.get(key)}
            if patch:
                writes.append({"row_id": row.id, "patch": json.dumps(patch)})

        if writes and not dry_run:
            # `data || patch` rather than a whole-row assignment: this job owns the
            # calculated keys and nothing else, so merging leaves a concurrent write
            # to some other column of the same record alone.
            await db.execute(_WRITE, writes)

        changed += len(writes)
        if commit and not dry_run:
            await db.commit()

    if dry_run:
        # The reads opened a transaction; end it rather than leaving one idle.
        await db.rollback()
    return changed


async def recompute_locked(db: AsyncSession, list_id: int) -> Optional[int]:
    """`recompute_records` behind the per-list lock. ``None`` if one is already running.

    The lock is released in a `finally` so an exception mid-run does not leave the list
    unre-indexable until the connection happens to close — though it would be released
    then too.
    """
    if not await try_lock(db, list_id):
        return None
    try:
        return await recompute_records(db, list_id)
    finally:
        await unlock(db, list_id)


class Deferrer(Protocol):
    """Something that will run a coroutine after the response has been sent.

    Structurally a FastAPI/Starlette `BackgroundTasks`, but typed as a protocol so the
    service layer does not import the web framework to describe "later".
    """

    def add_task(self, func, /, *args, **kwargs) -> None: ...


async def run_deferred(list_id: int) -> None:
    """A deferred re-index, on its own session.

    Its **own**, and that is not a style choice: FastAPI runs a `yield` dependency's
    teardown *before* background tasks (since 0.106), so the request's session is
    already closed by the time this runs. Closing over it would fail on the first query,
    after the response had gone out reporting success.

    Three things about `BackgroundTasks` are worth knowing here, because none is
    obvious. It creates no `asyncio.Task` — it is awaited inside the ASGI response
    cycle — so there is **no handle to cancel**, uvicorn's graceful shutdown waits for
    it, and a redeploy during a long run is delayed and then truncated. It competes with
    every other request for the one process's CPU. And on a free tier that spins down on
    idle, a background run is not traffic, so a long one can be killed under a user who
    fired it and left.

    All three are survivable because chunks commit independently: a truncated run leaves
    the index part-rebuilt, which is where it already was, and the next run or the
    nightly sweep finishes it. Nothing is raised out of here — there is no caller left
    to catch it — so a failure is logged and left to those.
    """
    from src.infrastructure.database import SessionLocal

    try:
        async with SessionLocal() as db:
            done = await recompute_locked(db, list_id)
            if done is None:
                logger.info(
                    "deferred re-index for list_id=%s skipped; another run holds "
                    "the lock and is doing the same work",
                    list_id,
                )
    except Exception:
        logger.exception(
            "deferred re-index failed for list_id=%s; the stored index may lag the "
            "formulas until the next run or the nightly recompute_calculated sweep",
            list_id,
        )
