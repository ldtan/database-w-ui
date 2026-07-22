"""List ownership transfer.

Ownership flows through the workspace, so handing a list to another user means
moving it into that user's immutable "Incoming" workspace, minted on demand. The
giver must be the list's current owner; the recipient becomes the new owner; the
giver's implicit access ends while existing shares survive.

DISABLE_AUTH resolves the caller to the first user, so — like test_permissions —
these drive HTTP as a chosen user via a get_current_user override.
"""
from contextlib import asynccontextmanager

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from main import create_app
from src.auth.dependencies import get_current_user
from src.auth.users.schemas import UserCreate
from src.auth.users.services import UserService
from src.infrastructure import database as db_module
from src.lists.schemas import ListCreate
from src.lists.services import ListService
from src.workspaces.schemas import WorkspaceCreate
from src.workspaces.services import INBOX_NAME, WorkspaceService


async def _make_user(db, name: str):
    return await UserService.create(
        db, UserCreate(username=name, email=f"{name}@example.com", password="pw")
    )


async def _make_list(db, owner, name: str = "lst"):
    ws = await WorkspaceService.create(
        db, owner.id, WorkspaceCreate(name=f"{name}_ws", display_name="WS")
    )
    return await ListService.create(
        db, ListCreate(name=name, display_name=name, workspace_uuid=ws.uuid)
    )


@pytest_asyncio.fixture
def make_client(engine, session_factory):
    """An AsyncClient that authenticates as a specific user (see test_permissions)."""

    @asynccontextmanager
    async def _make(user):
        orig_engine, orig_factory = db_module.engine, db_module.SessionLocal
        db_module.engine = engine
        db_module.SessionLocal = session_factory
        app = create_app()

        async def _current_user():
            return user

        app.dependency_overrides[get_current_user] = _current_user
        transport = ASGITransport(app=app)
        try:
            async with AsyncClient(
                transport=transport,
                base_url="http://test",
                headers={"Authorization": "Bearer test"},
            ) as client:
                yield client
        finally:
            db_module.engine, db_module.SessionLocal = orig_engine, orig_factory

    return _make


async def _recipient_inbox(session_factory, recipient_id: int):
    """The recipient's inbox as it stands now (idempotent — does not create one if a
    transfer already did)."""
    async with session_factory() as db:
        return await WorkspaceService.get_or_create_inbox(db, recipient_id)


# --------------------------------------------------------------------------


async def test_owner_transfers_list_into_recipient_inbox(make_client, session_factory):
    async with session_factory() as db:
        owner = await _make_user(db, "owner")
        alice = await _make_user(db, "alice")
        lst = await _make_list(db, owner, "tasks")
        list_uuid, alice_uuid = str(lst.uuid), str(alice.uuid)

    async with make_client(owner) as client:
        resp = await client.post(
            f"/api/lists/{list_uuid}/transfer", json={"user_uuid": alice_uuid}
        )
        assert resp.status_code == 200, resp.text

    inbox = await _recipient_inbox(session_factory, alice.id)
    assert inbox.is_system is True
    assert inbox.name == INBOX_NAME
    # The list now lives in Alice's inbox.
    assert resp.json()["workspace"]["uuid"] == str(inbox.uuid)

    # The giver has lost access; the recipient (now owner) can see it.
    async with make_client(owner) as client:
        assert (await client.get(f"/api/lists/{list_uuid}")).status_code == 403
    async with make_client(alice) as client:
        got = await client.get(f"/api/lists/{list_uuid}")
        assert got.status_code == 200, got.text


async def test_only_the_owner_can_transfer(make_client, session_factory):
    """Owner-only — even a `manage` grant on the list does not qualify."""
    async with session_factory() as db:
        owner = await _make_user(db, "owner")
        other = await _make_user(db, "other")
        alice = await _make_user(db, "alice")
        lst = await _make_list(db, owner, "tasks")
        list_uuid = str(lst.uuid)
        other_uuid, alice_uuid = str(other.uuid), str(alice.uuid)

    # Give `other` full control of the list — still not its owner.
    async with make_client(owner) as client:
        grant = await client.post(
            "/api/permissions/grants",
            json={
                "resource_type": "list",
                "resource_uuid": list_uuid,
                "user_uuid": other_uuid,
                "actions": ["manage"],
            },
        )
        assert grant.status_code == 201, grant.text

    async with make_client(other) as client:
        resp = await client.post(
            f"/api/lists/{list_uuid}/transfer", json={"user_uuid": alice_uuid}
        )
        assert resp.status_code == 403, resp.text


async def test_cannot_transfer_to_yourself(make_client, session_factory):
    async with session_factory() as db:
        owner = await _make_user(db, "owner")
        lst = await _make_list(db, owner, "tasks")
        list_uuid, owner_uuid = str(lst.uuid), str(owner.uuid)

    async with make_client(owner) as client:
        resp = await client.post(
            f"/api/lists/{list_uuid}/transfer", json={"user_uuid": owner_uuid}
        )
        assert resp.status_code == 400, resp.text


