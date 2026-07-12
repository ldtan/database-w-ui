import asyncio
import os
import sys
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context

# Run from anywhere (pytest, a deploy script) without relying on the cwd.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core.config import settings  # noqa: E402
from src.infrastructure.database import Base  # noqa: E402

# Registers every model on Base.metadata. A model missing from that list is NOT a
# no-op here: autogenerate would see its table as absent from the models and emit a
# DROP TABLE. tests/test_migrations.py fails if the registry ever falls behind.
import src.infrastructure.model_registry  # noqa: E402,F401

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# The URL lives in the app's settings (and thus .env), not in alembic.ini — one copy
# of the credentials, and `alembic` targets whatever database the app itself would.
#
# Only fill it in if a caller has not already chosen one. tests/test_migrations.py
# points Alembic at a scratch database; overwriting that here would run the tests --
# including `downgrade base` -- against the real database and drop every table in it.
if not config.get_main_option("sqlalchemy.url", None):
    # Escaped because ConfigParser treats % as interpolation syntax, and a
    # url-encoded password (e.g. ! -> %21) would otherwise blow up.
    config.set_main_option(
        "sqlalchemy.url", settings.DATABASE_URI.replace("%", "%%")
    )

target_metadata = Base.metadata

# compare_type: detect a column whose type changed, not just added/dropped ones.
# NOTE: autogenerate still cannot see new *enum values* — it diffs tables and
# columns, not enum labels. Adding a ColumnType member produces an EMPTY migration.
# Write those by hand (ALTER TYPE ... ADD VALUE, inside op.get_context().
# autocommit_block()); src/infrastructure/schema_check.py fails startup if you forget.
_CONFIGURE = {"target_metadata": target_metadata, "compare_type": True}


def run_migrations_offline() -> None:
    """Emit SQL to stdout instead of running it (`alembic upgrade head --sql`)."""
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        **_CONFIGURE,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, **_CONFIGURE)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
