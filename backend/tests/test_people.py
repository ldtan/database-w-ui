"""Person (profile) CRUD.

A person record is personal data, so it is readable only by its owner or a
superuser — unlike /auth/users, whose reads stay open for the Share dialog.
"""
from contextlib import asynccontextmanager

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from main import create_app
from src.auth.dependencies import get_current_user
from src.auth.users.schemas import UserCreate
from src.auth.users.services import UserService
from src.infrastructure import database as db_module


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


BODY = {
    "first_name": "Ada",
    "middle_name": "King",
    "last_name": "Lovelace",
    "birth_date": "1815-12-10",
}


async def test_create_read_update_delete_own_person(make_client, session_factory):
    async with session_factory() as db:
        user = await _make_user(db, "ada")
        user_uuid = str(user.uuid)

    async with make_client(user) as client:
        # No profile yet — an account can exist without one.
        assert (await client.get("/api/people/me")).status_code == 404

        resp = await client.post("/api/people/", json=BODY)
        assert resp.status_code == 201, resp.text
        person = resp.json()
        assert person["first_name"] == "Ada"
        assert person["birth_date"] == "1815-12-10"
        assert person["user"]["uuid"] == user_uuid
        uuid = person["uuid"]

        assert (await client.get("/api/people/me")).json()["uuid"] == uuid
        assert (await client.get(f"/api/people/{uuid}")).status_code == 200

        # Omitting a field leaves it alone; an explicit null clears a nullable one.
        resp = await client.patch(f"/api/people/{uuid}", json={"last_name": "Byron"})
        assert resp.json()["last_name"] == "Byron"
        assert resp.json()["middle_name"] == "King"

        resp = await client.patch(f"/api/people/{uuid}", json={"middle_name": None})
        assert resp.json()["middle_name"] is None

        assert (await client.delete(f"/api/people/{uuid}")).status_code == 204
        assert (await client.get("/api/people/me")).status_code == 404


async def test_one_person_per_user(make_client, session_factory):
    async with session_factory() as db:
        user = await _make_user(db, "solo")

    async with make_client(user) as client:
        assert (await client.post("/api/people/", json=BODY)).status_code == 201
        # The unique index is what enforces this, not a check in the service.
        assert (await client.post("/api/people/", json=BODY)).status_code == 409


async def test_soft_deleted_profile_does_not_block_a_new_one(
    make_client, session_factory
):
    """The unique index is partial on deleted_at IS NULL."""
    async with session_factory() as db:
        user = await _make_user(db, "again")

    async with make_client(user) as client:
        uuid = (await client.post("/api/people/", json=BODY)).json()["uuid"]
        assert (await client.delete(f"/api/people/{uuid}")).status_code == 204
        assert (await client.post("/api/people/", json=BODY)).status_code == 201


async def test_person_records_are_private_to_owner_and_superuser(
    make_client, session_factory
):
    async with session_factory() as db:
        owner = await _make_user(db, "owner")
        nosy = await _make_user(db, "nosy")
        admin = await _make_user(db, "root", superuser=True)

    async with make_client(owner) as client:
        uuid = (await client.post("/api/people/", json=BODY)).json()["uuid"]

    async with make_client(nosy) as client:
        assert (await client.get(f"/api/people/{uuid}")).status_code == 403
        assert (await client.patch(f"/api/people/{uuid}", json={"last_name": "X"})).status_code == 403
        assert (await client.delete(f"/api/people/{uuid}")).status_code == 403
        # Listing everyone's personal data is superuser-only.
        assert (await client.get("/api/people/")).status_code == 403

    async with make_client(admin) as client:
        assert (await client.get(f"/api/people/{uuid}")).status_code == 200
        assert len((await client.get("/api/people/")).json()) == 1


async def test_only_a_superuser_may_create_a_profile_for_someone_else(
    make_client, session_factory
):
    async with session_factory() as db:
        target = await _make_user(db, "target")
        other = await _make_user(db, "other")
        admin = await _make_user(db, "admin2", superuser=True)
        target_uuid = str(target.uuid)

    async with make_client(other) as client:
        resp = await client.post("/api/people/", json={**BODY, "user_uuid": target_uuid})
        assert resp.status_code == 403

    async with make_client(admin) as client:
        resp = await client.post("/api/people/", json={**BODY, "user_uuid": target_uuid})
        assert resp.status_code == 201
        assert resp.json()["user"]["uuid"] == target_uuid


async def test_birth_date_cannot_be_in_the_future(make_client, session_factory):
    async with session_factory() as db:
        user = await _make_user(db, "timetraveller")

    async with make_client(user) as client:
        resp = await client.post("/api/people/", json={**BODY, "birth_date": "3000-01-01"})
        assert resp.status_code == 422


async def test_prefix_and_suffix_round_trip(client):
    """A field added to PersonBase must actually reach the row — `create` used to
    table its columns by hand, so a new one was accepted by the API and dropped."""
    created = await client.post(
        "/api/people/",
        json={
            "name_prefix": "Dr",
            "first_name": "Grace",
            "last_name": "Hopper",
            "name_suffix": "Jr",
        },
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert (body["name_prefix"], body["name_suffix"]) == ("Dr", "Jr")

    me = (await client.get("/api/people/me")).json()
    assert (me["name_prefix"], me["name_suffix"]) == ("Dr", "Jr")

    # And an explicit null clears them, as with every other nullable person field.
    patched = await client.patch(
        f"/api/people/{body['uuid']}", json={"name_prefix": None, "name_suffix": None}
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["name_prefix"] is None
    assert patched.json()["name_suffix"] is None
