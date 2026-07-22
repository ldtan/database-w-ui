"""add is_system to workspaces

Revision ID: d5e2f8a1c9b7
Revises: 1f1e7ff38afd
Create Date: 2026-07-23 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd5e2f8a1c9b7'
down_revision: Union[str, Sequence[str], None] = '1f1e7ff38afd'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Marks the per-user "Incoming" workspace that transferred lists land in.
    # server_default false so every existing workspace stays ordinary; no backfill
    # needed, and inbox workspaces are created lazily on the first transfer.
    op.add_column(
        'workspaces',
        sa.Column(
            'is_system',
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('workspaces', 'is_system')
