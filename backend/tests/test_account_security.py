"""Account-administration guards.

The permission model is only as strong as the identity layer under it. These
routes were previously open to any authenticated caller, which meant an ordinary
user could reset another user's password and log in as them -- bypassing
permissions entirely -- or add themselves to a group and inherit its grants.
"""
from contextlib import asynccontextmanager

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from main import create_app
from src.auth.dependencies import get_current_user
from src.auth.users.models import AUTHENTICATED_USERS_NAME
from src.auth.users.schemas import UserCreate, UserGroupCreate
from src.auth.users.services import UserGroupService, UserService
from src.infrastructure import database as db_module
from src.auth.seed import ensure_system_groups


async def _make_user(db, name: str, superuser: bool = False):
    user = await UserService.create(
        db, UserCreate(username=name, email=f"{name}@example.com", password="origpw123")
    )
    if superuser:
        user.is_superuser = True
        await db.commit()
    return user


@pytest_asyncio.fixture
def make_client(engine, session_factory):
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


async def test_cannot_take_over_another_account(make_client, session_factory):
    """The original exploit: reset someone else's password, then log in as them."""
    async with session_factory() as db:
        victim = await _make_user(db, "victim")
        attacker = await _make_user(db, "attacker")
        victim_uuid = str(victim.uuid)

    async with make_client(attacker) as client:
        resp = await client.patch(
            f"/api/auth/users/{victim_uuid}", json={"password": "pwned"}
        )
        assert resp.status_code == 403

        resp = await client.delete(f"/api/auth/users/{victim_uuid}")
        assert resp.status_code == 403

    # The victim's password is untouched, so their original credentials still work.
    async with make_client(attacker) as client:
        resp = await client.post(
            "/api/auth/tokens/login",
            data={"username": "victim", "password": "pwned"},
        )
        assert resp.status_code != 200


async def test_password_change_requires_the_current_password(
    make_client, session_factory
):
    async with session_factory() as db:
        user = await _make_user(db, "selfserve")
        user_uuid = str(user.uuid)

    async with make_client(user) as client:
        # Own account, but no proof of the current password.
        resp = await client.patch(
            f"/api/auth/users/{user_uuid}", json={"password": "newpw123"}
        )
        assert resp.status_code == 403

        resp = await client.patch(
            f"/api/auth/users/{user_uuid}",
            json={"password": "newpw123", "current_password": "wrong"},
        )
        assert resp.status_code == 403

        # With the current password it goes through.
        resp = await client.patch(
            f"/api/auth/users/{user_uuid}",
            json={"password": "newpw123", "current_password": "origpw123"},
        )
        assert resp.status_code == 200

    async with make_client(user) as client:
        resp = await client.post(
            "/api/auth/tokens/login",
            data={"username": "selfserve", "password": "newpw123"},
        )
        assert resp.status_code == 200


async def test_cannot_self_add_to_a_group(make_client, session_factory):
    """Groups are principals in the grant table, so joining one inherits its grants."""
    async with session_factory() as db:
        outsider = await _make_user(db, "outsider")
        group = await UserGroupService.create(db, UserGroupCreate(name="privileged"))
        group_uuid = str(group.uuid)
        outsider_uuid = str(outsider.uuid)

    async with make_client(outsider) as client:
        resp = await client.patch(
            f"/api/auth/groups/{group_uuid}", json={"user_uuids": [outsider_uuid]}
        )
        assert resp.status_code == 403

        assert (await client.post("/api/auth/groups/", json={"name": "mine"})).status_code == 403
        assert (await client.delete(f"/api/auth/groups/{group_uuid}")).status_code == 403


