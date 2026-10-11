"""Prove the per-table advisory lock actually serializes writes.

Driving two simultaneous inserts through the HTTP/ASGI stack does *not*
reproduce the race — httpx's ASGITransport effectively serializes the requests,
so both a locked and an unlocked implementation yield one success + one
conflict (verified empirically). To test the lock with teeth, we drive the
service layer directly: hold the table's advisory lock in one transaction and
assert that a concurrent `create` blocks on it, then completes once released.
Remove the lock from `_enforce_unique` and this test fails.
"""
import asyncio

from sqlalchemy import func, select

from src.tables.records.schemas import TableRecordCreate
from src.tables.records.services import TableRecordService, _UNIQUE_LOCK_NS
from src.tables.models import Table
from src.tables.services import TableService


async def test_create_blocks_while_table_advisory_lock_is_held(
    table_uuid, add_column, session_factory, user_id
):
    col = await add_column(
        table_uuid, name="email", display_name="Email", type="text",
        config={"unique": True},
    )

    async with session_factory() as db:
        table_id = await db.scalar(select(Table.id).where(Table.uuid == table_uuid))

    # Session A holds the table's advisory lock inside an open transaction.
    holder = session_factory()
    await holder.execute(select(func.pg_advisory_xact_lock(_UNIQUE_LOCK_NS, table_id)))

    async def do_create():
        async with session_factory() as db_b:
            table = await TableService.get_by_uuid(db_b, user_id, table_uuid)
            return await TableRecordService.create(
                db_b, table, TableRecordCreate(data={col: "a@b.co"})
            )

    task = asyncio.create_task(do_create())
    try:
        # Give the create ample time to reach _enforce_unique and block on the
        # lock. If the lock were gone, it would finish and task.done() be True.
        await asyncio.sleep(0.5)
        assert not task.done(), "create did not block on the held advisory lock"

        # Releasing the lock (rollback ends the transaction) unblocks the create.
        await holder.rollback()
        record = await asyncio.wait_for(task, timeout=5)
        assert record.data[col] == "a@b.co"
    finally:
        if not task.done():
            task.cancel()
        await holder.close()


async def test_sequential_unique_still_conflicts(client, table_uuid, add_column):
    """Functional companion: a duplicate on a unique column returns 409."""
    col = await add_column(
        table_uuid, name="email", display_name="Email", type="text",
        config={"unique": True},
    )
    first = await client.post(f"/api/tables/{table_uuid}/records/", json={"data": {col: "x@y.co"}})
    assert first.status_code == 201
    second = await client.post(f"/api/tables/{table_uuid}/records/", json={"data": {col: "x@y.co"}})
    assert second.status_code == 409
