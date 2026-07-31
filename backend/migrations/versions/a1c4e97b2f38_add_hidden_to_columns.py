"""add hidden to columns

Revision ID: a1c4e97b2f38
Revises: b3f9d47c1a06
Create Date: 2026-08-01 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1c4e97b2f38'
down_revision: Union[str, Sequence[str], None] = 'b3f9d47c1a06'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Marks a column that exists but is not yet part of the list's shape — today
    # only a type conversion's target, while it is being backfilled. server_default
    # false so every existing column stays visible; no backfill needed.
    op.add_column(
        'columns',
        sa.Column(
            'hidden',
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('columns', 'hidden')
