"""The startup schema-drift guard.

These deliberately damage the test database to reproduce the two drifts that have
actually happened: a column added to a model after the table existed, and an enum
value added after the type existed. create_all silently ignores both.
"""
import pytest
import sqlalchemy as sa

from src.infrastructure.schema_check import (
    SchemaDriftError,
    find_drift,
    raise_on_drift,
)


async def test_a_fresh_database_has_no_drift(engine):
    async with engine.begin() as conn:
        assert await conn.run_sync(find_drift) == []


async def test_a_missing_column_is_reported(engine):
    """The is_superuser case: model gains a column, existing table does not."""
    async with engine.begin() as conn:
        await conn.execute(sa.text("ALTER TABLE users DROP COLUMN is_superuser"))
        remedies = await conn.run_sync(find_drift)

        assert any("ADD COLUMN is_superuser" in r for r in remedies), remedies
        assert all(r.startswith("ALTER TABLE users") for r in remedies)

        with pytest.raises(SchemaDriftError, match="is_superuser"):
            await conn.run_sync(raise_on_drift)

        # Put it back so the rest of the session sees a sane schema.
        await conn.execute(
            sa.text(
                "ALTER TABLE users ADD COLUMN is_superuser boolean "
                "NOT NULL DEFAULT false"
            )
        )
        assert await conn.run_sync(find_drift) == []


async def test_a_missing_enum_value_is_reported(engine):
    """The ColumnType.time case: model gains an enum member, existing type does not.

    Postgres cannot remove an enum label, so rebuild the type without it -- which is
    exactly the shape a long-lived database was in.

    The rebuilt type below still carries 'long_text', which the models no longer
    have. That is the other half of the contract, and the merge_long_text_into_text
    migration leans on it: an extra label is a leftover, not drift, so it is absent
    from the remedies.
    """
    async with engine.begin() as conn:
        await conn.execute(sa.text("ALTER TYPE column_type RENAME TO column_type_old"))
        await conn.execute(
            sa.text(
                "CREATE TYPE column_type AS ENUM "
                "('text','long_text','number','checkbox','choice','lookup','date')"
            )
        )
        await conn.execute(
            sa.text(
                "ALTER TABLE columns ALTER COLUMN type TYPE column_type "
                "USING type::text::column_type"
            )
        )
        await conn.execute(sa.text("DROP TYPE column_type_old"))

        remedies = await conn.run_sync(find_drift)
        assert "ALTER TYPE column_type ADD VALUE IF NOT EXISTS 'time';" in remedies
        assert "ALTER TYPE column_type ADD VALUE IF NOT EXISTS 'datetime';" in remedies
        # A label the database has and the models do not is left alone.
        assert not any("long_text" in remedy for remedy in remedies)

        with pytest.raises(SchemaDriftError, match="column_type"):
            await conn.run_sync(raise_on_drift)
