"""add settings to users

Revision ID: c7b4e1f9a3d2
Revises: a9c3f5e7d2b8
Create Date: 2026-07-26 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'c7b4e1f9a3d2'
down_revision: Union[str, Sequence[str], None] = 'a9c3f5e7d2b8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Per-account UI preferences (theme, light/dark), so they follow the user to
    # whatever device they log in on. server_default '{}' so existing accounts land
    # on an empty blob and read as the schema defaults — no backfill, and no
    # nullable column to null-check at every read site.
    op.add_column(
        'users',
        sa.Column(
            'settings',
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('users', 'settings')
