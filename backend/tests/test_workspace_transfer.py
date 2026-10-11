"""Workspace ownership transfer.

A workspace *is* the ownership boundary — ``owner_id`` on the row itself — so unlike
a table transfer there is no inbox to move into: the workspace changes hands where it
stands and its tables follow, because a table belongs to whoever owns its workspace.
The giver must be the current owner; the recipient becomes the new one; the giver's
implicit access ends while existing shares survive.

DISABLE_AUTH resolves the caller to the first user, so — like test_table_transfer —
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
from src.tables.schemas import TableCreate
from src.tables.services import TableService
from src.workspaces.schemas import WorkspaceCreate
from src.workspaces.services import WorkspaceService


async def _make_user(db, name: str):
    return await UserService.create(
        db, UserCreate(username=name, email=f"{name}@example.com", password="pw")
    )


async def _make_workspace(db, owner, name: str = "ws"):
    return await WorkspaceService.create(
        db, owner.id, WorkspaceCreate(name=name, display_name=name.upper())
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


# --------------------------------------------------------------------------


async def test_owner_transfers_workspace_and_its_tables(make_client, session_factory):
    """The workspace changes hands and takes its tables with it — same uuids, new
    owner. The giver loses access to both."""
    async with session_factory() as db:
        owner = await _make_user(db, "owner")
        alice = await _make_user(db, "alice")
        ws = await _make_workspace(db, owner, "team")
        table = await TableService.create(
            db, TableCreate(name="tasks", display_name="Tasks", workspace_uuid=ws.uuid)
        )
        ws_uuid, table_uuid, alice_uuid = str(ws.uuid), str(table.uuid), str(alice.uuid)

    async with make_client(owner) as client:
        resp = await client.post(
            f"/api/workspaces/{ws_uuid}/transfer", json={"user_uuid": alice_uuid}
        )
        assert resp.status_code == 200, resp.text
        # The workspace keeps its identity; only who owns it changed.
        assert resp.json()["uuid"] == ws_uuid
        assert resp.json()["name"] == "team"

    # The giver has lost the workspace and, with it, the table inside.
    async with make_client(owner) as client:
        assert (await client.get(f"/api/workspaces/{ws_uuid}")).status_code == 403
        assert (await client.get(f"/api/tables/{table_uuid}")).status_code == 403

    async with make_client(alice) as client:
        assert (await client.get(f"/api/workspaces/{ws_uuid}")).status_code == 200
        got = await client.get(f"/api/tables/{table_uuid}")
        assert got.status_code == 200, got.text


async def test_transferred_workspace_shows_up_as_owned_by_the_recipient(
    make_client, session_factory
):
    """`shared=false` is "workspaces you own", so the recipient's own listing must
    hold it and the giver's must not — the split the Workspaces page renders from."""
    async with session_factory() as db:
        owner = await _make_user(db, "owner")
        alice = await _make_user(db, "alice")
        ws = await _make_workspace(db, owner, "team")
        ws_uuid, alice_uuid = str(ws.uuid), str(alice.uuid)

    async with make_client(owner) as client:
        resp = await client.post(
            f"/api/workspaces/{ws_uuid}/transfer", json={"user_uuid": alice_uuid}
        )
        assert resp.status_code == 200, resp.text
        mine = await client.get("/api/workspaces/?shared=false&limit=100")
        assert ws_uuid not in [w["uuid"] for w in mine.json()]

    async with make_client(alice) as client:
        theirs = await client.get("/api/workspaces/?shared=false&limit=100")
        assert ws_uuid in [w["uuid"] for w in theirs.json()]


async def test_only_the_owner_can_transfer(make_client, session_factory):
    """Owner-only — even a `manage` grant on the workspace does not qualify."""
    async with session_factory() as db:
        owner = await _make_user(db, "owner")
        other = await _make_user(db, "other")
        alice = await _make_user(db, "alice")
        ws = await _make_workspace(db, owner, "team")
        ws_uuid = str(ws.uuid)
        other_uuid, alice_uuid = str(other.uuid), str(alice.uuid)

    async with make_client(owner) as client:
        grant = await client.post(
            "/api/permissions/grants",
            json={
                "resource_type": "workspace",
                "resource_uuid": ws_uuid,
                "user_uuid": other_uuid,
                "actions": ["manage"],
            },
        )
        assert grant.status_code == 201, grant.text

    async with make_client(other) as client:
        resp = await client.post(
            f"/api/workspaces/{ws_uuid}/transfer", json={"user_uuid": alice_uuid}
        )
        assert resp.status_code == 403, resp.text


