"""The migrations must reproduce exactly what the models describe.

Two ways this quietly breaks, both of which have already happened here:

- A model changes and nobody generates a revision. Tests would still pass, because
  the test database is built by ``create_all`` from the models — never by the
  migrations — so the two paths can drift apart indefinitely.
- A model module is missing from ``migrations/env.py``. That is not a no-op:
  autogenerate would see the table as absent from the models and emit a DROP TABLE.

So build a database *through the migrations* and compare it to the models.
"""
import asyncio
import os

import pytest
import pytest_asyncio
import sqlalchemy as sa
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy.ext.asyncio import create_async_engine

from src.infrastructure.database import Base
from src.infrastructure.schema_check import find_drift

from conftest import TEST_URL, _ensure_database

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MIGRATED_DB = f"{TEST_URL.database}_migrated"
MIGRATED_URL = TEST_URL.set(database=MIGRATED_DB)


def _alembic_config() -> Config:
    config = Config(os.path.join(BACKEND_DIR, "alembic.ini"))
    config.set_main_option("script_location", os.path.join(BACKEND_DIR, "migrations"))
    # env.py only falls back to the app's settings when this is unset — so setting it
    # here is what keeps the tests (including `downgrade base`!) off the real database.
    # % is ConfigParser interpolation syntax, and a url-encoded password contains it.
    url = MIGRATED_URL.render_as_string(hide_password=False).replace("%", "%%")
    config.set_main_option("sqlalchemy.url", url)
    return config


@pytest_asyncio.fixture
async def migrated_url():
    """An empty database, used only by these tests."""
    await _ensure_database()  # makes sure the server is reachable / test db exists

    admin = create_async_engine(
        TEST_URL.set(database="postgres").render_as_string(hide_password=False),
        isolation_level="AUTOCOMMIT",
    )
    try:
        async with admin.connect() as conn:
            await conn.execute(sa.text(f'DROP DATABASE IF EXISTS "{MIGRATED_DB}"'))
            await conn.execute(sa.text(f'CREATE DATABASE "{MIGRATED_DB}"'))
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"PostgreSQL unavailable: {exc}")
    finally:
        await admin.dispose()

    yield MIGRATED_URL

    admin = create_async_engine(
        TEST_URL.set(database="postgres").render_as_string(hide_password=False),
        isolation_level="AUTOCOMMIT",
    )
    async with admin.connect() as conn:
        await conn.execute(sa.text(f'DROP DATABASE IF EXISTS "{MIGRATED_DB}"'))
    await admin.dispose()


async def _upgrade_head() -> None:
    # Alembic's command layer is sync and spins its own event loop in env.py, so it
    # cannot run on this one.
    await asyncio.to_thread(command.upgrade, _alembic_config(), "head")


async def _downgrade_base() -> None:
    await asyncio.to_thread(command.downgrade, _alembic_config(), "base")


async def _upgrade_to(revision: str) -> None:
    await asyncio.to_thread(command.upgrade, _alembic_config(), revision)


async def test_migrations_produce_the_models_schema(migrated_url):
    """Catches a model change with no revision, and a model missing from env.py."""
    await _upgrade_head()

    engine = create_async_engine(migrated_url.render_as_string(hide_password=False))
    async with engine.begin() as conn:
        drift = await conn.run_sync(find_drift)
    await engine.dispose()

    assert drift == [], f"migrated schema does not match the models: {drift}"


async def test_no_pending_autogenerate_diff(migrated_url):
    """Stronger than find_drift for tables/columns: it also catches *extra* schema,
    so a migration that adds something the models dropped is caught too."""
    await _upgrade_head()

    engine = create_async_engine(migrated_url.render_as_string(hide_password=False))

    def _diff(sync_conn):
        context = MigrationContext.configure(sync_conn, opts={"compare_type": True})
        return compare_metadata(context, Base.metadata)

    async with engine.begin() as conn:
        diff = await conn.run_sync(_diff)
    await engine.dispose()

    assert diff == [], (
        "autogenerate still has changes to make — a revision is missing:\n"
        f"{diff}"
    )


async def test_the_circular_foreign_key_is_created(migrated_url):
    """tables.primary_column_id -> columns.id is use_alter (the two tables reference
    each other). SQLAlchemy omits such keys from CREATE TABLE and autogenerate does
    not emit the follow-up ALTER, so the baseline adds it by hand — and it silently
    would not exist otherwise."""
    await _upgrade_head()

    engine = create_async_engine(migrated_url.render_as_string(hide_password=False))
    async with engine.begin() as conn:
        names = (
            await conn.execute(
                sa.text(
                    "SELECT conname FROM pg_constraint "
                    "WHERE conrelid = 'tables'::regclass AND contype = 'f'"
                )
            )
        ).scalars().all()
    await engine.dispose()

    assert "fk_tables_primary_column_id" in names


