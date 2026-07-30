"""Prove the record's row lock actually serializes a read-modify-write.

`update` merges one JSONB blob in Python: it reads the record, builds `merged`
from what it read, and writes the whole thing back. Two writers touching
*different* columns of the same record therefore both merge onto the value they
read before either committed, and whichever commits last silently drops the
other's column. The `UPDATE`'s own row lock is too late to prevent that — by the
time it is taken, `merged` has already been built from the stale read — so the
lock has to be taken at the `SELECT`, which is what `for_update=True` does.

This became reachable in practice with the grid's "Edit cells" mode: the record
dialog sent one write per record, but a cell commit sends one write per *cell*,
so tabbing across a row now fires several overlapping writes at one record. The
defect is older than that feature and applies equally to two dialog saves or two
API clients, which is why `update` locks unconditionally rather than asking who
is calling.

As in test_unique_concurrency, the race cannot be driven through the HTTP stack
— httpx's ASGITransport effectively serializes the requests, so a locked and an
unlocked implementation look identical from outside. Both tests below drive the
service layer directly.

They guard the two halves separately: remove `.with_for_update()` from
`get_by_uuid` and the first fails; stop passing `for_update=True` from `update`
and the second fails (verified both ways, not assumed).
"""
import asyncio

from sqlalchemy import select

from src.lists.items.schemas import ListItemUpdate
from src.lists.items.services import ListItemService
from src.lists.models import List
from src.lists.services import ListService


async def _list_id(session_factory, list_uuid) -> int:
    async with session_factory() as db:
        return await db.scalar(select(List.id).where(List.uuid == list_uuid))


async def test_for_update_locks_the_row_while_a_plain_read_stays_free(
    client, list_uuid, add_column, session_factory
):
    """The `for_update` flag itself: it blocks a second locker, and only that.

    Note this cannot be written as "a concurrent `update` blocks", tempting as
    that is: an unlocked `update` still blocks, just later, because its own
    `UPDATE` takes an exclusive lock anyway. Blocking is therefore not the
    observable difference between locked and unlocked — *what the second writer
    reads* is, which is what the sibling test below measures. What is worth
    pinning here is the flag's two halves, since the default carries a
    performance promise as load-bearing as the lock itself.
    """
    name = await add_column(list_uuid, name="name", display_name="Name", type="text")
    created = await client.post(
        f"/api/lists/{list_uuid}/items/", json={"data": {name: "Ada"}}
    )
    assert created.status_code == 201, created.text
    uuid = created.json()["uuid"]
    list_id = await _list_id(session_factory, list_uuid)

    holder = session_factory()
    locked = await ListItemService.get_by_uuid(
        holder, list_id, uuid, for_update=True
    )
    assert locked is not None

    task = None
    try:
        # The default read is unaffected. This is the half that keeps rendering a
        # page from queueing behind whoever is mid-write.
        async with session_factory() as reader:
            got = await asyncio.wait_for(
                ListItemService.get_by_uuid(reader, list_id, uuid), timeout=2
            )
            assert got is not None and got.data[name] == "Ada"

        # A second locking read waits, which is what serializes two writers.
        async def lock_again():
            async with session_factory() as db_b:
                return await ListItemService.get_by_uuid(
                    db_b, list_id, uuid, for_update=True
                )

        task = asyncio.create_task(lock_again())
        await asyncio.sleep(0.5)
        assert not task.done(), "for_update did not block on the held row lock"

        # Ending the holder's transaction releases the row.
        await holder.rollback()
        assert await asyncio.wait_for(task, timeout=5) is not None
    finally:
        if task is not None and not task.done():
            task.cancel()
        await holder.close()


async def test_concurrent_writes_to_different_cells_both_survive(
    client, list_uuid, add_column, session_factory, user_id
):
    """The guarantee the lock buys, stated as an outcome rather than a wait.

    A edits one column and B another, overlapping. Both changes must be present
    afterwards — the whole point of committing a cell on its own.
    """
    name = await add_column(list_uuid, name="name", display_name="Name", type="text")
    email = await add_column(list_uuid, name="email", display_name="Email", type="text")
    created = await client.post(
        f"/api/lists/{list_uuid}/items/",
        json={"data": {name: "Ada", email: "ada@example.com"}},
    )
    assert created.status_code == 201, created.text
    uuid = created.json()["uuid"]
    list_id = await _list_id(session_factory, list_uuid)

    holder = session_factory()
    try:
        # A reads under the lock and builds its merge, exactly as `update` does.
        item = await ListItemService.get_by_uuid(
            holder, list_id, uuid, for_update=True
        )
        merged = dict(item.data or {})
        merged[email] = "ada@lovelace.org"

        async def patch_name():
            async with session_factory() as db_b:
                lst = await ListService.get_by_uuid(db_b, user_id, list_uuid)
                return await ListItemService.update(
                    db_b, lst, uuid, ListItemUpdate(data={name: "Bob"})
                )

        # B starts while A still holds the row, so an unlocked B would read the
        # pre-A record here and later write its own stale merge over A's column.
        task = asyncio.create_task(patch_name())
        await asyncio.sleep(0.5)

        # A commits its column and releases the row; B then merges onto A's result.
        item.data = merged
        await holder.commit()
        await asyncio.wait_for(task, timeout=5)
    finally:
        await holder.close()

    got = await client.get(f"/api/lists/{list_uuid}/items/{uuid}")
    assert got.status_code == 200, got.text
    data = got.json()["data"]
    assert data[name] == "Bob"
    assert data[email] == "ada@lovelace.org"
