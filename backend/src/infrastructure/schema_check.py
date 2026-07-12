"""Startup guard against schema drift.

Alembic owns the schema, but it does not close every hole:

- **Autogenerate cannot see new enum values.** It diffs tables and columns, not enum
  labels, so adding a ``ColumnType`` member produces an *empty* migration. That is
  exactly how `invalid input value for enum column_type: "time"` reached a running
  server — the enum kept its old labels and blew up at query time.
- A revision can simply be forgotten, or hand-written wrong.

So compare the models against the live database on boot and refuse to start, naming
what is missing. Drift is reported, never applied — mutating a schema on boot is not
something that should happen behind your back, and with several workers it races.

Tests cannot catch drift on their own: they run against a fresh database, which is
always built correctly. Only long-lived databases drift. ``tests/test_migrations.py``
covers the other half — that the migrations reproduce the models' schema.
"""
import sqlalchemy as sa
from sqlalchemy import Connection
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateColumn

import src.infrastructure.model_registry  # noqa: F401  (populates Base.metadata)
from src.infrastructure.database import Base


class SchemaDriftError(RuntimeError):
    """The database is missing something the models expect."""


def _enum_types() -> dict[str, set[str]]:
    """Every named enum type the models use, mapped to its expected labels."""
    expected: dict[str, set[str]] = {}
    for table in Base.metadata.tables.values():
        for column in table.columns:
            type_ = column.type
            if isinstance(type_, sa.Enum) and type_.name:
                expected.setdefault(type_.name, set()).update(type_.enums)
    return expected


def find_drift(conn: Connection) -> list[str]:
    """Return remediation SQL for everything the models expect but the DB lacks.

    Runs on a *sync* connection (drive it through ``run_sync``). Only reports
    additions — a column or enum value present in the DB but absent from the models
    is harmless leftovers, not drift that breaks anything.
    """
    # A guard that compares against empty metadata would report "no drift" for every
    # database, forever. That cannot happen while model_registry is imported above,
    # but the failure would be silent, so make it loud instead.
    if not Base.metadata.tables:
        raise SchemaDriftError(
            "Base.metadata is empty — no model modules were imported, so there is "
            "nothing to check against. See src/infrastructure/model_registry.py."
        )

    inspector = sa.inspect(conn)
    dialect = postgresql.dialect()
    remedies: list[str] = []

    existing_tables = set(inspector.get_table_names())
    for table in Base.metadata.sorted_tables:
        if table.name not in existing_tables:
            # create_all should have made it; if it did not, that is worth saying.
            remedies.append(f"-- table {table.name!r} is missing entirely")
            continue

        db_columns = {col["name"] for col in inspector.get_columns(table.name)}
        for column in table.columns:
            if column.name in db_columns:
                continue
            ddl = CreateColumn(column).compile(dialect=dialect)
            remedies.append(f"ALTER TABLE {table.name} ADD COLUMN {ddl};")

    # Enum values: ADD VALUE cannot run inside a transaction block that then uses the
    # value, so these must be applied on an autocommit connection.
    for type_name, labels in _enum_types().items():
        rows = conn.execute(
            sa.text(
                "SELECT e.enumlabel FROM pg_enum e "
                "JOIN pg_type t ON t.oid = e.enumtypid WHERE t.typname = :name"
            ),
            {"name": type_name},
        ).scalars().all()
        if not rows:
            continue  # type absent entirely — create_all makes it with all labels
        for label in sorted(labels - set(rows)):
            remedies.append(
                f"ALTER TYPE {type_name} ADD VALUE IF NOT EXISTS '{label}';"
            )

    return remedies


def raise_on_drift(conn: Connection) -> None:
    remedies = find_drift(conn)
    if not remedies:
        return

    inspector = sa.inspect(conn)
    tables = set(inspector.get_table_names())
    if not tables or tables == {"alembic_version"}:
        raise SchemaDriftError(
            "The database has no schema. Run `alembic upgrade head` before starting "
            "the app — the app no longer creates tables itself."
        )

    statements = "\n".join(f"  {line}" for line in remedies)
    raise SchemaDriftError(
        "The database is behind the models. Usually this means a migration is "
        "missing: run `alembic upgrade head`.\n\n"
        "If you are already at head, the revision is incomplete — most likely a new "
        "enum value, which autogenerate cannot see and you must write by hand. "
        "Everything below is additive and preserves data:\n\n"
        f"{statements}\n\n"
        "ALTER TYPE ... ADD VALUE cannot run inside a transaction that then uses the "
        "value; in a migration wrap it in `with op.get_context().autocommit_block():`."
    )
