"""Prove the per-list advisory lock actually serializes writes.

Driving two simultaneous inserts through the HTTP/ASGI stack does *not*
reproduce the race — httpx's ASGITransport effectively serializes the requests,
so both a locked and an unlocked implementation yield one success + one
conflict (verified empirically). To test the lock with teeth, we drive the
service layer directly: hold the list's advisory lock in one transaction and
assert that a concurrent `create` blocks on it, then completes once released.
Remove the lock from `_enforce_unique` and this test fails.
"""
import asyncio

from sqlalchemy import func, select

from src.lists.items.schemas import ListItemCreate
from src.lists.items.services import ListItemService, _UNIQUE_LOCK_NS
from src.lists.models import List
from src.lists.services import ListService


async def test_create_blocks_while_list_advisory_lock_is_held(
    list_uuid, add_column, session_factory, user_id
):
    col = await add_column(
        list_uuid, name="email", display_name="Email", type="text",
        config={"unique": True},
    )

    async with session_factory() as db:
        list_id = await db.scalar(select(List.id).where(List.uuid == list_uuid))

    # Session A holds the list's advisory lock inside an open transaction.
    holder = session_factory()
    await holder.execute(select(func.pg_advisory_xact_lock(_UNIQUE_LOCK_NS, list_id)))

    async def do_create():
        async with session_factory() as db_b:
            lst = await ListService.get_by_uuid(db_b, user_id, list_uuid)
            return await ListItemService.create(
                db_b, lst, ListItemCreate(data={col: "a@b.co"})
            )

    task = asyncio.create_task(do_create())
    try:
        # Give the create ample time to reach _enforce_unique and block on the
        # lock. If the lock were gone, it would finish and task.done() be True.
        await asyncio.sleep(0.5)
        assert not task.done(), "create did not block on the held advisory lock"

        # Releasing the lock (rollback ends the transaction) unblocks the create.
        await holder.rollback()
        item = await asyncio.wait_for(task, timeout=5)
        assert item.data[col] == "a@b.co"
    finally:
        if not task.done():
            task.cancel()
        await holder.close()


async def test_sequential_unique_still_conflicts(client, list_uuid, add_column):
    """Functional companion: a duplicate on a unique column returns 409."""
    col = await add_column(
        list_uuid, name="email", display_name="Email", type="text",
        config={"unique": True},
    )
    first = await client.post(f"/api/lists/{list_uuid}/items/", json={"data": {col: "x@y.co"}})
    assert first.status_code == 201
    second = await client.post(f"/api/lists/{list_uuid}/items/", json={"data": {col: "x@y.co"}})
    assert second.status_code == 409
