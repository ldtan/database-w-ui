"""The two parts of a purge that are sized by the records, batched (src.lists.purge).

`drain_records` for destroying a list, `strip_column_keys` for destroying a column
while its records stay. Six cascades between them, all covered here.

Every test shrinks ``CHUNK`` rather than creating five thousand records: what matters
is that the loop runs more than once and finishes, not the number it runs at.

The flag each loop is called with is the point, and it is not a preference — see the
`committed_as_they_go` / `are_not` pairs.
"""
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import func, select, update

from src.auth.users.schemas import UserCreate
from src.auth.users.services import UserService
from src.lists import purge as purge_module
from src.lists.columns.models import Column
from src.lists.forms.models import Form
from src.lists.items.models import ListItem
from src.lists.metrics.models import Metric
from src.lists.models import List
from src.lists.purge import drain_records, strip_column_keys
from src.lists.views.models import View
from src.maintenance.purge_deleted_list_config import purge as purge_config
from src.maintenance.purge_deleted_lists import purge as purge_lists
from src.maintenance.purge_deleted_workspaces import purge as purge_workspaces
from src.permissions.enums import ResourceType
from src.permissions.models import PermissionGrant
from src.workspaces.models import Workspace

RECORDS = 5


@pytest.fixture(autouse=True)
def tiny_chunk(monkeypatch):
    """Two per batch, so five records take three passes and the loop's exit is real."""
    monkeypatch.setattr(purge_module, "CHUNK", 2)


@pytest_asyncio.fixture
async def alice(session_factory):
    async with session_factory() as db:
        u = await UserService.create(
            db, UserCreate(username="alice", email="a@example.com", password="pw")
        )
        return str(u.uuid)


@pytest_asyncio.fixture
async def filled_list(client, list_uuid, add_column, alice):
    """A list holding `RECORDS` records, every one of them with a grant of its own.

    The grants matter as much as the rows: `permission_grants` has no foreign key, and
    the statement that used to collect them (`resource_id IN (SELECT id FROM
    list_items ...)`) stops matching once the first batch is deleted. Each batch has to
    take its own.
    """
    column = await add_column(
        list_uuid, name="title", display_name="Title", type="text"
    )
    for n in range(RECORDS):
        created = await client.post(
            f"/api/lists/{list_uuid}/items/", json={"data": {column: f"row {n}"}}
        )
        assert created.status_code == 201, created.text
        granted = await client.post(
            "/api/permissions/grants",
            json={
                "resource_type": "item",
                "resource_uuid": created.json()["uuid"],
                "user_uuid": alice,
                "actions": ["view"],
            },
        )
        assert granted.status_code == 201, granted.text
    return list_uuid


async def _count(session_factory, model) -> int:
    async with session_factory() as db:
        return await db.scalar(select(func.count()).select_from(model))


async def _item_grants(session_factory) -> int:
    async with session_factory() as db:
        return await db.scalar(
            select(func.count())
            .select_from(PermissionGrant)
            .where(PermissionGrant.resource_type == ResourceType.item)
        )


async def _list_id(session_factory, uuid: str) -> int:
    async with session_factory() as db:
        return await db.scalar(select(List.id).where(List.uuid == uuid))


async def _workspace_gone(session_factory, uuid: str) -> bool:
    """By uuid, not by count — the seeded user already owns a personal workspace that
    none of this touches."""
    async with session_factory() as db:
        return await db.scalar(
            select(Workspace.id).where(Workspace.uuid == uuid)
        ) is None


async def _age_everything(session_factory, days: int) -> None:
    """Backdate every soft-deleted row so the retention window is behind it."""
    when = datetime.now(timezone.utc) - timedelta(days=days)
    async with session_factory() as db:
        for model in (Workspace, List, Column, ListItem, View, Form, Metric):
            await db.execute(
                update(model)
                .where(model.deleted_at.is_not(None))
                .values(deleted_at=when)
            )
        await db.commit()


async def test_the_list_job_drains_every_batch_and_its_grants(
    client, session_factory, filled_list
):
    assert await _count(session_factory, ListItem) == RECORDS
    assert await _item_grants(session_factory) == RECORDS

    assert (await client.delete(f"/api/lists/{filled_list}")).status_code == 204
    await _age_everything(session_factory, days=45)

    assert await purge_lists(days=30) == 1

    # Not "most of them": the loop stops when the select comes back empty, not after
    # a fixed number of passes.
    assert await _count(session_factory, ListItem) == 0
    assert await _item_grants(session_factory) == 0
    assert await _count(session_factory, List) == 0


async def test_the_workspace_job_drains_every_batch_and_its_grants(
    client, session_factory, workspace_uuid, filled_list
):
    assert (
        await client.delete(f"/api/workspaces/{workspace_uuid}")
    ).status_code == 204
    await _age_everything(session_factory, days=45)

    assert await purge_workspaces(days=30) == 1

    assert await _count(session_factory, ListItem) == 0
    assert await _item_grants(session_factory) == 0
    assert await _count(session_factory, List) == 0
    assert await _workspace_gone(session_factory, workspace_uuid)