async def test_system_group_is_immutable(make_client, session_factory):
    """The virtual 'Authenticated Users' group exists only for the resolver."""
    async with session_factory() as db:
        admin = await _make_user(db, "root2", superuser=True)
        await ensure_system_groups(db)
        group = await UserGroupService.get_by_name(db, AUTHENTICATED_USERS_NAME)
        assert group is not None
        group_uuid = str(group.uuid)

    async with make_client(admin) as client:
        resp = await client.patch(
            f"/api/auth/groups/{group_uuid}",
            json={"name": "tampered"},
        )
        assert resp.status_code == 403

        resp = await client.delete(f"/api/auth/groups/{group_uuid}")
        assert resp.status_code == 403


async def test_cannot_promote_self_to_superuser(make_client, session_factory):
    """Otherwise the role is worth nothing — anyone could grant it to themselves."""
    async with session_factory() as db:
        user = await _make_user(db, "climber")
        user_uuid = str(user.uuid)

    async with make_client(user) as client:
        resp = await client.patch(
            f"/api/auth/users/{user_uuid}", json={"is_superuser": True}
        )
        assert resp.status_code == 403

        resp = await client.get("/api/auth/users/me")
        assert resp.json()["is_superuser"] is False


async def test_superuser_administers_users_and_groups(make_client, session_factory):
    async with session_factory() as db:
        admin = await _make_user(db, "root", superuser=True)
        member = await _make_user(db, "member")
        member_uuid = str(member.uuid)

    async with make_client(admin) as client:
        assert (await client.get("/api/auth/users/me")).json()["is_superuser"] is True

        # Create a user, and a group with a member in it.
        resp = await client.post(
            "/api/auth/users/",
            json={"username": "hired", "email": "hired@example.com", "password": "pw123456"},
        )
        assert resp.status_code == 201

        resp = await client.post("/api/auth/groups/", json={"name": "team"})
        assert resp.status_code == 201
        group_uuid = resp.json()["uuid"]

        resp = await client.patch(
            f"/api/auth/groups/{group_uuid}", json={"user_uuids": [member_uuid]}
        )
        assert resp.status_code == 200
        assert len(resp.json()["users"]) == 1

        # Reset someone else's password without knowing the old one, and promote them.
        resp = await client.patch(
            f"/api/auth/users/{member_uuid}", json={"password": "resetpw123"}
        )
        assert resp.status_code == 200

        resp = await client.patch(
            f"/api/auth/users/{member_uuid}", json={"is_superuser": True}
        )
        assert resp.json()["is_superuser"] is True

        assert (await client.delete(f"/api/auth/groups/{group_uuid}")).status_code == 204


async def test_the_last_superuser_cannot_be_removed(make_client, session_factory):
    """Superusers are only seeded into an empty table or promoted by another, so
    losing the last one locks account administration out for good."""
    async with session_factory() as db:
        admin = await _make_user(db, "onlyroot", superuser=True)
        admin_uuid = str(admin.uuid)

    async with make_client(admin) as client:
        assert (await client.delete(f"/api/auth/users/{admin_uuid}")).status_code == 403
        resp = await client.patch(
            f"/api/auth/users/{admin_uuid}", json={"is_superuser": False}
        )
        assert resp.status_code == 403

        # With a second superuser in place, stepping down is allowed.
        resp = await client.post(
            "/api/auth/users/",
            json={"username": "heir", "email": "heir@example.com", "password": "pw123456"},
        )
        heir_uuid = resp.json()["uuid"]
        assert (
            await client.patch(
                f"/api/auth/users/{heir_uuid}", json={"is_superuser": True}
            )
        ).status_code == 200
        resp = await client.patch(
            f"/api/auth/users/{admin_uuid}", json={"is_superuser": False}
        )
        assert resp.status_code == 200


async def test_reads_stay_open_so_sharing_still_works(make_client, session_factory):
    """The Share dialog lists users and groups to grant to, so reads must not be gated."""
    async with session_factory() as db:
        user = await _make_user(db, "sharer")
        await UserGroupService.create(db, UserGroupCreate(name="team"))

    async with make_client(user) as client:
        assert (await client.get("/api/auth/users/")).status_code == 200
        assert (await client.get("/api/auth/groups/")).status_code == 200
