"""backfill a view form onto every existing list

New lists get one from ``ListService._provision_defaults``; lists that already
existed need it inserted, or their records have no detail panel to open (the eye
action hides itself when no view form is visible, which is also how un-sharing
one works — so a missing row is indistinguishable from a revoked grant).

The config is ``{"fields": []}``, the documented "all columns" convention
(data-model §8), which is what makes the seeded form show audit and calculated
columns without naming any of them.

Two lists are skipped: one that already has an active ``view``-type form (this
revision re-running), and one that already has a form slugged ``view`` — the
partial unique index ``uq_forms_list_name`` would reject a second, and failing
the whole migration over a name collision is worse than that one list going
without. Its owner can rename the other form and re-run.

Separate from b8e3f1c07a45 on purpose: a value added by ALTER TYPE is not usable
by the transaction that added it.

Revision ID: d2f6a8c41b93
Revises: b8e3f1c07a45
Create Date: 2026-07-27 00:00:01.000000

"""
from datetime import datetime, timezone
from typing import Sequence, Union
from uuid import uuid7

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ENUM, JSONB

# revision identifiers, used by Alembic.
revision: str = 'd2f6a8c41b93'
down_revision: Union[str, Sequence[str], None] = 'b8e3f1c07a45'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# A lightweight handle rather than the application model: ``create_type=False``
# so the enum is bound as form_type and not re-created, and ``id`` is left out
# so the table's sequence assigns it.
forms_t = sa.table(
    "forms",
    sa.column("uuid", sa.Uuid),
    sa.column("list_id", sa.Integer),
    sa.column("name", sa.String),
    sa.column("display_name", sa.String),
    sa.column("type", ENUM(name="form_type", create_type=False)),
    sa.column("is_default", sa.Boolean),
    sa.column("config", JSONB),
    sa.column("permissions_inherit", sa.Boolean),
    sa.column("created_at", sa.DateTime(timezone=True)),
    sa.column("updated_at", sa.DateTime(timezone=True)),
)


def upgrade() -> None:
    bind = op.get_bind()

    taken = set(
        bind.execute(
            sa.text(
                "SELECT DISTINCT list_id FROM forms "
                "WHERE deleted_at IS NULL AND (type = 'view' OR name = 'view')"
            )
        ).scalars()
    )
    list_ids = [
        list_id
        for list_id in bind.execute(
            sa.text("SELECT id FROM lists WHERE deleted_at IS NULL ORDER BY id")
        ).scalars()
        if list_id not in taken
    ]
    if not list_ids:
        return

    now = datetime.now(timezone.utc)
    bind.execute(
        sa.insert(forms_t),
        [
            {
                # uuid7 like every other row this app writes (src/core/mixins.py),
                # not gen_random_uuid(): a v4 outlier in a table of v7s is a
                # needless difference.
                "uuid": uuid7(),
                "list_id": list_id,
                "name": "view",
                "display_name": "View",
                "type": "view",
                "is_default": True,
                "config": {"fields": []},
                "permissions_inherit": True,
                "created_at": now,
                "updated_at": now,
            }
            for list_id in list_ids
        ],
    )


def downgrade() -> None:
    # Only the rows this revision could have written. A form someone renamed or
    # re-typed since is not ours to remove. Any grant targeted at one of these is
    # left behind — grants carry no FK to their resource (known-gaps §3); it
    # resolves to nothing and `sweep_orphan_grants` collects it.
    op.execute(
        "DELETE FROM forms WHERE type = 'view' AND name = 'view' AND is_default"
    )
