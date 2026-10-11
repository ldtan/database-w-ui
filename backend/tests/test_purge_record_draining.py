"""The two parts of a purge that are sized by the records, batched (src.tables.purge).

`drain_records` for destroying a table, `strip_column_keys` for destroying a column
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
from src.tables import purge as purge_module
from src.tables.columns.models import Column
from src.tables.forms.models import Form
from src.tables.records.models import TableRecord
from src.tables.metrics.models import Metric
from src.tables.models import Table
from src.tables.purge import drain_records, strip_column_keys
from src.tables.views.models import View
from src.maintenance.purge_deleted_table_config import purge as purge_config
from src.maintenance.purge_deleted_tables import purge as purge_tables
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
async def filled_table(client, table_uuid, add_column, alice):
    """A table holding `RECORDS` records, every one of them with a grant of its own.

    The grants matter as much as the rows: `permission_grants` has no foreign key, and
    the statement that used to collect them (`resource_id IN (SELECT id FROM
    table_records ...)`) stops matching once the first batch is deleted. Each batch has to
    take its own.
    """
    column = await add_column(
        table_uuid, name="title", display_name="Title", type="text"
    )
    for n in range(RECORDS):
        created = await client.post(
            f"/api/tables/{table_uuid}/records/", json={"data": {column: f"row {n}"}}
        )
        assert created.status_code == 201, created.text
        granted = await client.post(
            "/api/permissions/grants",
            json={
                "resource_type": "record",
                "resource_uuid": created.json()["uuid"],
                "user_uuid": alice,
                "actions": ["view"],
            },
        )
        assert granted.status_code == 201, granted.text
    return table_uuid


async def _count(session_factory, model) -> int:
    async with session_factory() as db:
        return await db.scalar(select(func.count()).select_from(model))


async def _item_grants(session_factory) -> int:
    async with session_factory() as db:
        return await db.scalar(
            select(func.count())
            .select_from(PermissionGrant)
            .where(PermissionGrant.resource_type == ResourceType.record)
        )


async def _table_id(session_factory, uuid: str) -> int:
    async with session_factory() as db:
        return await db.scalar(select(Table.id).where(Table.uuid == uuid))


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
        for model in (Workspace, Table, Column, TableRecord, View, Form, Metric):
            await db.execute(
                update(model)
                .where(model.deleted_at.is_not(None))
                .values(deleted_at=when)
            )
        await db.commit()


async def test_the_table_job_drains_every_batch_and_its_grants(
    client, session_factory, filled_table
):
    assert await _count(session_factory, TableRecord) == RECORDS
    assert await _item_grants(session_factory) == RECORDS

    assert (await client.delete(f"/api/tables/{filled_table}")).status_code == 204
    await _age_everything(session_factory, days=45)

    assert await purge_tables(days=30) == 1

    # Not "most of them": the loop stops when the select comes back empty, not after
    # a fixed number of passes.
    assert await _count(session_factory, TableRecord) == 0
    assert await _item_grants(session_factory) == 0
    assert await _count(session_factory, Table) == 0


async def test_the_workspace_job_drains_every_batch_and_its_grants(
    client, session_factory, workspace_uuid, filled_table
):
    assert (
        await client.delete(f"/api/workspaces/{workspace_uuid}")
    ).status_code == 204
    await _age_everything(session_factory, days=45)

    assert await purge_workspaces(days=30) == 1

    assert await _count(session_factory, TableRecord) == 0
    assert await _item_grants(session_factory) == 0
    assert await _count(session_factory, Table) == 0
    assert await _workspace_gone(session_factory, workspace_uuid)


async def test_the_tables_permanent_route_drains_every_batch_and_its_grants(
    client, session_factory, filled_table
):
    assert (await client.delete(f"/api/tables/{filled_table}")).status_code == 204
    assert (
        await client.delete(f"/api/tables/{filled_table}/permanent")
    ).status_code == 204

    assert await _count(session_factory, TableRecord) == 0
    assert await _item_grants(session_factory) == 0
    assert await _count(session_factory, Table) == 0


async def test_the_workspaces_permanent_route_drains_every_batch_and_its_grants(
    client, session_factory, workspace_uuid, filled_table
):
    assert (
        await client.delete(f"/api/workspaces/{workspace_uuid}")
    ).status_code == 204
    assert (
        await client.delete(f"/api/workspaces/{workspace_uuid}/permanent")
    ).status_code == 204

    assert await _count(session_factory, TableRecord) == 0
    assert await _item_grants(session_factory) == 0
    assert await _count(session_factory, Table) == 0
    assert await _workspace_gone(session_factory, workspace_uuid)


async def test_a_jobs_batches_are_committed_as_they_go(session_factory, filled_table):
    """What "safe to kill and re-run" rests on. A job's rows are already past the
    retention window, which `get_deleted_by_uuid` will not hand back, so a half-drained
    table is unreachable rather than restorable-and-short — and the work done so far is
    worth keeping."""
    table_id = await _table_id(session_factory, filled_table)

    async with session_factory() as db:
        assert await drain_records(db, [table_id], commit=True) == RECORDS
        await db.rollback()  # nothing left to roll back

    assert await _count(session_factory, TableRecord) == 0
    assert await _item_grants(session_factory) == 0


async def test_a_routes_batches_are_not(session_factory, filled_table):
    """And the mirror of it, which is the whole reason the flag exists.

    A table is only purgeable while it is still *inside* the window — which is exactly
    while `restore` still works on it. If the route committed per batch, an interrupted
    purge would leave a table that comes back missing records and nothing to say so. So
    the batches stay in the caller's transaction and a failure takes all of them back.
    """
    table_id = await _table_id(session_factory, filled_table)

    async with session_factory() as db:
        assert await drain_records(db, [table_id], commit=False) == RECORDS
        # Stands in for anything later in the cascade failing.
        await db.rollback()

    assert await _count(session_factory, TableRecord) == RECORDS
    assert await _item_grants(session_factory) == RECORDS


async def test_draining_nothing_is_not_a_query(session_factory):
    """A workspace with no tables reaches this with an empty sequence, and `ANY(:ids)`
    on an empty array would scan for nothing at all."""
    async with session_factory() as db:
        assert await drain_records(db, [], commit=True) == 0


async def test_live_records_go_too(client, session_factory, filled_table):
    """The drain is keyed on the table, not on `deleted_at`. A record binned on its own
    and one that never was are both going: the table they belong to is being destroyed.
    """
    async with session_factory() as db:
        one = await db.scalar(select(TableRecord.uuid).limit(1))
    assert (
        await client.delete(f"/api/tables/{filled_table}/records/{one}")
    ).status_code == 204

    async with session_factory() as db:
        # One in the bin, the rest live.
        assert await db.scalar(
            select(func.count())
            .select_from(TableRecord)
            .where(TableRecord.deleted_at.is_(None))
        ) == RECORDS - 1

    table_id = await _table_id(session_factory, filled_table)
    async with session_factory() as db:
        assert await drain_records(db, [table_id], commit=True) == RECORDS

    assert await _count(session_factory, TableRecord) == 0


async def test_the_column_job_strips_every_batch(
    client, session_factory, table_uuid, add_column
):
    """The same hazard one level down: 5,000 columns is a small batch until one of
    their tables holds a million records, and every one of those gets rewritten."""
    doomed = await add_column(
        table_uuid, name="doomed", display_name="Doomed", type="text"
    )
    keeper = await add_column(
        table_uuid, name="keeper", display_name="Keeper", type="text"
    )
    for n in range(RECORDS):
        created = await client.post(
            f"/api/tables/{table_uuid}/records/",
            json={"data": {doomed: f"gone {n}", keeper: f"stays {n}"}},
        )
        assert created.status_code == 201, created.text

    assert (
        await client.delete(f"/api/tables/{table_uuid}/columns/{doomed}")
    ).status_code == 204
    await _age_everything(session_factory, days=45)

    assert await purge_config(days=30) == 1

    # Every record, not the first batch's worth.
    async with session_factory() as db:
        rows = (await db.execute(select(TableRecord.data))).scalars().all()
    assert len(rows) == RECORDS
    assert all(doomed not in data for data in rows)
    assert all(data[keeper].startswith("stays") for data in rows)


async def test_two_doomed_columns_of_one_table_both_go_in_one_batch(
    client, session_factory, table_uuid, add_column
):
    """Why the keys are subtracted as an array. The join this replaced picked a single
    partner per record, so a record holding both lost only one of them."""
    first = await add_column(table_uuid, name="a", display_name="A", type="text")
    second = await add_column(table_uuid, name="b", display_name="B", type="text")
    keeper = await add_column(table_uuid, name="c", display_name="C", type="text")
    for n in range(RECORDS):
        created = await client.post(
            f"/api/tables/{table_uuid}/records/",
            json={"data": {first: "a", second: "b", keeper: "c"}},
        )
        assert created.status_code == 201, created.text

    for column in (first, second):
        assert (
            await client.delete(f"/api/tables/{table_uuid}/columns/{column}")
        ).status_code == 204
    await _age_everything(session_factory, days=45)

    assert await purge_config(days=30) == 2

    async with session_factory() as db:
        rows = (await db.execute(select(TableRecord.data))).scalars().all()
    assert all(first not in data and second not in data for data in rows)
    assert all(data[keeper] == "c" for data in rows)


async def test_the_columns_permanent_route_strips_every_batch(
    client, session_factory, table_uuid, add_column
):
    doomed = await add_column(
        table_uuid, name="doomed", display_name="Doomed", type="text"
    )
    for n in range(RECORDS):
        created = await client.post(
            f"/api/tables/{table_uuid}/records/", json={"data": {doomed: f"gone {n}"}}
        )
        assert created.status_code == 201, created.text

    assert (
        await client.delete(f"/api/tables/{table_uuid}/columns/{doomed}")
    ).status_code == 204
    assert (
        await client.delete(f"/api/tables/{table_uuid}/columns/{doomed}/permanent")
    ).status_code == 204

    async with session_factory() as db:
        rows = (await db.execute(select(TableRecord.data))).scalars().all()
    assert len(rows) == RECORDS
    assert all(doomed not in data for data in rows)


async def test_a_column_strips_batches_are_committed_or_not_by_the_same_rule(
    client, session_factory, table_uuid, add_column
):
    """Both halves of the flag in one place, since the setup is the same.

    A binned column is restorable right up until its permanent delete succeeds, so the
    route's batches have to roll back together; the job's rows are past the window and
    unreachable, so its batches are worth keeping.
    """
    column = await add_column(
        table_uuid, name="doomed", display_name="Doomed", type="text"
    )
    for n in range(RECORDS):
        created = await client.post(
            f"/api/tables/{table_uuid}/records/", json={"data": {column: f"value {n}"}}
        )
        assert created.status_code == 201, created.text
    table_id = await _table_id(session_factory, table_uuid)

    # The route's rule: a failure anywhere takes every batch back with it.
    async with session_factory() as db:
        assert await strip_column_keys(db, table_id, [column], commit=False) == RECORDS
        await db.rollback()
    async with session_factory() as db:
        rows = (await db.execute(select(TableRecord.data))).scalars().all()
    assert all(data[column].startswith("value") for data in rows)

    # The job's rule: committed as they go, so a kill loses none of the work.
    async with session_factory() as db:
        assert await strip_column_keys(db, table_id, [column], commit=True) == RECORDS
        await db.rollback()
    async with session_factory() as db:
        rows = (await db.execute(select(TableRecord.data))).scalars().all()
    assert all(column not in data for data in rows)


async def test_stripping_no_keys_is_not_a_query(session_factory, table_uuid):
    """A batch of views or forms reaches the job with nothing to strip."""
    table_id = await _table_id(session_factory, table_uuid)
    async with session_factory() as db:
        assert await strip_column_keys(db, table_id, [], commit=True) == 0


async def test_the_batch_loop_leaves_another_tables_records_alone(
    client, session_factory, workspace_uuid, filled_table, add_column
):
    """`ANY(:table_ids)` is the only thing scoping this, and the loop re-selects rather
    than paging — so a bug in either would show up as somebody else's rows going."""
    other = (
        await client.post(
            "/api/tables/",
            json={
                "name": "keeper",
                "display_name": "Keeper",
                "workspace_uuid": workspace_uuid,
            },
        )
    ).json()["uuid"]
    column = await add_column(other, name="title", display_name="Title", type="text")
    kept = await client.post(
        f"/api/tables/{other}/records/", json={"data": {column: "safe"}}
    )
    assert kept.status_code == 201, kept.text

    table_id = await _table_id(session_factory, filled_table)
    async with session_factory() as db:
        assert await drain_records(db, [table_id], commit=True) == RECORDS

    assert await _count(session_factory, TableRecord) == 1
    async with session_factory() as db:
        survivor = await db.scalar(select(TableRecord.uuid))
    assert str(survivor) == kept.json()["uuid"]
