"""add expires_at to permission_grants

Revision ID: f2c8a1b4d6e9
Revises: e4b7c1a2d3f5
Create Date: 2026-07-16 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f2c8a1b4d6e9'
down_revision: Union[str, Sequence[str], None] = 'e4b7c1a2d3f5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # A grant may now carry an expiry; NULL keeps the existing "never expires"
    # behaviour, so no backfill is needed.
    op.add_column(
        'permission_grants',
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('permission_grants', 'expires_at')
