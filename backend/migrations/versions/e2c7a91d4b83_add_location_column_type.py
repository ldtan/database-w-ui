"""add location column type

Hand-written: `alembic revision --autogenerate` diffs tables and columns, not
enum labels, so it emits an empty upgrade() for a new ColumnType member. See
the migration caveats in the root README.

Nothing else to migrate, even though a location cell is the first one whose value
is a JSON *object*: records live in a JSONB blob, so a new value shape costs no
schema change (data-model-design D1).

Revision ID: e2c7a91d4b83
Revises: d6b2f83e05c1
Create Date: 2026-08-18 13:00:00.000000

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'e2c7a91d4b83'
down_revision: Union[str, Sequence[str], None] = 'd6b2f83e05c1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ALTER TYPE ... ADD VALUE cannot run inside a transaction block that later
    # uses the new value, so it needs Alembic's autocommit escape.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE column_type ADD VALUE IF NOT EXISTS 'location'")


def downgrade() -> None:
    # Postgres has no DROP VALUE; removing a label means rebuilding the type,
    # which would fail anyway while any column still uses it. One-way on purpose.
    pass