async def test_the_lists_permanent_route_drains_every_batch_and_its_grants(
    client, session_factory, filled_list
):
    assert (await client.delete(f"/api/lists/{filled_list}")).status_code == 204
    assert (
        await client.delete(f"/api/lists/{filled_list}/permanent")
    ).status_code == 204

    assert await _count(session_factory, ListItem) == 0
    assert await _item_grants(session_factory) == 0
    assert await _count(session_factory, List) == 0


async def test_the_workspaces_permanent_route_drains_every_batch_and_its_grants(
    client, session_factory, workspace_uuid, filled_list
):
    assert (
        await client.delete(f"/api/workspaces/{workspace_uuid}")
    ).status_code == 204
    assert (
        await client.delete(f"/api/workspaces/{workspace_uuid}/permanent")
    ).status_code == 204

    assert await _count(session_factory, ListItem) == 0
    assert await _item_grants(session_factory) == 0
    assert await _count(session_factory, List) == 0
    assert await _workspace_gone(session_factory, workspace_uuid)


async def test_a_jobs_batches_are_committed_as_they_go(session_factory, filled_list):
    """What "safe to kill and re-run" rests on. A job's rows are already past the
    retention window, which `get_deleted_by_uuid` will not hand back, so a half-drained
    list is unreachable rather than restorable-and-short — and the work done so far is
    worth keeping."""
    list_id = await _list_id(session_factory, filled_list)

    async with session_factory() as db:
        assert await drain_records(db, [list_id], commit=True) == RECORDS
        await db.rollback()  # nothing left to roll back

    assert await _count(session_factory, ListItem) == 0
    assert await _item_grants(session_factory) == 0


async def test_a_routes_batches_are_not(session_factory, filled_list):
    """And the mirror of it, which is the whole reason the flag exists.

    A list is only purgeable while it is still *inside* the window — which is exactly
    while `restore` still works on it. If the route committed per batch, an interrupted
    purge would leave a list that comes back missing records and nothing to say so. So
    the batches stay in the caller's transaction and a failure takes all of them back.
    """
    list_id = await _list_id(session_factory, filled_list)

    async with session_factory() as db:
        assert await drain_records(db, [list_id], commit=False) == RECORDS
        # Stands in for anything later in the cascade failing.
        await db.rollback()

    assert await _count(session_factory, ListItem) == RECORDS
    assert await _item_grants(session_factory) == RECORDS


async def test_draining_nothing_is_not_a_query(session_factory):
    """A workspace with no lists reaches this with an empty sequence, and `ANY(:ids)`
    on an empty array would scan for nothing at all."""
    async with session_factory() as db:
        assert await drain_records(db, [], commit=True) == 0


async def test_live_records_go_too(client, session_factory, filled_list):
    """The drain is keyed on the list, not on `deleted_at`. A record binned on its own
    and one that never was are both going: the list they belong to is being destroyed.
    """
    async with session_factory() as db:
        one = await db.scalar(select(ListItem.uuid).limit(1))
    assert (
        await client.delete(f"/api/lists/{filled_list}/items/{one}")
    ).status_code == 204

    async with session_factory() as db:
        # One in the bin, the rest live.
        assert await db.scalar(
            select(func.count())
            .select_from(ListItem)
            .where(ListItem.deleted_at.is_(None))
        ) == RECORDS - 1

    list_id = await _list_id(session_factory, filled_list)
    async with session_factory() as db:
        assert await drain_records(db, [list_id], commit=True) == RECORDS

    assert await _count(session_factory, ListItem) == 0


async def test_the_column_job_strips_every_batch(
    client, session_factory, list_uuid, add_column
):
    """The same hazard one level down: 5,000 columns is a small batch until one of
    their lists holds a million records, and every one of those gets rewritten."""
    doomed = await add_column(
        list_uuid, name="doomed", display_name="Doomed", type="text"
    )
    keeper = await add_column(
        list_uuid, name="keeper", display_name="Keeper", type="text"
    )
    for n in range(RECORDS):
        created = await client.post(
            f"/api/lists/{list_uuid}/items/",
            json={"data": {doomed: f"gone {n}", keeper: f"stays {n}"}},
        )
        assert created.status_code == 201, created.text

    assert (
        await client.delete(f"/api/lists/{list_uuid}/columns/{doomed}")
    ).status_code == 204
    await _age_everything(session_factory, days=45)

    assert await purge_config(days=30) == 1

    # Every record, not the first batch's worth.
    async with session_factory() as db:
        rows = (await db.execute(select(ListItem.data))).scalars().all()
    assert len(rows) == RECORDS
    assert all(doomed not in data for data in rows)
    assert all(data[keeper].startswith("stays") for data in rows)


