"""Records outlive the `multiple` setting that shaped them.

Toggling a column's `multiple` does not rewrite its records — on a large list that
is a multi-minute UPDATE holding locks on every row, inside a web request. So the
service tolerates both shapes at its edges, the scrub stops caring about shape at
all, and a scheduled job converges the data afterwards. These tests pin all three.
"""
import pytest_asyncio
from sqlalchemy import select

from src.auth.users.schemas import UserCreate
from src.auth.users.services import UserService
from src.lists.items.models import ListItem
from src.maintenance.reconcile_shapes import reconcile


@pytest_asyncio.fixture
async def two_users(session_factory):
    async with session_factory() as db:
        a = await UserService.create(
            db, UserCreate(username="alice", email="a@example.com", password="pw")
        )
        b = await UserService.create(
            db, UserCreate(username="bob", email="b@example.com", password="pw")
        )
        return str(a.uuid), str(b.uuid)


async def _multi_column(client, list_uuid) -> str:
    resp = await client.post(
        f"/api/lists/{list_uuid}/columns/",
        json={
            "name": "reviewers",
            "display_name": "Reviewers",
            "type": "user",
            "config": {"multiple": True},
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["uuid"]


async def _stored(session_factory, col: str):
    """The raw JSONB, bypassing the API's shape coercion."""
    async with session_factory() as db:
        row = (await db.execute(select(ListItem.data))).scalars().first()
    return row.get(col)


async def test_toggling_leaves_the_records_alone(
    client, session_factory, list_uuid, two_users
):
    """The point of the whole design: the toggle is O(1). Whatever the list's size,
    it changes one config row and touches no records."""
    alice, bob = two_users
    col = await _multi_column(client, list_uuid)
    await client.post(
        f"/api/lists/{list_uuid}/items/", json={"data": {col: [alice, bob]}}
    )

    await client.patch(
        f"/api/lists/{list_uuid}/columns/{col}", json={"config": {"multiple": False}}
    )

    assert await _stored(session_factory, col) == [alice, bob]  # untouched on disk


async def test_reads_come_back_in_the_columns_shape(
    client, session_factory, list_uuid, two_users
):
    """Every reader sees the declared shape — coerced once, in the service, so no
    consumer has to know the record is a shape behind."""
    alice, bob = two_users
    col = await _multi_column(client, list_uuid)
    created = await client.post(
        f"/api/lists/{list_uuid}/items/", json={"data": {col: [alice, bob]}}
    )
    item = created.json()["uuid"]

    await client.patch(
        f"/api/lists/{list_uuid}/columns/{col}", json={"config": {"multiple": False}}
    )

    listed = (await client.get(f"/api/lists/{list_uuid}/items/")).json()
    assert listed[0]["data"][col] == alice  # the first value, not ["alice", "bob"]

    one = (await client.get(f"/api/lists/{list_uuid}/items/{item}")).json()
    assert one["data"][col] == alice

    csv = (
        await client.post(f"/api/lists/{list_uuid}/export", json={"format": "csv"})
    ).text
    assert "alice" in csv and "bob" not in csv  # export coerces too

    # And the other direction: a bare value on a now-multiple column reads as a list.
    await client.patch(
        f"/api/lists/{list_uuid}/columns/{col}", json={"config": {"multiple": True}}
    )
    listed = (await client.get(f"/api/lists/{list_uuid}/items/")).json()
    assert listed[0]["data"][col] == [alice, bob]  # still both — nothing was destroyed


async def test_writing_back_a_legacy_value_collapses_it(
    client, session_factory, list_uuid, two_users
):
    """A client that echoes the record it was given must not be told its own data is
    invalid. The write edge coerces, so the array becomes the first value."""
    alice, bob = two_users
    col = await _multi_column(client, list_uuid)
    created = await client.post(
        f"/api/lists/{list_uuid}/items/", json={"data": {col: [alice, bob]}}
    )
    item = created.json()["uuid"]
    await client.patch(
        f"/api/lists/{list_uuid}/columns/{col}", json={"config": {"multiple": False}}
    )

    echoed = await client.patch(
        f"/api/lists/{list_uuid}/items/{item}", json={"data": {col: [alice, bob]}}
    )
    assert echoed.status_code == 200, echoed.text
    assert echoed.json()["data"][col] == alice
    assert await _stored(session_factory, col) == alice  # written in the new shape


async def test_the_scrub_no_longer_cares_about_shape(
    client, session_factory, list_uuid, two_users
):
    """The bug this design could have reintroduced: with records a shape behind, a
    scrub that picks its SQL from the column's current `multiple` matches nothing and
    leaves the deleted user's uuid behind. It no longer asks."""
    alice, bob = two_users
    col = await _multi_column(client, list_uuid)
    await client.post(
        f"/api/lists/{list_uuid}/items/", json={"data": {col: [alice, bob]}}
    )
    await client.patch(  # records now hold an array; the column says single
        f"/api/lists/{list_uuid}/columns/{col}", json={"config": {"multiple": False}}
    )

    async with session_factory() as db:
        assert await UserService.delete(db, alice) is True

    # Scrubbed out of the array on disk, even though the column says single.
    assert await _stored(session_factory, col) == [bob]


async def test_reconcile_converges_the_stored_shape(
    client, session_factory, list_uuid, two_users
):
    alice, bob = two_users
    col = await _multi_column(client, list_uuid)
    await client.post(
        f"/api/lists/{list_uuid}/items/", json={"data": {col: [alice, bob]}}
    )
    await client.patch(
        f"/api/lists/{list_uuid}/columns/{col}", json={"config": {"multiple": False}}
    )

    planned = await reconcile(dry_run=True)
    assert sum(planned.values()) == 1  # one record out of shape
    assert await _stored(session_factory, col) == [alice, bob]  # dry run changes nothing

    changed = await reconcile()
    assert sum(changed.values()) == 1
    assert await _stored(session_factory, col) == alice  # converged, extras dropped

    assert await reconcile() == {}  # idempotent: nothing left to do