async def test_transfer_to_unknown_user_is_rejected(make_client, session_factory):
    async with session_factory() as db:
        owner = await _make_user(db, "owner")
        lst = await _make_list(db, owner, "tasks")
        list_uuid = str(lst.uuid)

    async with make_client(owner) as client:
        resp = await client.post(
            f"/api/lists/{list_uuid}/transfer",
            json={"user_uuid": "00000000-0000-0000-0000-000000000000"},
        )
        assert resp.status_code == 400, resp.text


async def test_existing_shares_survive_a_transfer(make_client, session_factory):
    """A grant points at the list by resource_id, unchanged by the move, so a user the
    giver shared with keeps their access after ownership changes hands."""
    async with session_factory() as db:
        owner = await _make_user(db, "owner")
        alice = await _make_user(db, "alice")
        bob = await _make_user(db, "bob")
        lst = await _make_list(db, owner, "tasks")
        list_uuid = str(lst.uuid)
        alice_uuid, bob_uuid = str(alice.uuid), str(bob.uuid)

    async with make_client(owner) as client:
        grant = await client.post(
            "/api/permissions/grants",
            json={
                "resource_type": "list",
                "resource_uuid": list_uuid,
                "user_uuid": bob_uuid,
                "actions": ["view"],
            },
        )
        assert grant.status_code == 201, grant.text
        resp = await client.post(
            f"/api/lists/{list_uuid}/transfer", json={"user_uuid": alice_uuid}
        )
        assert resp.status_code == 200, resp.text

    # Bob's view grant still resolves after the list changed owners.
    async with make_client(bob) as client:
        assert (await client.get(f"/api/lists/{list_uuid}")).status_code == 200


async def test_landing_name_collision_auto_suffixes(make_client, session_factory):
    """Two lists of the same name transferred to one inbox: the second lands renamed
    rather than failing on the per-workspace unique name."""
    async with session_factory() as db:
        owner = await _make_user(db, "owner")
        alice = await _make_user(db, "alice")
        alice_uuid = str(alice.uuid)
        first = await _make_list(db, owner, "dup")
        first_uuid = str(first.uuid)

    async with make_client(owner) as client:
        r1 = await client.post(
            f"/api/lists/{first_uuid}/transfer", json={"user_uuid": alice_uuid}
        )
        assert r1.status_code == 200, r1.text
        assert r1.json()["name"] == "dup"

    # A second list also named "dup" (in a different workspace) transferred to the same
    # inbox must land renamed rather than 409 on the inbox's unique name.
    async with session_factory() as db:
        owner = await UserService.get_by_username(db, "owner")
        ws = await WorkspaceService.create(
            db, owner.id, WorkspaceCreate(name="second_ws", display_name="WS2")
        )
        dup2 = await ListService.create(
            db, ListCreate(name="dup", display_name="dup", workspace_uuid=ws.uuid)
        )
        dup2_uuid = str(dup2.uuid)

    async with make_client(owner) as client:
        r2 = await client.post(
            f"/api/lists/{dup2_uuid}/transfer", json={"user_uuid": alice_uuid}
        )
        assert r2.status_code == 200, r2.text
        assert r2.json()["name"] == "dup_2"


async def test_inbox_is_immutable(make_client, session_factory):
    """The recipient owns the inbox but cannot rename or delete it — only its lists."""
    async with session_factory() as db:
        owner = await _make_user(db, "owner")
        alice = await _make_user(db, "alice")
        lst = await _make_list(db, owner, "tasks")
        list_uuid, alice_uuid = str(lst.uuid), str(alice.uuid)

    async with make_client(owner) as client:
        resp = await client.post(
            f"/api/lists/{list_uuid}/transfer", json={"user_uuid": alice_uuid}
        )
        assert resp.status_code == 200, resp.text

    inbox = await _recipient_inbox(session_factory, alice.id)
    inbox_uuid = str(inbox.uuid)

    async with make_client(alice) as client:
        patched = await client.patch(
            f"/api/workspaces/{inbox_uuid}", json={"display_name": "Renamed"}
        )
        assert patched.status_code == 403, patched.text
        deleted = await client.delete(f"/api/workspaces/{inbox_uuid}")
        assert deleted.status_code == 403, deleted.text


async def test_incoming_is_a_reserved_workspace_name(make_client, session_factory):
    async with session_factory() as db:
        owner = await _make_user(db, "owner")

    async with make_client(owner) as client:
        resp = await client.post(
            "/api/workspaces/",
            json={"name": INBOX_NAME, "display_name": "Mine"},
        )
        assert resp.status_code == 409, resp.text
