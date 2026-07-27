"""add view form type

Hand-written for the same reason as the link, user/group and calculated
revisions: autogenerate diffs tables and columns, not enum labels, so it emits
an empty upgrade() for a new FormType member. A ``view`` form is a display-only
field layout — same table, same config shape, so there is nothing here but the
label. The rows themselves are backfilled by the next revision, which has to be
a separate one: a value added by ALTER TYPE cannot be used by the transaction
that added it.

Revision ID: b8e3f1c07a45
Revises: c7b4e1f9a3d2
Create Date: 2026-07-27 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'b8e3f1c07a45'
down_revision: Union[str, Sequence[str], None] = 'c7b4e1f9a3d2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ALTER TYPE ... ADD VALUE cannot run inside a transaction block that later
    # uses the new value, so it needs Alembic's autocommit escape.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE form_type ADD VALUE IF NOT EXISTS 'view'")


def downgrade() -> None:
    # Postgres has no DROP VALUE; removing a label means rebuilding the type.
    # One-way on purpose.
    pass
