from typing import AsyncGenerator, Optional

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    create_async_engine,
    async_sessionmaker,
)
from sqlalchemy.orm import (
    DeclarativeBase,
)

from src.core.config import settings


engine = create_async_engine(
    settings.DATABASE_URI,
    pool_pre_ping=True,
)
# expire_on_commit=False keeps ORM instances usable after commit. With the
# default (True), reading any attribute of an object after db.commit() triggers
# a lazy reload; under the async asyncpg driver that lazy IO happens outside the
# greenlet and raises MissingGreenlet. Services pass loaded objects (e.g. the
# parent list) into helpers after committing, so they must not re-expire.
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with SessionLocal() as session:
        yield session

async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
