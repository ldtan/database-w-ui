"""merge long_text into text + config.text_mode

`text` and `long_text` stored the same thing — a plain string — and were treated
identically by every validator, filter, sort, search, import and export. The only
difference in the whole stack was which editor the UI drew. That is a property of
how a column is *edited*, so it moves into the column's config as
``text_mode: "single" | "multiline"``, and `long_text` stops being a type.

Every existing `long_text` column becomes a `text` column with
``text_mode: "multiline"``, which is the same column with the same editor. Record
cells are untouched: both types already held identical plain strings, so there is
nothing to convert — this is a config rewrite, not a data conversion.

Soft-deleted columns are included on purpose. Restoring one must not resurrect a
type the ``ColumnType`` enum no longer has, which would raise on the next read.

**The `long_text` label stays on the Postgres `column_type` enum, deliberately.**
Postgres has no ``DROP VALUE``, so removing it would mean building a new enum
type, moving the column onto it and dropping the old one — real risk to buy
nothing, since after the UPDATE below no row uses the label. It is safe to leave:
``schema_check.find_drift`` reports only what the models expect and the database
lacks, treating extras as harmless leftovers, and Alembic's ``compare_metadata``
(what ``tests/test_migrations.py`` asserts on) does not diff enum labels at all.
So please do not "tidy" it away.

Hand-written: autogenerate can see neither enum labels nor inside JSONB.

Revision ID: f7a3c81b5d24
Revises: d2f6a8c41b93
Create Date: 2026-07-27 12:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'f7a3c81b5d24'
down_revision: Union[str, Sequence[str], None] = 'd2f6a8c41b93'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # jsonb_set with create_missing (the default) adds the key, and coalesce
    # covers a column whose config is NULL rather than {}.
    op.execute(
        sa.text(
            """
            UPDATE columns
               SET type = 'text',
                   config = jsonb_set(
                       coalesce(config, '{}'::jsonb),
                       '{text_mode}',
                       '"multiline"'::jsonb
                   )
             WHERE type = 'long_text'
            """
        )
    )


def downgrade() -> None:
    # One way. A `text` column with text_mode "multiline" is now indistinguishable
    # from one created that way after this migration, so reversing would turn
    # columns that were never `long_text` into it. The label is still on the enum,
    # so a hand-written reversal is possible if it is ever actually wanted.
    pass
