"""Test fixtures.

Tests run against a real PostgreSQL database (the models rely on JSONB, GIN
indexes, partial unique indexes, and advisory locks, so SQLite is not an
option). A dedicated `<db>_test` database is created automatically next to the
one configured in `.env`; the whole suite skips cleanly if Postgres is not
reachable. Auth is disabled so requests act as a single seeded user.
"""
import os

import asyncpg
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.engine import make_url

from src.core.config import settings

# Requests act as the first user (no token needed).
settings.DISABLE_AUTH = True

# Registers every model so Base.metadata is complete for create_all/drop_all.
import src.infrastructure.model_registry  # noqa: E402,F401

from main import create_app  # noqa: E402
from src.auth.users.schemas import UserCreate  # noqa: E402
from src.auth.users.services import UserService  # noqa: E402
from src.infrastructure import database as db_module  # noqa: E402
from src.infrastructure.database import (  # noqa: E402
    Base,
    make_engine,
    make_session_factory,
)

_URL = make_url(settings.DATABASE_URI)
TEST_DB_NAME = os.getenv("TEST_DB_NAME", f"{_URL.database}_test")
TEST_URL = _URL.set(database=TEST_DB_NAME)


async def _ensure_database() -> None:
    """Create the test database if it does not exist yet."""
    admin = await asyncpg.connect(
        host=_URL.host,
        port=_URL.port or 5432,
        user=_URL.username,
        password=_URL.password,
        database="postgres",
        ssl=False,
    )
    try:
        exists = await admin.fetchval(
            "SELECT 1 FROM pg_database WHERE datname = $1", TEST_DB_NAME
        )
        if not exists:
            await admin.execute(f'CREATE DATABASE "{TEST_DB_NAME}"')
    finally:
        await admin.close()


@pytest_asyncio.fixture
async def engine():
    try:
        await _ensure_database()
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"PostgreSQL test database unavailable: {exc}")

    eng = make_engine(TEST_URL)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield eng
    finally:
        async with eng.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await eng.dispose()


@pytest_asyncio.fixture
async def session_factory(engine):
    # Built through the app's own factory so the session configuration under
    # test (expire_on_commit, etc.) is identical to production's.
    return make_session_factory(engine)


@pytest_asyncio.fixture
async def user_id(session_factory) -> int:
    async with session_factory() as db:
        existing = await UserService.get_by_username(db, "tester")
        if existing is not None:
            return existing.id
        created = await UserService.create(
            db,
            UserCreate(username="tester", email="tester@example.com", password="pw"),
        )
        return created.id


@pytest_asyncio.fixture
async def client(engine, session_factory, user_id):
    # Point the app's real engine/SessionLocal at the test database instead of
    # overriding get_db. This way requests go through the exact get_db and
    # session configuration the app uses in production, so a regression there
    # (e.g. the expire_on_commit setting) would fail the tests rather than hide.
    orig_engine, orig_factory = db_module.engine, db_module.SessionLocal
    db_module.engine = engine
    db_module.SessionLocal = session_factory
    app = create_app()
    transport = ASGITransport(app=app)
    # OAuth2PasswordBearer still requires an Authorization header even when
    # DISABLE_AUTH short-circuits identity resolution, so send a dummy token.
    try:
        async with AsyncClient(
            transport=transport,
            base_url="http://test",
            headers={"Authorization": "Bearer test"},
        ) as c:
            yield c
    finally:
        db_module.engine, db_module.SessionLocal = orig_engine, orig_factory


@pytest_asyncio.fixture
async def workspace_uuid(client):
    resp = await client.post(
        "/api/workspaces/", json={"name": "w1", "display_name": "Workspace 1"}
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["uuid"]


@pytest_asyncio.fixture
async def list_uuid(client, workspace_uuid):
    resp = await client.post(
        "/api/lists/",
        json={"name": "l1", "display_name": "List 1", "workspace_uuid": workspace_uuid},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["uuid"]


@pytest.fixture
def add_column(client):
    """Return a helper that creates a column and returns its uuid."""

    async def _add(list_uuid: str, **body) -> str:
        resp = await client.post(f"/api/lists/{list_uuid}/columns/", json=body)
        assert resp.status_code == 201, resp.text
        return resp.json()["uuid"]

    return _add
