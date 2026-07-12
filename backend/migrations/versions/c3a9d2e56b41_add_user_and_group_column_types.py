"""add user and group column types

Hand-written for the same reason as the link revision: autogenerate diffs tables
and columns, not enum labels, so it emits an empty upgrade() for a new
ColumnType member.

Revision ID: c3a9d2e56b41
Revises: b1f7c4a90e12
Create Date: 2026-07-12 16:20:00.000000

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'c3a9d2e56b41'
down_revision: Union[str, Sequence[str], None] = 'b1f7c4a90e12'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ALTER TYPE ... ADD VALUE cannot run inside a transaction block that later
    # uses the new value, so it needs Alembic's autocommit escape.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE column_type ADD VALUE IF NOT EXISTS 'user'")
        op.execute("ALTER TYPE column_type ADD VALUE IF NOT EXISTS 'group'")


def downgrade() -> None:
    # Postgres has no DROP VALUE; removing a label means rebuilding the type.
    # One-way on purpose.
    pass
