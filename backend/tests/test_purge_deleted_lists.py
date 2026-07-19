"""Purging soft-deleted lists and their descendants/grants after the retention window."""
from datetime import datetime, timedelta, timezone

import pytest_asyncio
from sqlalchemy import func, select, update

from src.auth.users.schemas import UserCreate
from src.auth.users.services import UserService
from src.lists.models import List
from src.lists.columns.models import Column
from src.lists.items.models import ListItem
from src.lists.views.models import View
from src.lists.forms.models import Form
from src.maintenance.purge_deleted_lists import purge
from src.permissions.models import PermissionGrant


@pytest_asyncio.fixture
async def alice(session_factory):
    async with session_factory() as db:
        u = await UserService.create(
            db, UserCreate(username="alice", email="a@example.com", password="pw")
        )
        return str(u.uuid)


async def _age_list(session_factory, uuid: str, days: int) -> None:
    """Backdate a list's deletion, and its descendants' deletion, so the retention
    window can be tested without waiting a month."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    async with session_factory() as db:
        lst = (await db.execute(select(List).where(List.uuid == uuid))).scalars().first()
        assert lst is not None
        lst.deleted_at = cutoff

        # Soft delete is cascaded at the service layer, but backdating in tests needs
        # to update descendants too since they're in separate tables.
        for model in (Column, ListItem, View, Form):
            await db.execute(
                update(model)
                .where(model.list_id == lst.id)
                .values(deleted_at=cutoff)
            )
        await db.commit()


async def _count_table(session_factory, model) -> int:
    async with session_factory() as db:
        return await db.scalar(select(func.count()).select_from(model))


async def test_only_lists_past_the_window_are_destroyed(
    client, session_factory, workspace_uuid
):
    temp_lists = {
        "live": "Live",
        "fresh_delete": "Fresh Delete",
        "old_delete": "Old Delete",
    }
    made = []
    for name, display_name in temp_lists.items():
        r = await client.post(
            "/api/lists/",
            json={"name": name, "display_name": display_name, "workspace_uuid": workspace_uuid},
        )
        assert r.status_code == 201, r.text
        made.append(r.json()["uuid"])
    live, fresh, old = made

    await client.delete(f"/api/lists/{fresh}")
    await client.delete(f"/api/lists/{old}")
    await _age_list(session_factory, old, days=45)

    assert await _count_table(session_factory, List) == 3

    planned = await purge(days=30, dry_run=True)
    assert planned == 1
    assert await _count_table(session_factory, List) == 3

    assert await purge(days=30) == 1
    assert await _count_table(session_factory, List) == 2

    async with session_factory() as db:
        left = (await db.execute(select(List.uuid))).scalars().all()
    assert {str(u) for u in left} == {live, fresh}


async def test_descendants_and_grants_go_with_it(
    client, session_factory, workspace_uuid, alice
):
    # Create list
    r = await client.post(
        "/api/lists/",
        json={"name": "test_purge", "display_name": "Test Purge", "workspace_uuid": workspace_uuid},
    )
    assert r.status_code == 201
    list_uuid = r.json()["uuid"]

    # Create descendants
    # 1. Column
    resp = await client.post(f"/api/lists/{list_uuid}/columns/", json={"name": "col1", "display_name": "Col 1", "type": "text"})
    assert resp.status_code == 201
    col_uuid = resp.json()["uuid"]

    # 2. View
    resp = await client.post(f"/api/lists/{list_uuid}/views/", json={"name": "view1", "display_name": "View 1", "config": {}})
    assert resp.status_code == 201

    # 3. Form
    resp = await client.post(f"/api/lists/{list_uuid}/forms/", json={"name": "form1", "display_name": "Form 1", "type": "create", "config": {}})
    assert resp.status_code == 201

    # 4. Item
    resp = await client.post(f"/api/lists/{list_uuid}/items/", json={"data": {col_uuid: "hello"}})
    assert resp.status_code == 201
    item_uuid = resp.json()["uuid"]

    # Create permission grant on the list itself and on the item
    granted_list = await client.post(
        "/api/permissions/grants",
        json={
            "resource_type": "list",
            "resource_uuid": list_uuid,
            "user_uuid": alice,
            "actions": ["view"],
        },
    )
    assert granted_list.status_code == 201

    granted_item = await client.post(
        "/api/permissions/grants",
        json={
            "resource_type": "item",
            "resource_uuid": item_uuid,
            "user_uuid": alice,
            "actions": ["view"],
        },
    )
    assert granted_item.status_code == 201

    # Soft-delete the list
    await client.delete(f"/api/lists/{list_uuid}")

    # Verify things are still in database
    assert await _count_table(session_factory, List) == 1
    assert await _count_table(session_factory, Column) == 5  # default list have default columns (4) + our column
    assert await _count_table(session_factory, ListItem) == 1
    assert await _count_table(session_factory, View) >= 1
    assert await _count_table(session_factory, Form) >= 1
    assert await _count_table(session_factory, PermissionGrant) == 2

    # Backdate and purge
    await _age_list(session_factory, list_uuid, days=45)
    assert await purge(days=30) == 1

    # Verify everything is gone
    assert await _count_table(session_factory, List) == 0
    assert await _count_table(session_factory, ListItem) == 0
    assert await _count_table(session_factory, PermissionGrant) == 0


async def test_a_zero_day_window_purges_everything_deleted(
    client, session_factory, workspace_uuid
):
    r = await client.post(
        "/api/lists/",
        json={"name": "test_zero", "display_name": "Test Zero", "workspace_uuid": workspace_uuid},
    )
    list_uuid = r.json()["uuid"]
    await client.delete(f"/api/lists/{list_uuid}")

    assert await purge(days=0) == 1
    assert await _count_table(session_factory, List) == 0
