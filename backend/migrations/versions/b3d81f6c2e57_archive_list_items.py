"""archive list items, and keep them out of the index the grid reads

Revision ID: b3d81f6c2e57
Revises: c7e2b95a10d4
Create Date: 2026-08-06 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b3d81f6c2e57'
down_revision: Union[str, Sequence[str], None] = 'c7e2b95a10d4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # A second lifecycle axis beside `deleted_at`: archived records drop out of every
    # normal read but keep their row, their references and their unique values. No
    # retention window, so nothing purges them for being archived.
    op.add_column(
        'list_items',
        sa.Column('archived_at', sa.DateTime(timezone=True), nullable=True),
    )

    # Recreate the grid's covering index partial on *both* flags. A partial index is
    # only usable when the query's predicate implies the index's, so this predicate
    # and `ListItemService._select` must say the same thing — otherwise archived rows
    # stay in the index every hot read walks, and archiving buys nothing.
    op.drop_index('ix_list_items_list_id_id', 'list_items')
    op.create_index(
        'ix_list_items_list_id_id',
        'list_items',
        ['list_id', 'id'],
        postgresql_where='deleted_at IS NULL AND archived_at IS NULL',
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_list_items_list_id_id', 'list_items')
    op.create_index(
        'ix_list_items_list_id_id',
        'list_items',
        ['list_id', 'id'],
        postgresql_where='deleted_at IS NULL',
    )
    op.drop_column('list_items', 'archived_at')