async def test_cannot_transfer_to_yourself(make_client, session_factory):
    async with session_factory() as db:
        owner = await _make_user(db, "owner")
        ws = await _make_workspace(db, owner, "team")
        ws_uuid, owner_uuid = str(ws.uuid), str(owner.uuid)

    async with make_client(owner) as client:
        resp = await client.post(
            f"/api/workspaces/{ws_uuid}/transfer", json={"user_uuid": owner_uuid}
        )
        assert resp.status_code == 400, resp.text


async def test_transfer_to_unknown_user_is_rejected(make_client, session_factory):
    async with session_factory() as db:
        owner = await _make_user(db, "owner")
        ws = await _make_workspace(db, owner, "team")
        ws_uuid = str(ws.uuid)

    async with make_client(owner) as client:
        resp = await client.post(
            f"/api/workspaces/{ws_uuid}/transfer",
            json={"user_uuid": "00000000-0000-0000-0000-000000000000"},
        )
        assert resp.status_code == 400, resp.text


async def test_existing_shares_survive_a_transfer(make_client, session_factory):
    """A grant points at the workspace by resource_id, which the transfer does not
    change, so a user the giver shared with keeps access after it changes hands."""
    async with session_factory() as db:
        owner = await _make_user(db, "owner")
        alice = await _make_user(db, "alice")
        bob = await _make_user(db, "bob")
        ws = await _make_workspace(db, owner, "team")
        ws_uuid = str(ws.uuid)
        alice_uuid, bob_uuid = str(alice.uuid), str(bob.uuid)

    async with make_client(owner) as client:
        grant = await client.post(
            "/api/permissions/grants",
            json={
                "resource_type": "workspace",
                "resource_uuid": ws_uuid,
                "user_uuid": bob_uuid,
                "actions": ["view"],
            },
        )
        assert grant.status_code == 201, grant.text
        resp = await client.post(
            f"/api/workspaces/{ws_uuid}/transfer", json={"user_uuid": alice_uuid}
        )
        assert resp.status_code == 200, resp.text

    async with make_client(bob) as client:
        assert (await client.get(f"/api/workspaces/{ws_uuid}")).status_code == 200


async def test_landing_name_collision_auto_suffixes(make_client, session_factory):
    """The recipient already has a workspace of that name: the arrival lands renamed
    rather than failing on the per-owner unique index."""
    async with session_factory() as db:
        owner = await _make_user(db, "owner")
        alice = await _make_user(db, "alice")
        await _make_workspace(db, alice, "team")
        ws = await _make_workspace(db, owner, "team")
        ws_uuid, alice_uuid = str(ws.uuid), str(alice.uuid)

    async with make_client(owner) as client:
        resp = await client.post(
            f"/api/workspaces/{ws_uuid}/transfer", json={"user_uuid": alice_uuid}
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["name"] == "team_2"


async def test_the_inbox_cannot_be_transferred(make_client, session_factory):
    """The "Incoming" workspace is the recipient's own fixture, not a thing to pass
    on — it is immutable here as it is for rename and delete."""
    async with session_factory() as db:
        owner = await _make_user(db, "owner")
        alice = await _make_user(db, "alice")
        bob = await _make_user(db, "bob")
        lst_ws = await _make_workspace(db, owner, "src")
        table = await TableService.create(
            db,
            TableCreate(name="tasks", display_name="Tasks", workspace_uuid=lst_ws.uuid),
        )
        table_uuid = str(table.uuid)
        alice_uuid, bob_uuid = str(alice.uuid), str(bob.uuid)

    # Mint Alice's inbox the only way it is minted — by transferring a table to her.
    async with make_client(owner) as client:
        resp = await client.post(
            f"/api/tables/{table_uuid}/transfer", json={"user_uuid": alice_uuid}
        )
        assert resp.status_code == 200, resp.text
        inbox_uuid = resp.json()["workspace"]["uuid"]

    async with make_client(alice) as client:
        refused = await client.post(
            f"/api/workspaces/{inbox_uuid}/transfer", json={"user_uuid": bob_uuid}
        )
        assert refused.status_code == 403, refused.text
