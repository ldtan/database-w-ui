"""The bin for deleted accounts and groups.

The last surface where a delete was one-way in practice: the row always stayed, but
nothing read it back, so there was no way to see or undo one, and
`purge_deleted_principals` deliberately never removed it either.

Three things here are unlike every other bin, and are most of what these assert:

- **restore cannot 409.** `username`, `email` and a group's `name` are plain unique
  constraints, not the partial (`WHERE deleted_at IS NULL`) indexes the rest of the
  schema uses, so a deleted principal keeps its name reserved and nothing can take it.
- **permanent delete is asymmetrical.** A group is only ever a principal; an account
  owns workspaces through a non-null foreign key, so destroying one is refused while
  any remain.
- **every account owns at least one workspace from birth** (`UserService.create` calls
  `WorkspaceService.create_default`), so in practice destroying an account always means
  dealing with that workspace first. That is the refusal working, not an edge case.
"""
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select, update

from main import create_app
from src.auth.dependencies import get_current_user
from src.auth.users.models import User, UserGroup, user_group_association
from src.auth.users.schemas import UserCreate
from src.auth.users.services import UserService
from src.infrastructure import database as db_module
from src.permissions.models import PermissionGrant
from src.workspaces.models import Workspace

USERS = "/api/auth/users"
GROUPS = "/api/auth/groups"


@pytest_asyncio.fixture(autouse=True)
async def superuser(session_factory, user_id):
    """These routes are identity administration. DISABLE_AUTH resolves the *first*
    user, so promoting that row is what makes the default client an administrator."""
    async with session_factory() as db:
        await db.execute(
            update(User).where(User.id == user_id).values(is_superuser=True)
        )
        await db.commit()


@pytest_asyncio.fixture
def make_client(engine, session_factory):
    """A client acting as a specific user, for the one test that needs a caller who is
    not an administrator."""

    @asynccontextmanager
    async def _make(user):
        orig_engine, orig_factory = db_module.engine, db_module.SessionLocal
        db_module.engine, db_module.SessionLocal = engine, session_factory
        app = create_app()

        async def _current_user():
            return user

        app.dependency_overrides[get_current_user] = _current_user
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
                headers={"Authorization": "Bearer test"},
            ) as client:
                yield client
        finally:
            db_module.engine, db_module.SessionLocal = orig_engine, orig_factory

    return _make


