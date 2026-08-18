"""add rating and duration column types

Hand-written: `alembic revision --autogenerate` diffs tables and columns, not
enum labels, so it emits an empty upgrade() for a new ColumnType member. See
the migration caveats in the root README.

Revision ID: c8e1b04f7a29
Revises: a4d9e2c71b60
Create Date: 2026-08-18 11:00:00.000000

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'c8e1b04f7a29'
down_revision: Union[str, Sequence[str], None] = 'a4d9e2c71b60'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ALTER TYPE ... ADD VALUE cannot run inside a transaction block that later
    # uses the new value, so it needs Alembic's autocommit escape.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE column_type ADD VALUE IF NOT EXISTS 'rating'")
        op.execute("ALTER TYPE column_type ADD VALUE IF NOT EXISTS 'duration'")


def downgrade() -> None:
    # Postgres has no DROP VALUE; removing a label means rebuilding the type,
    # which would fail anyway while any column still uses it. One-way on purpose.
    pass