async def test_two_doomed_columns_of_one_list_both_go_in_one_batch(
    client, session_factory, list_uuid, add_column
):
    """Why the keys are subtracted as an array. The join this replaced picked a single
    partner per record, so a record holding both lost only one of them."""
    first = await add_column(list_uuid, name="a", display_name="A", type="text")
    second = await add_column(list_uuid, name="b", display_name="B", type="text")
    keeper = await add_column(list_uuid, name="c", display_name="C", type="text")
    for n in range(RECORDS):
        created = await client.post(
            f"/api/lists/{list_uuid}/items/",
            json={"data": {first: "a", second: "b", keeper: "c"}},
        )
        assert created.status_code == 201, created.text

    for column in (first, second):
        assert (
            await client.delete(f"/api/lists/{list_uuid}/columns/{column}")
        ).status_code == 204
    await _age_everything(session_factory, days=45)

    assert await purge_config(days=30) == 2

    async with session_factory() as db:
        rows = (await db.execute(select(ListItem.data))).scalars().all()
    assert all(first not in data and second not in data for data in rows)
    assert all(data[keeper] == "c" for data in rows)


async def test_the_columns_permanent_route_strips_every_batch(
    client, session_factory, list_uuid, add_column
):
    doomed = await add_column(
        list_uuid, name="doomed", display_name="Doomed", type="text"
    )
    for n in range(RECORDS):
        created = await client.post(
            f"/api/lists/{list_uuid}/items/", json={"data": {doomed: f"gone {n}"}}
        )
        assert created.status_code == 201, created.text

    assert (
        await client.delete(f"/api/lists/{list_uuid}/columns/{doomed}")
    ).status_code == 204
    assert (
        await client.delete(f"/api/lists/{list_uuid}/columns/{doomed}/permanent")
    ).status_code == 204

    async with session_factory() as db:
        rows = (await db.execute(select(ListItem.data))).scalars().all()
    assert len(rows) == RECORDS
    assert all(doomed not in data for data in rows)


async def test_a_column_strips_batches_are_committed_or_not_by_the_same_rule(
    client, session_factory, list_uuid, add_column
):
    """Both halves of the flag in one place, since the setup is the same.

    A binned column is restorable right up until its permanent delete succeeds, so the
    route's batches have to roll back together; the job's rows are past the window and
    unreachable, so its batches are worth keeping.
    """
    column = await add_column(
        list_uuid, name="doomed", display_name="Doomed", type="text"
    )
    for n in range(RECORDS):
        created = await client.post(
            f"/api/lists/{list_uuid}/items/", json={"data": {column: f"value {n}"}}
        )
        assert created.status_code == 201, created.text
    list_id = await _list_id(session_factory, list_uuid)

    # The route's rule: a failure anywhere takes every batch back with it.
    async with session_factory() as db:
        assert await strip_column_keys(db, list_id, [column], commit=False) == RECORDS
        await db.rollback()
    async with session_factory() as db:
        rows = (await db.execute(select(ListItem.data))).scalars().all()
    assert all(data[column].startswith("value") for data in rows)

    # The job's rule: committed as they go, so a kill loses none of the work.
    async with session_factory() as db:
        assert await strip_column_keys(db, list_id, [column], commit=True) == RECORDS
        await db.rollback()
    async with session_factory() as db:
        rows = (await db.execute(select(ListItem.data))).scalars().all()
    assert all(column not in data for data in rows)


async def test_stripping_no_keys_is_not_a_query(session_factory, list_uuid):
    """A batch of views or forms reaches the job with nothing to strip."""
    list_id = await _list_id(session_factory, list_uuid)
    async with session_factory() as db:
        assert await strip_column_keys(db, list_id, [], commit=True) == 0


async def test_the_batch_loop_leaves_another_lists_records_alone(
    client, session_factory, workspace_uuid, filled_list, add_column
):
    """`ANY(:list_ids)` is the only thing scoping this, and the loop re-selects rather
    than paging — so a bug in either would show up as somebody else's rows going."""
    other = (
        await client.post(
            "/api/lists/",
            json={
                "name": "keeper",
                "display_name": "Keeper",
                "workspace_uuid": workspace_uuid,
            },
        )
    ).json()["uuid"]
    column = await add_column(other, name="title", display_name="Title", type="text")
    kept = await client.post(
        f"/api/lists/{other}/items/", json={"data": {column: "safe"}}
    )
    assert kept.status_code == 201, kept.text

    list_id = await _list_id(session_factory, filled_list)
    async with session_factory() as db:
        assert await drain_records(db, [list_id], commit=True) == RECORDS

    assert await _count(session_factory, ListItem) == 1
    async with session_factory() as db:
        survivor = await db.scalar(select(ListItem.uuid))
    assert str(survivor) == kept.json()["uuid"]
