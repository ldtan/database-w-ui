"""The two maintenance loops that are bounded by data rather than by schema.

`_references.clear_expired` rewrites a record for every stale reference it holds, and
`sweep_orphan_grants` deletes a grant per share that outlived its resource. Neither is
bounded by anything the schema fixes, and both used to be a single statement over all
of it.

The batching is what these assert. Each test shrinks ``CHUNK`` to two so the loop runs
several times over a handful of rows, and then checks the *last* batch landed — an
unterminated or single-pass loop is what would leave rows behind.
"""
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import func, select, update

from src.auth.users.schemas import UserCreate
from src.auth.users.services import UserService
from src.tables.records.models import TableRecord
from src.maintenance import _references, sweep_orphan_grants
from src.maintenance.purge_deleted_records import purge as purge_items
from src.maintenance.sweep_orphan_grants import sweep
from src.permissions.models import PermissionGrant

ROWS = 5


@pytest.fixture(autouse=True)
def tiny_chunks(monkeypatch):
    monkeypatch.setattr(_references, "CHUNK", 2)
    monkeypatch.setattr(sweep_orphan_grants, "CHUNK", 2)


@pytest_asyncio.fixture
async def alice(session_factory):
    async with session_factory() as db:
        u = await UserService.create(
            db, UserCreate(username="alice", email="a@example.com", password="pw")
        )
        return str(u.uuid)


async def _backdate(session_factory, model, uuid, days=45) -> None:
    async with session_factory() as db:
        await db.execute(
            update(model)
            .where(model.uuid == uuid)
            .values(deleted_at=datetime.now(timezone.utc) - timedelta(days=days))
        )
        await db.commit()


async def _cells(session_factory, key: str) -> list:
    async with session_factory() as db:
        rows = (await db.execute(select(TableRecord.data))).scalars().all()
    return [row.get(key) for row in rows]


async def _grants(session_factory) -> int:
    async with session_factory() as db:
        return await db.scalar(select(func.count()).select_from(PermissionGrant))


@pytest_asyncio.fixture
async def target_row(client, workspace_uuid, add_column):
    """A row on another table, for the lookups below to point at and then lose."""
    target_table = (
        await client.post(
            "/api/tables/",
            json={
                "name": "targets",
                "display_name": "Targets",
                "workspace_uuid": workspace_uuid,
            },
        )
    ).json()["uuid"]
    label = await add_column(
        target_table, name="label", display_name="Label", type="text"
    )
    created = await client.post(
        f"/api/tables/{target_table}/records/", json={"data": {label: "pointed at"}}
    )
    assert created.status_code == 201, created.text
    return target_table, created.json()["uuid"]


async def _expire_target(client, session_factory, target_table, row) -> None:
    """The reference survives the delete — that is what makes it restorable, and what
    lets the cell read as "(deleted)" meanwhile. Only past the window is it cleared."""
    assert (
        await client.delete(f"/api/tables/{target_table}/records/{row}")
    ).status_code == 204
    await _backdate(session_factory, TableRecord, row)


async def test_every_batch_of_stale_lookups_is_cleared(
    client, session_factory, table_uuid, add_column, target_row
):
    """`ROWS` records pointing at one doomed target. The clearing is one column's worth
    of work, but that column can span every record the table has."""
    target_table, row = target_row
    link = await add_column(
        table_uuid,
        name="link",
        display_name="Link",
        type="lookup",
        config={"target_table_uuid": target_table},
    )
    for _ in range(ROWS):
        created = await client.post(
            f"/api/tables/{table_uuid}/records/", json={"data": {link: row}}
        )
        assert created.status_code == 201, created.text

    await _expire_target(client, session_factory, target_table, row)
    await purge_items(days=30)

    # Every one, not the first two.
    assert await _cells(session_factory, link) == [None] * ROWS


async def test_every_batch_of_a_multi_valued_lookup_is_cleared(
    client, session_factory, table_uuid, add_column, target_row
):
    """The array shape of the same rewrite: a cell holding several uuids keeps the ones
    still worth pointing at rather than being nulled. Both branches of the CASE have to
    survive being run a batch at a time."""
    target_table, row = target_row
    link = await add_column(
        table_uuid,
        name="links",
        display_name="Links",
        type="lookup",
        config={"target_table_uuid": target_table, "multiple": True},
    )
    for _ in range(ROWS):
        created = await client.post(
            f"/api/tables/{table_uuid}/records/", json={"data": {link: [row]}}
        )
        assert created.status_code == 201, created.text

    await _expire_target(client, session_factory, target_table, row)
    await purge_items(days=30)

    assert await _cells(session_factory, link) == [[]] * ROWS


async def test_every_batch_of_orphan_grants_is_swept(
    client, session_factory, table_uuid, add_column, alice
):
    """Grants for records that no longer exist at all — what a hard delete leaves if
    something skips the purge path. There is one per share, so a busy table's worth is
    not a small number."""
    column = await add_column(
        table_uuid, name="title", display_name="Title", type="text"
    )
    for n in range(ROWS):
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

    assert await _grants(session_factory) == ROWS

    # Destroy the records out from under the grants, leaving every one an orphan.
    async with session_factory() as db:
        await db.execute(TableRecord.__table__.delete())
        await db.commit()

    assert await sweep(days=30) == ROWS
    assert await _grants(session_factory) == 0


async def test_a_sweep_with_nothing_to_do_stops_immediately(
    client, session_factory, table_uuid, alice
):
    """The loop's exit, on the case that is normal in a healthy database: a grant whose
    resource is alive is not swept, and the first empty select ends the pass."""
    granted = await client.post(
        "/api/permissions/grants",
        json={
            "resource_type": "table",
            "resource_uuid": table_uuid,
            "user_uuid": alice,
            "actions": ["view"],
        },
    )
    assert granted.status_code == 201, granted.text

    assert await sweep(days=30) == 0
    assert await _grants(session_factory) == 1
