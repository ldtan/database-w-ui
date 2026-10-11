"""rename the domain noun: lists -> tables, list_items -> table_records

The central object was called a `list` but is a table: it has columns, views, forms,
metrics, a primary column, and holds records. The old name also squatted on a Python
builtin, which is why the code carried 849 `lst` variables and a `List as ListModel`
alias. Records get the same treatment: the frontend already called them records while
the backend said `ListItem`.

Nothing here changes a row's meaning — it is a pure renaming of schema objects plus
one JSONB key. Both halves are therefore **exactly reversible**, unlike
`a9c3f5e7d2b8`, whose `downgrade` can legitimately `pass` because labels cannot
recover slugs. `downgrade()` here restores every name `upgrade()` touches, which
`test_upgrade_then_downgrade_is_clean` requires: the baseline drops by literal name
(`op.drop_table('lists')`), and revisions below this one still speak the old names.

Four things worth knowing:

- **`ALTER TABLE ... RENAME` carries definitions, not names.** Index and constraint
  *definitions* follow the table (they reference it by OID), and so do the partial
  `WHERE deleted_at IS NULL` predicates — which never mention `list_id` anyway. The
  *names* do not follow, so all 17 indexes, 7 constraints and 2 sequences are renamed
  explicitly. They have to be: `test_no_pending_autogenerate_diff` compares indexes
  **by name**, so a leftover `uq_lists_workspace_name` beside a model-declared
  `uq_tables_workspace_name` reads as remove_index + add_index and fails.

- **The circular FK is renamed, never dropped.** `tables.primary_column_id ->
  columns.id` is `use_alter` because `columns.table_id -> tables.id` points back. A
  rename neither validates nor revalidates a constraint, so the cycle imposes no
  ordering here and there is no reason to drop and re-add — the one move that could
  silently lose it. `test_the_circular_foreign_key_is_created` guards that.

- **`ALTER TYPE ... RENAME VALUE` is catalog-only.** An enum value is stored on disk
  as its `pg_enum` row's OID, so renaming the label rewrites no heap and invalidates
  no index: all five indexes on `permission_grants.resource_type` stay valid, and
  every existing grant keeps pointing at the same value. It is also transactional
  (PG10+) and must *not* go in an `autocommit_block()` — that restriction belongs to
  `ADD VALUE`, which is what the warnings in `env.py` and `schema_check.py` are about.

- **Named NOT NULL constraints are deliberately left alone.** PG17+ catalogues them
  (`lists_id_not_null` and 16 siblings here on 18.4), but they are auto-generated,
  invisible to the app, and invisible to Alembic — `compare_metadata` diffs column
  nullability, not constraint names. Renaming them would make this revision fail
  outright on any server old enough not to have them, which is a deploy broken for no
  gain. They keep the old spelling; nothing reads it.

The whole revision is one transaction (Postgres DDL is transactional, and so is
`RENAME VALUE`), so a failure anywhere leaves the schema exactly as it was. That is
why nothing below is guarded by an existence check.

Revision ID: f4a9c2e71b85
Revises: e2c7a91d4b83
Create Date: 2026-10-11 10:40:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'f4a9c2e71b85'
down_revision: Union[str, Sequence[str], None] = 'e2c7a91d4b83'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# (old, new) for every schema object whose *name* carries the old noun. Written out
# rather than derived so the downgrade is the same data read backwards, and so a
# reader can check it against `\d` output.
TABLES = [
    ("lists", "tables"),
    ("list_items", "table_records"),
]

# The FK column on every child of a table, plus the one on records.
TABLE_ID_COLUMNS = ["columns", "forms", "views", "metrics", "table_records"]

# Plain indexes: the `ix_` ones from `index=True`, and the `uq_` partial uniques,
# which are indexes rather than constraints because they carry a WHERE clause.
INDEXES = [
    ("ix_lists_workspace_id", "ix_tables_workspace_id"),
    ("uq_lists_workspace_name", "uq_tables_workspace_name"),
    ("ix_columns_list_id", "ix_columns_table_id"),
    ("uq_columns_list_name", "uq_columns_table_name"),
    ("ix_forms_list_id", "ix_forms_table_id"),
    ("uq_forms_list_name", "uq_forms_table_name"),
    ("ix_views_list_id", "ix_views_table_id"),
    ("uq_views_list_name", "uq_views_table_name"),
    ("ix_metrics_list_id", "ix_metrics_table_id"),
    ("uq_metrics_list_name", "uq_metrics_table_name"),
    ("ix_list_items_data", "ix_table_records_data"),
    ("ix_list_items_list_id", "ix_table_records_table_id"),
    ("ix_list_items_list_id_id", "ix_table_records_table_id_id"),
]

# Constraints, keyed by the table they sit on (post-rename name). Primary keys and
# unique constraints own an index of the same name, so renaming the constraint renames
# that index too — which is why they are here rather than in INDEXES.
CONSTRAINTS = [
    ("tables", "lists_pkey", "tables_pkey"),
    ("tables", "lists_uuid_key", "tables_uuid_key"),
    ("tables", "lists_workspace_id_fkey", "tables_workspace_id_fkey"),
    ("tables", "fk_lists_primary_column_id", "fk_tables_primary_column_id"),
    ("table_records", "list_items_pkey", "table_records_pkey"),
    ("table_records", "list_items_uuid_key", "table_records_uuid_key"),
    ("table_records", "list_items_list_id_fkey", "table_records_table_id_fkey"),
    ("columns", "columns_list_id_fkey", "columns_table_id_fkey"),
    ("forms", "forms_list_id_fkey", "forms_table_id_fkey"),
    ("views", "views_list_id_fkey", "views_table_id_fkey"),
    ("metrics", "metrics_list_id_fkey", "metrics_table_id_fkey"),
]

SEQUENCES = [
    ("lists_id_seq", "tables_id_seq"),
    ("list_items_id_seq", "table_records_id_seq"),
]

# permission_grants.resource_type. `item` moves with `list` because the thing it
# points at is now a record.
ENUM_VALUES = [("list", "table"), ("item", "record")]

# The lookup column's target, inside `columns.config`.
OLD_KEY = "target_list_uuid"
NEW_KEY = "target_table_uuid"


def _rename_key(old: str, new: str) -> None:
    """Move one JSONB key on every lookup column that has it.

    `->` and not `->>`: the key is *nullable, not merely absent* — deleting the target
    table clears it in place, so the stored value is really JSON `null`. `->>` would
    hand `jsonb_build_object` an SQL NULL and quietly change the shape to a key that
    is no longer there. The `?` guard makes a re-run a no-op, and soft-deleted rows are
    included on purpose: a restore must not resurrect a key the code stopped reading.
    """
    op.execute(
        sa.text(
            f"""
            UPDATE columns
               SET config = (config - '{old}')
                            || jsonb_build_object('{new}', config -> '{old}')
             WHERE config ? '{old}'
            """
        )
    )


def upgrade() -> None:
    for old, new in TABLES:
        op.rename_table(old, new)

    for table in TABLE_ID_COLUMNS:
        op.alter_column(table, "list_id", new_column_name="table_id")

    for old, new in INDEXES:
        op.execute(sa.text(f"ALTER INDEX {old} RENAME TO {new}"))

    for table, old, new in CONSTRAINTS:
        op.execute(sa.text(f"ALTER TABLE {table} RENAME CONSTRAINT {old} TO {new}"))

    for old, new in SEQUENCES:
        op.execute(sa.text(f"ALTER SEQUENCE {old} RENAME TO {new}"))

    for old, new in ENUM_VALUES:
        op.execute(
            sa.text(f"ALTER TYPE permission_resource_type RENAME VALUE '{old}' TO '{new}'")
        )

    _rename_key(OLD_KEY, NEW_KEY)


def downgrade() -> None:
    """The mirror image, in reverse order.

    Reverse order is not required — renames have no dependencies on each other — but
    it keeps the two halves diffable against one another.
    """
    _rename_key(NEW_KEY, OLD_KEY)

    for old, new in ENUM_VALUES:
        op.execute(
            sa.text(f"ALTER TYPE permission_resource_type RENAME VALUE '{new}' TO '{old}'")
        )

    for old, new in SEQUENCES:
        op.execute(sa.text(f"ALTER SEQUENCE {new} RENAME TO {old}"))

    for table, old, new in CONSTRAINTS:
        op.execute(sa.text(f"ALTER TABLE {table} RENAME CONSTRAINT {new} TO {old}"))

    for old, new in INDEXES:
        op.execute(sa.text(f"ALTER INDEX {new} RENAME TO {old}"))

    for table in TABLE_ID_COLUMNS:
        op.alter_column(table, "table_id", new_column_name="list_id")

    for old, new in TABLES:
        op.rename_table(new, old)
