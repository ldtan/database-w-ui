"""index list_items and permission_grants the way reads ask for them

Revision ID: c7e2b95a10d4
Revises: a1c4e97b2f38
Create Date: 2026-08-05 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'c7e2b95a10d4'
down_revision: Union[str, Sequence[str], None] = 'a1c4e97b2f38'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # The grid's page: one list's live records in id order. Without this the planner
    # prefers the primary key, which is already in id order, and discards every row
    # belonging to another list — at 100k records over 50 lists, 9,850 rows thrown
    # away to return 100. `list_id` alone cannot help, because it gives no ordering.
    op.create_index(
        'ix_list_items_list_id_id',
        'list_items',
        ['list_id', 'id'],
        postgresql_where='deleted_at IS NULL',
    )

    # The collection-visibility filters ask what one principal may see. The unique
    # indexes lead with resource_type, which has five values, so answering that meant
    # scanning a whole fifth of the index. Principal first turns 33 buffers into 2.
    op.create_index(
        'ix_permission_grants_user_resource_type',
        'permission_grants',
        ['user_id', 'resource_type'],
        postgresql_where='user_id IS NOT NULL',
    )
    op.create_index(
        'ix_permission_grants_group_resource_type',
        'permission_grants',
        ['group_id', 'resource_type'],
        postgresql_where='group_id IS NOT NULL',
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_permission_grants_group_resource_type', 'permission_grants')
    op.drop_index('ix_permission_grants_user_resource_type', 'permission_grants')
    op.drop_index('ix_list_items_list_id_id', 'list_items')
