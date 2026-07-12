"""Startup guard against schema drift.

There is no migration tool here: the schema comes from ``Base.metadata.create_all``,
which only ever creates things that are *missing* and never alters what already
exists. A brand-new table appears by itself, but a new **column** or a new **enum
value** is silently skipped on a database that already exists — so the DB keeps its
old shape and fails at query time (a 500 deep in a request) rather than at startup.

Tests cannot catch this: they run against a fresh database, where everything is
missing and therefore created correctly. Only long-lived databases drift.

So compare the models against the live database on boot and refuse to start, naming
the exact SQL to run. Drift is reported, never applied — mutating a schema on boot
is not something that should happen behind your back.
"""
import sqlalchemy as sa
from sqlalchemy import Connection
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateColumn

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
    statements = "\n".join(f"  {line}" for line in remedies)
    raise SchemaDriftError(
        "The database is missing schema the models expect. create_all does not "
        "alter existing tables or enum types, so this must be applied by hand "
        "(all of it is additive and preserves data):\n\n"
        f"{statements}\n\n"
        "Apply the ALTER TYPE statements on an autocommit connection."
    )
