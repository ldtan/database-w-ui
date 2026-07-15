"""add calculated column type

Hand-written for the same reason as the link and user/group revisions:
autogenerate diffs tables and columns, not enum labels, so it emits an empty
upgrade() for a new ColumnType member. A `calculated` column stores no value —
it holds a formula in its config and is computed at read time — so there is no
table or column change here, only the new enum label.

Revision ID: e4b7c1a2d3f5
Revises: daa34e0fe344
Create Date: 2026-07-15 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'e4b7c1a2d3f5'
down_revision: Union[str, Sequence[str], None] = 'daa34e0fe344'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ALTER TYPE ... ADD VALUE cannot run inside a transaction block that later
    # uses the new value, so it needs Alembic's autocommit escape.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE column_type ADD VALUE IF NOT EXISTS 'calculated'")


def downgrade() -> None:
    # Postgres has no DROP VALUE; removing a label means rebuilding the type.
    # One-way on purpose.
    pass
