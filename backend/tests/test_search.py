"""Server-side search on the user/group/person listings.

The People page and the Share dialog search these. Filtering client-side over one
fetched page would mean a search silently fails to find anything past the page
limit -- and, in the Share dialog, that you could not grant to that principal at all.
"""
from contextlib import asynccontextmanager

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from main import create_app
from src.auth.dependencies import get_current_user
from src.auth.users.schemas import UserCreate, UserGroupCreate
from src.auth.users.services import UserGroupService, UserService
from src.infrastructure import database as db_module
from src.people.schemas import PersonCreate
from src.people.services import PersonService


async def _make_user(db, name: str, superuser: bool = False, email: str = None):
    user = await UserService.create(
        db,
        UserCreate(
            username=name,
            email=email or f"{name}@example.com",
            password="origpw123",
        ),
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


async def test_user_search_matches_username_and_email(make_client, session_factory):
    async with session_factory() as db:
        admin = await _make_user(db, "root", superuser=True)
        await _make_user(db, "alice", email="alice@corp.example")
        await _make_user(db, "bob", email="bob@other.example")

    async with make_client(admin) as client:
        found = (await client.get("/api/auth/users/?q=alic")).json()
        assert [u["username"] for u in found] == ["alice"]

        # Email matches too, and matching is case-insensitive.
        found = (await client.get("/api/auth/users/?q=OTHER.EXAMPLE")).json()
        assert [u["username"] for u in found] == ["bob"]

        assert (await client.get("/api/auth/users/?q=nobody")).json() == []
        # No q at all still lists everyone.
        assert len((await client.get("/api/auth/users/")).json()) == 3


async def test_search_finds_users_beyond_the_first_page(make_client, session_factory):
    """The whole point: filtering client-side over one page would miss this user."""
    async with session_factory() as db:
        admin = await _make_user(db, "root", superuser=True)
        for i in range(12):
            await _make_user(db, f"filler{i:02d}")
        await _make_user(db, "needle")

    async with make_client(admin) as client:
        page = (await client.get("/api/auth/users/?limit=5")).json()
        assert len(page) == 5
        assert "needle" not in [u["username"] for u in page]

        # ...but searching finds it, even with the same small page size.
        found = (await client.get("/api/auth/users/?q=needle&limit=5")).json()
        assert [u["username"] for u in found] == ["needle"]


async def test_wildcards_in_a_search_term_are_literal(make_client, session_factory):
    """`%` and `_` are LIKE wildcards; typed into a search box they must not act as such."""
    async with session_factory() as db:
        admin = await _make_user(db, "root", superuser=True)
        await _make_user(db, "alice")
        await _make_user(db, "a_b", email="a_b@example.com")

    async with make_client(admin) as client:
        # A bare % would match everything if unescaped.
        assert (await client.get("/api/auth/users/?q=%25")).json() == []
        # `_` matches any single char in LIKE, so "a_b" must not match "alice"...
        found = (await client.get("/api/auth/users/?q=a_b")).json()
        assert [u["username"] for u in found] == ["a_b"]


async def test_group_search(make_client, session_factory):
    async with session_factory() as db:
        admin = await _make_user(db, "root", superuser=True)
        await UserGroupService.create(
            db, UserGroupCreate(name="engineering", description="builds things")
        )
        await UserGroupService.create(db, UserGroupCreate(name="sales"))

    async with make_client(admin) as client:
        found = (await client.get("/api/auth/groups/?q=engin")).json()
        assert [g["name"] for g in found] == ["engineering"]
        # Description matches too.
        found = (await client.get("/api/auth/groups/?q=builds")).json()
        assert [g["name"] for g in found] == ["engineering"]


async def test_person_names_are_searchable_only_by_a_superuser(
    make_client, session_factory
):
    """/auth/users is readable by everyone, so matching on person names for an
    ordinary caller would let them probe for someone's real name by watching which
    searches return a hit."""
    async with session_factory() as db:
        admin = await _make_user(db, "root", superuser=True)
        nosy = await _make_user(db, "nosy")
        target = await _make_user(db, "gh")
        await PersonService.create(
            db,
            target.id,
            PersonCreate(first_name="Grace", middle_name=None, last_name="Hopper"),
        )

    async with make_client(admin) as client:
        found = (await client.get("/api/auth/users/?q=hopper")).json()
        assert [u["username"] for u in found] == ["gh"]

    async with make_client(nosy) as client:
        # Same query, ordinary user: the name is not searchable.
        assert (await client.get("/api/auth/users/?q=hopper")).json() == []
        # Username search still works for them.
        assert [u["username"] for u in (await client.get("/api/auth/users/?q=gh")).json()] == ["gh"]


async def test_person_search_by_name(make_client, session_factory):
    async with session_factory() as db:
        admin = await _make_user(db, "root", superuser=True)
        target = await _make_user(db, "grace")
        await PersonService.create(
            db,
            target.id,
            PersonCreate(first_name="Grace", middle_name=None, last_name="Hopper"),
        )

    async with make_client(admin) as client:
        found = (await client.get("/api/people/?q=hopp")).json()
        assert [p["last_name"] for p in found] == ["Hopper"]
        assert (await client.get("/api/people/?q=turing")).json() == []
