"""Purging soft-deleted records after the retention window.

Deleting a record only sets `deleted_at`; this job is what eventually makes that
permanent. It is the first thing in the codebase that hard-deletes anything, which
makes it the first thing that has to clean up the grants nothing else does.
"""
from datetime import datetime, timedelta, timezone

import pytest_asyncio
from sqlalchemy import func, select, update

from src.auth.users.schemas import UserCreate
from src.auth.users.services import UserService
from src.tables.records.models import TableRecord
from src.maintenance.purge_deleted_records import purge
from src.permissions.models import PermissionGrant


@pytest_asyncio.fixture
async def alice(session_factory):
    async with session_factory() as db:
        u = await UserService.create(
            db, UserCreate(username="alice", email="a@example.com", password="pw")
        )
        return str(u.uuid)


async def _age(session_factory, uuid: str, days: int) -> None:
    """Backdate a record's deletion, so the retention window can be tested without
    waiting a month."""
    async with session_factory() as db:
        await db.execute(
            update(TableRecord)
            .where(TableRecord.uuid == uuid)
            .values(deleted_at=datetime.now(timezone.utc) - timedelta(days=days))
        )
        await db.commit()


async def _rows(session_factory) -> int:
    async with session_factory() as db:
        return await db.scalar(select(func.count()).select_from(TableRecord))


async def test_only_records_past_the_window_are_destroyed(
    client, session_factory, table_uuid, add_column
):
    col = await add_column(table_uuid, name="n", display_name="N", type="text")
    made = []
    for name in ("live", "fresh-delete", "old-delete"):
        r = await client.post(
            f"/api/tables/{table_uuid}/records/", json={"data": {col: name}}
        )
        made.append(r.json()["uuid"])
    live, fresh, old = made

    await client.delete(f"/api/tables/{table_uuid}/records/{fresh}")
    await client.delete(f"/api/tables/{table_uuid}/records/{old}")
    await _age(session_factory, old, days=45)

    assert await _rows(session_factory) == 3  # nothing is gone yet — only flagged

    planned = await purge(days=30, dry_run=True)
    assert planned == 1  # only the 45-day-old one
    assert await _rows(session_factory) == 3  # a dry run destroys nothing

    assert await purge(days=30) == 1
    assert await _rows(session_factory) == 2  # the old delete is gone for good

    # The live record and the recently-deleted one are both still there: the window
    # is the period in which a delete can still be undone.
    async with session_factory() as db:
        left = (await db.execute(select(TableRecord.uuid))).scalars().all()
    assert {str(u) for u in left} == {live, fresh}


async def test_the_records_grants_go_with_it(
    client, session_factory, table_uuid, add_column, alice
):
    """Nothing else removes these. `permission_grants` points at its resource with no
    foreign key, so a hard delete would leave a grant pointing at an id that is gone —
    the "grants are not scrubbed on hard delete" gap. This job closes it."""
    col = await add_column(table_uuid, name="n", display_name="N", type="text")
    record = (
        await client.post(f"/api/tables/{table_uuid}/records/", json={"data": {col: "x"}})
    ).json()["uuid"]
    await client.patch(
        f"/api/tables/{table_uuid}/records/{record}", json={"permissions_inherit": False}
    )
    granted = await client.post(
        "/api/permissions/grants",
        json={
            "resource_type": "record",
            "resource_uuid": record,
            "user_uuid": alice,
            "actions": ["view"],
        },
    )
    assert granted.status_code == 201, granted.text

    await client.delete(f"/api/tables/{table_uuid}/records/{record}")
    await _age(session_factory, record, days=45)

    async with session_factory() as db:
        assert await db.scalar(select(func.count()).select_from(PermissionGrant)) == 1

    assert await purge(days=30) == 1

    async with session_factory() as db:
        assert await db.scalar(select(func.count()).select_from(PermissionGrant)) == 0


async def test_a_zero_day_window_purges_everything_deleted(
    client, session_factory, table_uuid, add_column
):
    """`--days 0` means "empty the bin now" — the escape hatch for someone who needs
    the data gone rather than merely hidden."""
    col = await add_column(table_uuid, name="n", display_name="N", type="text")
    record = (
        await client.post(f"/api/tables/{table_uuid}/records/", json={"data": {col: "x"}})
    ).json()["uuid"]
    await client.delete(f"/api/tables/{table_uuid}/records/{record}")

    assert await purge(days=0) == 1
    assert await _rows(session_factory) == 0
