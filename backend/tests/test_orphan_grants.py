"""Grants outlive the things they were about, and nothing cascades.

`permission_grants` points at its resource with no foreign key — `resource_id` means a
different table per `resource_type` — so a deleted list, view or account leaves its
grants behind. They are inert, but they accumulate and they make "who has access?"
answer with the names of people who don't.
"""
from datetime import datetime, timedelta, timezone

import pytest_asyncio
from sqlalchemy import func, select, update

from src.auth.users.schemas import UserCreate, UserGroupCreate
from src.auth.users.services import UserGroupService, UserService
from src.auth.users.models import User, UserGroup
from src.lists.models import List
from src.maintenance.sweep_orphan_grants import sweep
from src.permissions.models import PermissionGrant


@pytest_asyncio.fixture
async def principals(session_factory):
    async with session_factory() as db:
        u = await UserService.create(
            db, UserCreate(username="alice", email="a@example.com", password="pw")
        )
        g = await UserGroupService.create(db, UserGroupCreate(name="editors", display_name="Editors"))
        return str(u.uuid), str(g.uuid)


async def _grants(session_factory) -> int:
    async with session_factory() as db:
        return await db.scalar(select(func.count()).select_from(PermissionGrant))


async def _expire(session_factory, model, uuid, days=45):
    async with session_factory() as db:
        await db.execute(
            update(model)
            .where(model.uuid == uuid)
            .values(deleted_at=datetime.now(timezone.utc) - timedelta(days=days))
        )
        await db.commit()


async def _share(client, resource_type, resource_uuid, **who):
    resp = await client.post(
        "/api/permissions/grants",
        json={
            "resource_type": resource_type,
            "resource_uuid": resource_uuid,
            "actions": ["view"],
            **who,
        },
    )
    assert resp.status_code == 201, resp.text


async def test_the_share_dialog_stops_listing_deleted_principals(
    client, session_factory, list_uuid, principals
):
    """The live bug: "who has access to this list?" was answering with a deleted user
    and a deleted group. Both are inert — the account cannot log in, the group has no
    members — but naming them is worse than useless to whoever is auditing."""
    alice, editors = principals
    await _share(client, "list", list_uuid, user_uuid=alice)
    await _share(client, "list", list_uuid, group_uuid=editors)

    url = f"/api/permissions/grants?resource_type=list&resource_uuid={list_uuid}"
    assert len((await client.get(url)).json()) == 2

    async with session_factory() as db:
        await UserService.delete(db, alice)
    # `group_name` is the label to render, so it is the display name; the slug rides
    # alongside it for the Share panel to disambiguate with.
    remaining = (await client.get(url)).json()
    assert [g["group_name"] for g in remaining] == ["Editors"]
    assert [g["group_slug"] for g in remaining] == ["editors"]

    async with session_factory() as db:
        await UserGroupService.delete(db, editors)
    assert (await client.get(url)).json() == []


async def test_a_grant_inside_the_window_is_left_alone(
    client, session_factory, list_uuid, principals
):
    """The resource can still be restored, and its sharing should come back with it."""
    alice, _ = principals
    await _share(client, "list", list_uuid, user_uuid=alice)
    assert (await client.delete(f"/api/lists/{list_uuid}")).status_code == 204

    assert await sweep(days=30, dry_run=True) == 0
    assert await _grants(session_factory) == 1


async def test_grants_are_swept_once_the_resource_is_past_the_window(
    client, session_factory, list_uuid, principals
):
    alice, _ = principals
    await _share(client, "list", list_uuid, user_uuid=alice)
    await client.delete(f"/api/lists/{list_uuid}")
    await _expire(session_factory, List, list_uuid)

    assert await sweep(days=30, dry_run=True) == 1
    assert await _grants(session_factory) == 1  # a dry run deletes nothing

    assert await sweep(days=30) == 1
    assert await _grants(session_factory) == 0
    assert await sweep(days=30) == 0  # idempotent


async def test_grants_are_swept_once_the_principal_is_past_the_window(
    client, session_factory, list_uuid, principals
):
    alice, editors = principals
    await _share(client, "list", list_uuid, user_uuid=alice)
    await _share(client, "list", list_uuid, group_uuid=editors)

    async with session_factory() as db:
        await UserService.delete(db, alice)
        await UserGroupService.delete(db, editors)
    assert await sweep(days=30) == 0  # still inside the window: still restorable

    await _expire(session_factory, User, alice)
    await _expire(session_factory, UserGroup, editors)
    assert await sweep(days=30) == 2
    assert await _grants(session_factory) == 0


async def test_expired_grants_are_swept(client, session_factory, list_uuid, principals):
    """A grant that has already outlived its expiry should be removed by the sweep."""
    alice, _ = principals
    await _share(client, "list", list_uuid, user_uuid=alice)

    async with session_factory() as db:
        grant = (
            await db.execute(
                select(PermissionGrant).where(PermissionGrant.resource_type == "list")
            )
        ).scalars().first()
        assert grant is not None
        grant.expires_at = datetime.now(timezone.utc) - timedelta(days=1)
        await db.commit()

    assert await sweep(days=30, dry_run=True) == 1
    assert await _grants(session_factory) == 1

    assert await sweep(days=30) == 1
    assert await _grants(session_factory) == 0