@pytest_asyncio.fixture
async def alice(client):
    resp = await client.post(
        f"{USERS}/",
        json={"username": "alice", "email": "a@example.com", "password": "pw123456"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["uuid"]


@pytest_asyncio.fixture
async def editors(client, alice):
    resp = await client.post(
        f"{GROUPS}/",
        json={
            "name": "editors",
            "display_name": "Editors",
            "description": "Can edit",
            "user_uuids": [alice],
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["uuid"]


async def _expire(session_factory, model, uuid, days=45) -> None:
    """Push a deletion past the retention window, where the bin stops offering it."""
    async with session_factory() as db:
        await db.execute(
            update(model)
            .where(model.uuid == uuid)
            .values(deleted_at=datetime.now(timezone.utc) - timedelta(days=days))
        )
        await db.commit()


async def _disown(session_factory, user_uuid: str) -> None:
    """Destroy every workspace the account owns, so the purge has nothing to refuse
    over. Straight to the table: the point here is the account, and an account's
    personal workspace is otherwise a two-step delete of its own."""
    async with session_factory() as db:
        owner_id = await db.scalar(select(User.id).where(User.uuid == user_uuid))
        await db.execute(
            Workspace.__table__.delete().where(Workspace.owner_id == owner_id)
        )
        await db.commit()


async def test_a_deleted_account_turns_up_in_the_bin_and_comes_back(client, alice):
    assert (await client.delete(f"{USERS}/{alice}")).status_code == 204
    assert alice not in [u["uuid"] for u in (await client.get(f"{USERS}/")).json()]

    binned = await client.get(f"{USERS}/deleted")
    assert binned.status_code == 200, binned.text
    assert binned.headers["X-Total-Count"] == "1"
    row = binned.json()[0]
    assert row["uuid"] == alice
    assert row["name"] == "alice"
    assert row["detail"] == "a@example.com"
    assert row["deleted_at"] is not None

    restored = await client.post(f"{USERS}/{alice}/restore")
    assert restored.status_code == 200, restored.text
    assert restored.json()["username"] == "alice"
    assert (await client.get(f"{USERS}/deleted")).headers["X-Total-Count"] == "0"
    assert alice in [u["uuid"] for u in (await client.get(f"{USERS}/")).json()]


async def test_a_deleted_group_turns_up_in_the_bin_and_comes_back(client, editors):
    assert (await client.delete(f"{GROUPS}/{editors}")).status_code == 204

    binned = await client.get(f"{GROUPS}/deleted")
    assert binned.status_code == 200, binned.text
    assert binned.headers["X-Total-Count"] == "1"
    # The bin exists to be recognised, so a group is listed by the label the People
    # page showed — its display name, not the slug code addresses it by.
    assert binned.json()[0]["name"] == "Editors"
    assert binned.json()[0]["detail"] == "Can edit"

    restored = await client.post(f"{GROUPS}/{editors}/restore")
    assert restored.status_code == 200, restored.text
    assert restored.json()["name"] == "editors"
    assert restored.json()["display_name"] == "Editors"


async def test_a_restored_account_keeps_its_groups(client, alice, editors):
    """Restore is a real undo. The delete used to clear memberships, which made it
    lossy in a way nothing recorded — an account came back with no groups and no way to
    know it had ever had any. They are kept now and the reads filter instead."""
    before = (await client.get(f"{USERS}/{alice}")).json()
    assert [g["uuid"] for g in before["groups"]] == [editors]

    assert (await client.delete(f"{USERS}/{alice}")).status_code == 204
    # Gone from the group's roster while it is in the bin, without being thrown away.
    assert (await client.get(f"{GROUPS}/{editors}")).json()["users"] == []

    restored = await client.post(f"{USERS}/{alice}/restore")
    assert restored.status_code == 200, restored.text
    assert [g["uuid"] for g in restored.json()["groups"]] == [editors]
    assert [u["uuid"] for u in (await client.get(f"{GROUPS}/{editors}")).json()["users"]] == [
        alice
    ]


async def test_a_restored_group_keeps_its_members(client, alice, editors):
    """The mirror, from the group's side."""
    assert (await client.delete(f"{GROUPS}/{editors}")).status_code == 204
    assert (await client.get(f"{USERS}/{alice}")).json()["groups"] == []

    restored = await client.post(f"{GROUPS}/{editors}/restore")
    assert restored.status_code == 200, restored.text
    assert [u["uuid"] for u in restored.json()["users"]] == [alice]
    assert [g["uuid"] for g in (await client.get(f"{USERS}/{alice}")).json()["groups"]] == [
        editors
    ]


async def test_a_deleted_group_confers_nothing_while_it_is_binned(
    client, session_factory, alice, editors, workspace_uuid, make_client
):
    """The security half of keeping membership rows, and the reason this change is not
    only cosmetic. `PermissionService._group_ids` reads the association table directly,
    so it does not benefit from the relationship's filter and checks liveness itself.
    Without that, deleting a group would leave every grant it holds still reaching its
    members — and restoring would be the only way to *stop* that."""
    granted = await client.post(
        "/api/permissions/grants",
        json={
            "resource_type": "workspace",
            "resource_uuid": workspace_uuid,
            "group_uuid": editors,
            "actions": ["view"],
        },
    )
    assert granted.status_code == 201, granted.text

    async with session_factory() as db:
        member = (
            await db.execute(select(User).where(User.uuid == alice))
        ).scalars().first()

    # Alice reaches the workspace only through the group.
    async with make_client(member) as as_alice:
        seen = await as_alice.get(f"/api/workspaces/{workspace_uuid}")
        assert seen.status_code == 200, seen.text

    assert (await client.delete(f"{GROUPS}/{editors}")).status_code == 204

    async with session_factory() as db:
        member = (
            await db.execute(select(User).where(User.uuid == alice))
        ).scalars().first()
    async with make_client(member) as as_alice:
        assert (
            await as_alice.get(f"/api/workspaces/{workspace_uuid}")
        ).status_code == 403

    # And restoring the group hands the access back — the sharp edge of that route.
    assert (await client.post(f"{GROUPS}/{editors}/restore")).status_code == 200
    async with session_factory() as db:
        member = (
            await db.execute(select(User).where(User.uuid == alice))
        ).scalars().first()
    async with make_client(member) as as_alice:
        assert (
            await as_alice.get(f"/api/workspaces/{workspace_uuid}")
        ).status_code == 200


async def test_restore_never_collides_because_the_name_was_never_released(
    client, alice
):
    """Every other bin can 409 on restore: its unique index is partial, so a live row
    may take the binned one's name meanwhile. Accounts are the exception — the
    constraint is unconditional, so the username stays reserved while it sits here."""
    assert (await client.delete(f"{USERS}/{alice}")).status_code == 204

    clash = await client.post(
        f"{USERS}/",
        json={
            "username": "alice",
            "email": "other@example.com",
            "password": "pw123456",
        },
    )
    assert clash.status_code == 409, clash.text

    assert (await client.post(f"{USERS}/{alice}/restore")).status_code == 200


async def test_only_something_in_the_bin_can_be_restored_or_purged(client, alice):
    """A live account 404s on both, so destroying one is always two deliberate steps."""
    assert (await client.post(f"{USERS}/{alice}/restore")).status_code == 404
    assert (await client.delete(f"{USERS}/{alice}/permanent")).status_code == 404


async def test_an_expired_account_is_no_longer_offered_back(
    client, session_factory, alice
):
    """Past the window it is on its way out and not promised back — the rule every
    other bin follows."""
    assert (await client.delete(f"{USERS}/{alice}")).status_code == 204
    await _expire(session_factory, User, alice)

    assert (await client.get(f"{USERS}/deleted")).headers["X-Total-Count"] == "0"
    assert (await client.post(f"{USERS}/{alice}/restore")).status_code == 404


async def test_purging_an_account_that_owns_a_workspace_is_refused(client, alice):
    """The whole reason `purge_deleted_principals` never deleted the row.
    `workspaces.owner_id` is non-null, so the alternative to refusing is taking
    somebody's data down with the account — and every account owns its personal
    workspace from the moment it is created, so this is the *normal* path."""
    assert (await client.delete(f"{USERS}/{alice}")).status_code == 204

    refused = await client.delete(f"{USERS}/{alice}/permanent")
    assert refused.status_code == 409, refused.text
    assert "workspace" in refused.json()["detail"]

    # Still in the bin and still restorable — a refusal changes nothing.
    assert (await client.get(f"{USERS}/deleted")).headers["X-Total-Count"] == "1"
    assert (await client.post(f"{USERS}/{alice}/restore")).status_code == 200


async def test_a_soft_deleted_workspace_blocks_it_too(client, session_factory, alice):
    """It holds the foreign key exactly as a live one does, and it is recoverable — so
    it is named rather than stepped over."""
    await _disown(session_factory, alice)
    async with session_factory() as db:
        owner_id = await db.scalar(select(User.id).where(User.uuid == alice))
        db.add(
            Workspace(
                name="binned",
                display_name="Binned",
                owner_id=owner_id,
                deleted_at=datetime.now(timezone.utc),
            )
        )
        await db.commit()

    assert (await client.delete(f"{USERS}/{alice}")).status_code == 204
    refused = await client.delete(f"{USERS}/{alice}/permanent")
    assert refused.status_code == 409, refused.text
    assert "binned" in refused.json()["detail"]


async def test_purging_an_account_that_owns_nothing_destroys_it(
    client, session_factory, alice, workspace_uuid
):
    """With no workspaces in the way the row really goes — the thing the purge job
    still refuses to do on its own."""
    granted = await client.post(
        "/api/permissions/grants",
        json={
            "resource_type": "workspace",
            "resource_uuid": workspace_uuid,
            "user_uuid": alice,
            "actions": ["view"],
        },
    )
    assert granted.status_code == 201, granted.text
    await _disown(session_factory, alice)

    assert (await client.delete(f"{USERS}/{alice}")).status_code == 204
    assert (await client.delete(f"{USERS}/{alice}/permanent")).status_code == 204

    async with session_factory() as db:
        assert await db.scalar(select(User.id).where(User.uuid == alice)) is None
        # Its grants went with it: nothing else can find them once the row is gone.
        assert await db.scalar(
            select(func.count())
            .select_from(PermissionGrant)
            .where(PermissionGrant.user_id.is_not(None))
        ) == 0

    assert (await client.post(f"{USERS}/{alice}/restore")).status_code == 404


async def test_purging_a_group_destroys_it_and_its_grants(
    client, session_factory, editors, workspace_uuid
):
    """Nothing blocks a group: it is only ever a principal, never an owner."""
    granted = await client.post(
        "/api/permissions/grants",
        json={
            "resource_type": "workspace",
            "resource_uuid": workspace_uuid,
            "group_uuid": editors,
            "actions": ["view"],
        },
    )
    assert granted.status_code == 201, granted.text

    assert (await client.delete(f"{GROUPS}/{editors}")).status_code == 204
    assert (await client.delete(f"{GROUPS}/{editors}/permanent")).status_code == 204

    async with session_factory() as db:
        assert await db.scalar(
            select(UserGroup.id).where(UserGroup.uuid == editors)
        ) is None
        assert await db.scalar(
            select(func.count())
            .select_from(PermissionGrant)
            .where(PermissionGrant.group_id.is_not(None))
        ) == 0
        # The membership rows too — they point at the group with a real foreign key.
        assert await db.scalar(
            select(func.count()).select_from(user_group_association)
        ) == 0


async def test_the_bins_are_superuser_only(client, session_factory, alice, make_client):
    """Identity administration, not data access — restoring a group hands back every
    grant it held, so the gate is the one the live write routes already use."""
    assert (await client.delete(f"{USERS}/{alice}")).status_code == 204

    async with session_factory() as db:
        ordinary = await UserService.create(
            db, UserCreate(username="bob", email="b@example.com", password="pw123456")
        )

    async with make_client(ordinary) as unprivileged:
        assert (await unprivileged.get(f"{USERS}/deleted")).status_code == 403
        assert (await unprivileged.get(f"{GROUPS}/deleted")).status_code == 403
        assert (
            await unprivileged.post(f"{USERS}/{alice}/restore")
        ).status_code == 403
        assert (
            await unprivileged.delete(f"{USERS}/{alice}/permanent")
        ).status_code == 403
