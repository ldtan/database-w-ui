"""Soft delete destroys nothing; purge destroys everything.

The scrub used to run at delete time, which quietly made a soft delete irreversible:
the pointers into the deleted thing were destroyed and nothing remembered them, so a
restore would bring the record back stripped of every inbound relationship. Now the
references survive the delete, render as "(deleted)", and are cleared only when the
target is purged — by which point it is unrecoverable anyway.
"""
from datetime import datetime, timedelta, timezone

import pytest_asyncio
from sqlalchemy import select, update

from src.auth.users.schemas import UserCreate
from src.auth.users.services import UserService
from src.tables.records.models import TableRecord
from src.maintenance.purge_deleted_records import purge
from src.maintenance.purge_deleted_principals import purge_principals


@pytest_asyncio.fixture
async def alice(session_factory):
    async with session_factory() as db:
        u = await UserService.create(
            db, UserCreate(username="alice", email="a@example.com", password="pw")
        )
        return str(u.uuid)


async def _age_item(session_factory, uuid, days):
    async with session_factory() as db:
        await db.execute(
            update(TableRecord)
            .where(TableRecord.uuid == uuid)
            .values(deleted_at=datetime.now(timezone.utc) - timedelta(days=days))
        )
        await db.commit()


async def _cell(session_factory, record_uuid, key):
    async with session_factory() as db:
        data = (
            await db.execute(
                select(TableRecord.data).where(TableRecord.uuid == record_uuid)
            )
        ).scalars().first()
    return (data or {}).get(key)


async def _target(client, workspace_uuid, add_column):
    table = (
        await client.post(
            "/api/tables/",
            json={"name": "schools", "display_name": "Schools",
                  "workspace_uuid": workspace_uuid},
        )
    ).json()["uuid"]
    label = await add_column(table, name="name", display_name="Name", type="text")
    row = (
        await client.post(
            f"/api/tables/{table}/records/", json={"data": {label: "Springfield"}}
        )
    ).json()["uuid"]
    return table, row


async def test_a_deleted_row_keeps_its_inbound_reference_and_stays_labelled(
    client, session_factory, workspace_uuid, table_uuid, add_column
):
    target, row = await _target(client, workspace_uuid, add_column)
    lookup = await add_column(
        table_uuid, name="school", display_name="School", type="lookup",
        config={"target_table_uuid": target},
    )
    record = (
        await client.post(
            f"/api/tables/{table_uuid}/records/", json={"data": {lookup: row}}
        )
    ).json()["uuid"]

    await client.delete(f"/api/tables/{target}/records/{row}")

    # The relationship survives the delete.
    assert await _cell(session_factory, record, lookup) == row

    # And it can still be named — flagged deleted, so the cell can say so.
    resolved = (
        await client.get(
            f"/api/tables/{table_uuid}/columns/{lookup}/options?uuid={row}"
        )
    ).json()
    assert resolved == [
        {"uuid": row, "label": "Springfield", "deleted": True, "archived": False}
    ]

    # But the picker will not offer it to anyone choosing a new value.
    offered = (
        await client.get(f"/api/tables/{table_uuid}/columns/{lookup}/options?q=spring")
    ).json()
    assert offered == []

    # The record holding it is still saveable...
    ok = await client.patch(
        f"/api/tables/{table_uuid}/records/{record}", json={"data": {lookup: row}}
    )
    assert ok.status_code == 200, ok.text
    # ...but a *different* record may not newly point at the deleted row.
    fresh = await client.post(
        f"/api/tables/{table_uuid}/records/", json={"data": {lookup: row}}
    )
    assert fresh.status_code == 400, fresh.text


async def test_purging_the_row_is_what_clears_the_reference(
    client, session_factory, workspace_uuid, table_uuid, add_column
):
    target, row = await _target(client, workspace_uuid, add_column)
    lookup = await add_column(
        table_uuid, name="school", display_name="School", type="lookup",
        config={"target_table_uuid": target},
    )
    record = (
        await client.post(
            f"/api/tables/{table_uuid}/records/", json={"data": {lookup: row}}
        )
    ).json()["uuid"]

    await client.delete(f"/api/tables/{target}/records/{row}")
    await _age_item(session_factory, row, days=45)

    assert await _cell(session_factory, record, lookup) == row  # still pointing

    destroyed = await purge(days=30)
    assert destroyed == 1
    # Now — and only now — the pointer goes: the row it named is gone for good.
    assert await _cell(session_factory, record, lookup) is None


async def test_a_deleted_account_keeps_its_cells_until_it_is_finalized(
    client, session_factory, table_uuid, add_column, alice
):
    col = await add_column(table_uuid, name="owner", display_name="Owner", type="user")
    record = (
        await client.post(
            f"/api/tables/{table_uuid}/records/", json={"data": {col: alice}}
        )
    ).json()["uuid"]

    async with session_factory() as db:
        await UserService.delete(db, alice)

    assert await _cell(session_factory, record, col) == alice  # survives the delete

    # Inside the window, nothing is finalized.
    assert await purge_principals(days=30) == {}
    assert await _cell(session_factory, record, col) == alice

    # Past it, the cells naming them are cleared.
    async with session_factory() as db:
        await db.execute(
            sa_update_user(alice, datetime.now(timezone.utc) - timedelta(days=45))
        )
        await db.commit()
    result = await purge_principals(days=30)
    assert result["users"] == 1
    assert await _cell(session_factory, record, col) is None


def sa_update_user(uuid, when):
    from src.auth.users.models import User

    return update(User).where(User.uuid == uuid).values(deleted_at=when)