async def test_long_text_columns_become_multiline_text(migrated_url):
    """The data half of merge_long_text_into_text.

    Every other migration test starts from an empty database, where a data
    migration is a no-op that passes for the wrong reason. This one stops at the
    revision before, plants a `long_text` column with a record in it, and then
    upgrades — so what is asserted is the rewrite itself: the type moves, the
    editor is preserved as `text_mode`, and the cell is not touched (both types
    always held the same plain string, so any change to it would be a bug).

    The soft-deleted column matters as much as the live one: leaving it behind
    would let a restore resurrect a type ColumnType no longer has, which raises
    on the next read rather than failing here.

    **This test straddles the List -> Table rename.** The inserts below run at
    `d2f6a8c41b93`, which predates it, so they must keep the old identifiers
    (`lists`, `list_id`, `list_items`); the assertions after `_upgrade_head()` run
    on the renamed schema and must use the new ones (`tables`, `table_id`,
    `table_records`). Making both halves agree is always wrong — one of them
    will be talking to a schema that does not exist yet, or any more.
    """
    await _upgrade_to("d2f6a8c41b93")  # the revision before the merge

    engine = create_async_engine(migrated_url.render_as_string(hide_password=False))
    body = "line one\nline two"
    async with engine.begin() as conn:
        # created_at/updated_at default in Python, not in the database, so raw
        # inserts have to supply them.
        await conn.execute(
            sa.text(
                "INSERT INTO users"
                " (uuid, username, email, password, is_active, settings,"
                "  created_at, updated_at)"
                " VALUES (gen_random_uuid(), 'u', 'u@example.com', 'x', true,"
                "         '{}'::jsonb, now(), now())"
            )
        )
        await conn.execute(
            sa.text(
                "INSERT INTO workspaces"
                " (uuid, name, display_name, owner_id, settings, created_at, updated_at)"
                " SELECT gen_random_uuid(), 'w', 'W', id, '{}'::jsonb, now(), now()"
                "   FROM users"
            )
        )
        await conn.execute(
            sa.text(
                "INSERT INTO lists"
                " (uuid, name, display_name, workspace_id, settings, created_at, updated_at)"
                " SELECT gen_random_uuid(), 'l', 'L', id, '{}'::jsonb, now(), now()"
                "   FROM workspaces"
            )
        )
        # One live, one soft-deleted, one already-`text` column that must not move.
        await conn.execute(
            sa.text(
                "INSERT INTO columns"
                " (uuid, name, display_name, type, position, config, list_id,"
                "  deleted_at, created_at, updated_at)"
                " SELECT gen_random_uuid(), v.n, v.n, v.t::column_type, 0,"
                "        '{\"required\": true}'::jsonb, l.id, v.d, now(), now()"
                "   FROM lists l, (VALUES"
                "     ('notes', 'long_text', NULL::timestamptz),"
                "     ('old', 'long_text', now()),"
                "     ('title', 'text', NULL)"
                "   ) AS v(n, t, d)"
            )
        )
        await conn.execute(
            sa.text(
                "INSERT INTO list_items"
                " (uuid, list_id, data, created_at, updated_at)"
                " SELECT gen_random_uuid(), l.id,"
                # ::text or asyncpg cannot infer the parameter's type in here.
                "        jsonb_build_object(c.uuid::text, :body ::text), now(), now()"
                "   FROM lists l JOIN columns c ON c.list_id = l.id"
                "  WHERE c.name = 'notes'"
            ),
            {"body": body},
        )
    await engine.dispose()

    await _upgrade_head()

    engine = create_async_engine(migrated_url.render_as_string(hide_password=False))
    async with engine.begin() as conn:
        rows = (
            await conn.execute(
                sa.text(
                    "SELECT name, type::text AS type, config,"
                    "       deleted_at IS NOT NULL AS deleted"
                    "  FROM columns ORDER BY name"
                )
            )
        ).all()
        by_name = {r.name: r for r in rows}
        cell = (
            await conn.execute(
                sa.text(
                    "SELECT i.data ->> c.uuid::text FROM table_records i"
                    "  JOIN columns c ON c.table_id = i.table_id AND c.name = 'notes'"
                )
            )
        ).scalar()
        labels = (
            await conn.execute(
                sa.text(
                    "SELECT e.enumlabel FROM pg_enum e JOIN pg_type t"
                    " ON t.oid = e.enumtypid WHERE t.typname = 'column_type'"
                )
            )
        ).scalars().all()
    await engine.dispose()

    assert by_name["notes"].type == "text"
    assert by_name["notes"].config["text_mode"] == "multiline"
    # The rewrite adds a key; it does not replace the config.
    assert by_name["notes"].config["required"] is True

    assert by_name["old"].deleted
    assert by_name["old"].type == "text"
    assert by_name["old"].config["text_mode"] == "multiline"

    # A column that was already `text` keeps its implicit single-line editor.
    assert by_name["title"].type == "text"
    assert "text_mode" not in by_name["title"].config

    assert cell == body, "record cells must be untouched"
    # Deliberately left on the enum: Postgres cannot drop a label, and find_drift
    # ignores extras. See the migration's docstring.
    assert "long_text" in labels


async def test_upgrade_then_downgrade_is_clean(migrated_url):
    """Revisions must be reversible, not write-only."""
    await _upgrade_head()
    await _downgrade_base()

    engine = create_async_engine(migrated_url.render_as_string(hide_password=False))
    async with engine.begin() as conn:
        tables = (
            await conn.execute(
                sa.text(
                    "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
                )
            )
        ).scalars().all()
    await engine.dispose()

    # alembic_version is Alembic's own bookkeeping and outlives a downgrade.
    assert set(tables) <= {"alembic_version"}, f"downgrade left tables behind: {tables}"
