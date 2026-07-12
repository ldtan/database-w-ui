"""add link column type

Hand-written: `alembic revision --autogenerate` diffs tables and columns, not
enum labels, so it emits an empty upgrade() for a new ColumnType member. See
the migration caveats in the root README.

Revision ID: b1f7c4a90e12
Revises: 6e52a932843c
Create Date: 2026-07-12 15:40:00.000000

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'b1f7c4a90e12'
down_revision: Union[str, Sequence[str], None] = '6e52a932843c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ALTER TYPE ... ADD VALUE cannot run inside a transaction block that later
    # uses the new value, so it needs Alembic's autocommit escape.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE column_type ADD VALUE IF NOT EXISTS 'link'")


def downgrade() -> None:
    # Postgres has no DROP VALUE; removing a label means rebuilding the type,
    # which would fail anyway while any column still uses it. One-way on purpose.
    pass
