"""Purging soft-deleted workspaces and all their lists/descendants/grants after the retention window."""
from datetime import datetime, timedelta, timezone

import pytest_asyncio
from sqlalchemy import func, select, update

from src.auth.users.schemas import UserCreate
from src.auth.users.services import UserService
from src.workspaces.models import Workspace
from src.lists.models import List
from src.lists.columns.models import Column
from src.lists.items.models import ListItem
from src.lists.views.models import View
from src.lists.forms.models import Form
from src.lists.metrics.models import Metric
from src.maintenance.purge_deleted_workspaces import purge
from src.permissions.models import PermissionGrant


@pytest_asyncio.fixture
async def alice(session_factory):
    async with session_factory() as db:
        # Creates a default personal workspace along with it
        u = await UserService.create(
            db, UserCreate(username="alice", email="a@example.com", password="pw")
        )
        return str(u.uuid)


async def _age_workspace(session_factory, uuid: str, days: int) -> None:
    """Backdate a workspace's deletion, and its lists and descendants' deletion,
    so the retention window can be tested without waiting a month."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    async with session_factory() as db:
        ws = (await db.execute(select(Workspace).where(Workspace.uuid == uuid))).scalars().first()
        assert ws is not None
        ws.deleted_at = cutoff

        # Find lists in this workspace
        lists = (await db.execute(select(List).where(List.workspace_id == ws.id))).scalars().all()
        for lst in lists:
            lst.deleted_at = cutoff
            for model in (Column, ListItem, View, Form, Metric):
                await db.execute(
                    update(model)
                    .where(model.list_id == lst.id)
                    .values(deleted_at=cutoff)
                )
        await db.commit()


async def _count_table(session_factory, model) -> int:
    async with session_factory() as db:
        return await db.scalar(select(func.count()).select_from(model))


async def test_only_workspaces_past_the_window_are_destroyed(
    client, session_factory
):
    temp_lists = {
        "live": "Live",
        "fresh_delete": "Fresh Delete",
        "old_delete": "Old Delete",
    }
    made = []
    for name, display_name in temp_lists.items():
        r = await client.post(
            "/api/workspaces/",
            json={"name": name, "display_name": display_name},
        )
        assert r.status_code == 201, r.text
        made.append(r.json()["uuid"])
    live, fresh, old = made

    await client.delete(f"/api/workspaces/{fresh}")
    await client.delete(f"/api/workspaces/{old}")
    await _age_workspace(session_factory, old, days=45)

    # 3 made + any defaults
    initial_count = await _count_table(session_factory, Workspace)

    planned = await purge(days=30, dry_run=True)
    assert planned == 1
    assert await _count_table(session_factory, Workspace) == initial_count

    assert await purge(days=30) == 1
    assert await _count_table(session_factory, Workspace) == initial_count - 1

    async with session_factory() as db:
        left = (await db.execute(select(Workspace.uuid))).scalars().all()
    current_workspaces = {str(u) for u in left}
    assert old not in current_workspaces
    assert all(uuid in current_workspaces for uuid in [live, fresh])


async def test_workspace_descendants_and_grants_go_with_it(
    client, session_factory, alice
):
    # Create workspace
    r = await client.post(
        "/api/workspaces/",
        json={"name": "test_ws_purge", "display_name": "Test WS Purge"},
    )
    assert r.status_code == 201
    workspace_uuid = r.json()["uuid"]

    # Create list in that workspace
    r = await client.post(
        "/api/lists/",
        json={"name": "test_list", "display_name": "Test List", "workspace_uuid": workspace_uuid},
    )
    assert r.status_code == 201
    list_uuid = r.json()["uuid"]

    # Create descendants of list
    resp = await client.post(f"/api/lists/{list_uuid}/columns/", json={"name": "col1", "display_name": "Col 1", "type": "text"})
    assert resp.status_code == 201
    col_uuid = resp.json()["uuid"]

    resp = await client.post(f"/api/lists/{list_uuid}/items/", json={"data": {col_uuid: "hello"}})
    assert resp.status_code == 201
    item_uuid = resp.json()["uuid"]

    # A metric on that list: it has no grants of its own, but its row still has to
    # go before the list it points at can.
    resp = await client.post(
        f"/api/lists/{list_uuid}/metrics/",
        json={
            "name": "row_count",
            "display_name": "Row count",
            "config": {"formula": "COUNT()"},
        },
    )
    assert resp.status_code == 201, resp.text

    # Create permission grant on the workspace itself
    granted_ws = await client.post(
        "/api/permissions/grants",
        json={
            "resource_type": "workspace",
            "resource_uuid": workspace_uuid,
            "user_uuid": alice,
            "actions": ["view"],
        },
    )
    assert granted_ws.status_code == 201

    # Create permission grant on the item
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

    # Soft-delete the workspace
    await client.delete(f"/api/workspaces/{workspace_uuid}")

    # Verify things are still in database
    assert await _count_table(session_factory, Workspace) == 3  # default personal + Alice default Workspace + our workspace
    assert await _count_table(session_factory, List) == 1
    assert await _count_table(session_factory, ListItem) == 1
    assert await _count_table(session_factory, Metric) == 1
    assert await _count_table(session_factory, PermissionGrant) == 2

    # Backdate and purge
    await _age_workspace(session_factory, workspace_uuid, days=45)
    assert await purge(days=30) == 1

    # Verify everything is gone
    assert await _count_table(session_factory, Workspace) == 2  # only default personal + Alice default Workspace 1 remains
    assert await _count_table(session_factory, List) == 0
    assert await _count_table(session_factory, ListItem) == 0
    assert await _count_table(session_factory, View) == 0
    assert await _count_table(session_factory, Form) == 0
    assert await _count_table(session_factory, Metric) == 0
    assert await _count_table(session_factory, Column) == 0
    assert await _count_table(session_factory, PermissionGrant) == 0


async def test_a_zero_day_window_purges_everything_deleted(
    client, session_factory
):
    r = await client.post(
        "/api/workspaces/",
        json={"name": "test_zero", "display_name": "Test Zero"},
    )
    workspace_uuid = r.json()["uuid"]
    await client.delete(f"/api/workspaces/{workspace_uuid}")

    assert await purge(days=0) == 1
    assert await _count_table(session_factory, Workspace) == 1  # only default personal remains
