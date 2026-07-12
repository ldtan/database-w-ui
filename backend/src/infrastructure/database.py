from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    create_async_engine,
    async_sessionmaker,
)
from sqlalchemy.orm import (
    DeclarativeBase,
)

from src.core.config import settings


def make_engine(url: str) -> AsyncEngine:
    return create_async_engine(url, pool_pre_ping=True)


def make_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    # expire_on_commit=False keeps ORM instances usable after commit. With the
    # default (True), reading any attribute of an object after db.commit()
    # triggers a lazy reload; under the async asyncpg driver that lazy IO happens
    # outside the greenlet and raises MissingGreenlet. Services pass loaded
    # objects (e.g. the parent list) into helpers after committing, so they must
    # not re-expire. Tests build their session factory through this same function
    # so their configuration can never drift from the app's (see conftest.py).
    return async_sessionmaker(engine, expire_on_commit=False)


engine = make_engine(settings.DATABASE_URI)
SessionLocal = make_session_factory(engine)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with SessionLocal() as session:
        yield session

async def init_db():
    """Verify the database matches the models. Does not create or alter anything.

    Alembic owns the schema now: run `alembic upgrade head` before starting the app.
    Migrating here instead would race across workers, and silently mutating a schema
    on boot is the very thing this check exists to prevent.

    The check still earns its keep alongside Alembic, because autogenerate cannot see
    new *enum values* — it diffs tables and columns, not enum labels. A forgotten or
    incomplete revision fails startup with the exact SQL rather than a 500 mid-request.
    """
    from src.infrastructure.schema_check import raise_on_drift

    async with engine.begin() as conn:
        await conn.run_sync(raise_on_drift)
