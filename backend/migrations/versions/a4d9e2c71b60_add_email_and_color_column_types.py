"""add email and color column types

Hand-written: `alembic revision --autogenerate` diffs tables and columns, not
enum labels, so it emits an empty upgrade() for a new ColumnType member. See
the migration caveats in the root README.

Revision ID: a4d9e2c71b60
Revises: b3d81f6c2e57
Create Date: 2026-08-18 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'a4d9e2c71b60'
down_revision: Union[str, Sequence[str], None] = 'b3d81f6c2e57'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ALTER TYPE ... ADD VALUE cannot run inside a transaction block that later
    # uses the new value, so it needs Alembic's autocommit escape.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE column_type ADD VALUE IF NOT EXISTS 'email'")
        op.execute("ALTER TYPE column_type ADD VALUE IF NOT EXISTS 'color'")


def downgrade() -> None:
    # Postgres has no DROP VALUE; removing a label means rebuilding the type,
    # which would fail anyway while any column still uses it. One-way on purpose.
    pass
