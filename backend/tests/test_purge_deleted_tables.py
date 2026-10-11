"""Purging soft-deleted tables and their descendants/grants after the retention window."""
from datetime import datetime, timedelta, timezone

import pytest_asyncio
from sqlalchemy import func, select, update

from src.auth.users.schemas import UserCreate
from src.auth.users.services import UserService
from src.tables.models import Table
from src.tables.columns.models import Column
from src.tables.records.models import TableRecord
from src.tables.views.models import View
from src.tables.forms.models import Form
from src.tables.metrics.models import Metric
from src.maintenance.purge_deleted_tables import purge
from src.permissions.models import PermissionGrant


@pytest_asyncio.fixture
async def alice(session_factory):
    async with session_factory() as db:
        u = await UserService.create(
            db, UserCreate(username="alice", email="a@example.com", password="pw")
        )
        return str(u.uuid)


async def _age_table(session_factory, uuid: str, days: int) -> None:
    """Backdate a table's deletion, and its descendants' deletion, so the retention
    window can be tested without waiting a month."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    async with session_factory() as db:
        table = (await db.execute(select(Table).where(Table.uuid == uuid))).scalars().first()
        assert table is not None
        table.deleted_at = cutoff

        # Soft delete is cascaded at the service layer, but backdating in tests needs
        # to update descendants too since they're in separate tables.
        for model in (Column, TableRecord, View, Form, Metric):
            await db.execute(
                update(model)
                .where(model.table_id == table.id)
                .values(deleted_at=cutoff)
            )
        await db.commit()


async def _count_table(session_factory, model) -> int:
    async with session_factory() as db:
        return await db.scalar(select(func.count()).select_from(model))


async def test_only_tables_past_the_window_are_destroyed(
    client, session_factory, workspace_uuid
):
    temp_tables = {
        "live": "Live",
        "fresh_delete": "Fresh Delete",
        "old_delete": "Old Delete",
    }
    made = []
    for name, display_name in temp_tables.items():
        r = await client.post(
            "/api/tables/",
            json={"name": name, "display_name": display_name, "workspace_uuid": workspace_uuid},
        )
        assert r.status_code == 201, r.text
        made.append(r.json()["uuid"])
    live, fresh, old = made

    await client.delete(f"/api/tables/{fresh}")
    await client.delete(f"/api/tables/{old}")
    await _age_table(session_factory, old, days=45)

    assert await _count_table(session_factory, Table) == 3

    planned = await purge(days=30, dry_run=True)
    assert planned == 1
    assert await _count_table(session_factory, Table) == 3

    assert await purge(days=30) == 1
    assert await _count_table(session_factory, Table) == 2

    async with session_factory() as db:
        left = (await db.execute(select(Table.uuid))).scalars().all()
    assert {str(u) for u in left} == {live, fresh}


async def test_descendants_and_grants_go_with_it(
    client, session_factory, workspace_uuid, alice
):
    # Create table
    r = await client.post(
        "/api/tables/",
        json={"name": "test_purge", "display_name": "Test Purge", "workspace_uuid": workspace_uuid},
    )
    assert r.status_code == 201
    table_uuid = r.json()["uuid"]

    # Create descendants
    # 1. Column
    resp = await client.post(f"/api/tables/{table_uuid}/columns/", json={"name": "col1", "display_name": "Col 1", "type": "text"})
    assert resp.status_code == 201
    col_uuid = resp.json()["uuid"]

    # 2. View
    resp = await client.post(f"/api/tables/{table_uuid}/views/", json={"name": "view1", "display_name": "View 1", "config": {}})
    assert resp.status_code == 201

    # 3. Form
    resp = await client.post(f"/api/tables/{table_uuid}/forms/", json={"name": "form1", "display_name": "Form 1", "type": "create", "config": {}})
    assert resp.status_code == 201

    # 4. Record
    resp = await client.post(f"/api/tables/{table_uuid}/records/", json={"data": {col_uuid: "hello"}})
    assert resp.status_code == 201
    record_uuid = resp.json()["uuid"]

    # 5. Metric — a child like the rest, and the one with no permission grants of
    # its own, so only the row itself has to go.
    resp = await client.post(
        f"/api/tables/{table_uuid}/metrics/",
        json={
            "name": "row_count",
            "display_name": "Row count",
            "config": {"formula": "COUNT()"},
        },
    )
    assert resp.status_code == 201, resp.text

    # Create permission grant on the table itself and on the record
    granted_table = await client.post(
        "/api/permissions/grants",
        json={
            "resource_type": "table",
            "resource_uuid": table_uuid,
            "user_uuid": alice,
            "actions": ["view"],
        },
    )
    assert granted_table.status_code == 201

    granted_item = await client.post(
        "/api/permissions/grants",
        json={
            "resource_type": "record",
            "resource_uuid": record_uuid,
            "user_uuid": alice,
            "actions": ["view"],
        },
    )
    assert granted_item.status_code == 201

    # Soft-delete the table
    await client.delete(f"/api/tables/{table_uuid}")

    # Verify things are still in database
    assert await _count_table(session_factory, Table) == 1
    assert await _count_table(session_factory, Column) == 5  # default table have default columns (4) + our column
    assert await _count_table(session_factory, TableRecord) == 1
    assert await _count_table(session_factory, View) >= 1
    assert await _count_table(session_factory, Form) >= 1
    assert await _count_table(session_factory, Metric) == 1
    assert await _count_table(session_factory, PermissionGrant) == 2

    # Backdate and purge
    await _age_table(session_factory, table_uuid, days=45)
    assert await purge(days=30) == 1

    # Verify everything is gone
    assert await _count_table(session_factory, Table) == 0
    assert await _count_table(session_factory, TableRecord) == 0
    assert await _count_table(session_factory, View) == 0
    assert await _count_table(session_factory, Form) == 0
    assert await _count_table(session_factory, Metric) == 0
    assert await _count_table(session_factory, Column) == 0
    assert await _count_table(session_factory, PermissionGrant) == 0


async def test_a_zero_day_window_purges_everything_deleted(
    client, session_factory, workspace_uuid
):
    r = await client.post(
        "/api/tables/",
        json={"name": "test_zero", "display_name": "Test Zero", "workspace_uuid": workspace_uuid},
    )
    table_uuid = r.json()["uuid"]
    await client.delete(f"/api/tables/{table_uuid}")

    assert await purge(days=0) == 1
    assert await _count_table(session_factory, Table) == 0
