"""add is_system to user_groups

Revision ID: a7d1e9c3b52f
Revises: f2c8a1b4d6e9
Create Date: 2026-07-16 00:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a7d1e9c3b52f'
down_revision: Union[str, Sequence[str], None] = 'f2c8a1b4d6e9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Marks the seeded "Authenticated Users" group (and any future system group).
    # server_default false so existing rows are ordinary groups; no backfill needed.
    op.add_column(
        'user_groups',
        sa.Column(
            'is_system',
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('user_groups', 'is_system')
