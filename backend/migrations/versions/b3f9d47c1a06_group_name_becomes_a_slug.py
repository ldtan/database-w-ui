"""a group's name becomes a slug, its label moves to display_name

``user_groups.name`` was free text doing three jobs: the label the UI shows, the
string search matches, and -- the problem -- the key two importers resolve against
(a batch-user CSV's ``groups`` column, and the exported value of a ``group`` cell).
A cosmetic rename therefore invalidated saved files silently. This gives groups the
same split every list, column, view and form already has: ``name`` is a ``NameSlug``,
``display_name`` is what a person reads.

Nothing stores a group *name* in JSONB, which keeps this small. ``group`` cells hold
uuids, saved view filter conditions on principal columns hold uuids, and permission
grants reference ``group_id`` -- so unlike ``a9c3f5e7d2b8`` there are no record or
view rewrites here, only this one table.

Revision ID: b3f9d47c1a06
Revises: eadb8a48f310
Create Date: 2026-07-31 12:00:00.000000

"""
import re
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'b3f9d47c1a06'
down_revision: Union[str, Sequence[str], None] = 'eadb8a48f310'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# The system group is pinned rather than slugified, so the group every grant-to-
# everyone depends on cannot drift if the transform below is ever adjusted. Spelled
# literally rather than imported from src.auth.users.models for the same reason the
# slugifier is copied: a migration has to keep doing what it did when it ran.
AUTHENTICATED_USERS_OLD_NAME = "Authenticated Users"
AUTHENTICATED_USERS_NAME = "authenticated_users"

# NameSlug's max_length (src/core/schemas.py). The dedupe suffix has to fit inside
# it, so slugs are cut short by _SUFFIX_ROOM before a suffix is appended.
MAX_LENGTH = 63
_SUFFIX_ROOM = 5  # "_999" and a byte of slack
_NON_SLUG = re.compile(r"[^a-z0-9]+")

groups_t = sa.table(
    "user_groups",
    sa.column("id", sa.Integer),
    sa.column("name", sa.String),
    sa.column("display_name", sa.String),
)


def _slugify(value: str, max_length: int = MAX_LENGTH) -> str:
    """A frozen copy of src.core.utils.slugify_name.

    Copied rather than imported deliberately. A migration is a record of what was
    done to a database on a particular day; if it called into application code, a
    later tweak to the transform would silently change what re-running this on a
    fresh database produces, and two deployments would disagree about what a group
    is called. The application copy is the one that may evolve.
    """
    slug = _NON_SLUG.sub("_", value.lower()).strip("_")
    if not slug:
        return "item"
    if not slug[0].isalpha():
        slug = f"x_{slug}"
    return slug[:max_length].rstrip("_") or "item"


def _dedupe(slug: str, taken: set[str], group_id: int, original: str) -> str:
    """`slug`, or the first free `slug_2`, `slug_3`, ... after it.

    Both directions need this. Going up, two distinct free-text names can slugify
    identically ("Grade-7 Teachers" and "Grade 7 teachers"); coming back down, two
    distinct display names can collide once truncated to the old 50 characters.
    Either way `name` is unique, so an unresolved collision is a failed migration.
    """
    if slug not in taken:
        return slug
    for n in range(2, 1000):
        candidate = f"{slug}_{n}"
        if candidate not in taken:
            return candidate
    raise RuntimeError(
        f"Could not find a free name for group {group_id} ({original!r}) "
        f"after 998 attempts at {slug!r}"
    )


def upgrade() -> None:
    bind = op.get_bind()

    # 1. display_name arrives nullable so existing rows survive the DDL, and is
    #    backfilled from today's name -- which *is* the human label, and is globally
    #    unique, so the copy needs no deduplication of its own.
    op.add_column(
        "user_groups", sa.Column("display_name", sa.String(255), nullable=True)
    )
    bind.execute(
        sa.update(groups_t)
        .where(groups_t.c.display_name.is_(None))
        .values(display_name=groups_t.c.name)
    )

    # 2. Widen before rewriting: a slug can be longer than the old String(50) once
    #    punctuation becomes underscores ("Year 10 & 11 Form Tutors").
    op.alter_column(
        "user_groups",
        "name",
        existing_type=sa.String(50),
        type_=sa.String(MAX_LENGTH),
        existing_nullable=False,
    )

    # 3. Rewrite each name to its slug. Ordered by id so the oldest group keeps the
    #    clean slug when two collide, which makes the result deterministic rather
    #    than dependent on whatever order Postgres returned rows in.
    #
    #    Soft-deleted groups are included: `name` here is a plain unique constraint
    #    rather than the partial (WHERE deleted_at IS NULL) index the rest of the
    #    schema uses, so a binned group still holds its name against the living (see
    #    auth/users/bin.py) and skipping it would let a live group take a slug the
    #    binned one would collide with on restore.
    rows = bind.execute(
        sa.select(groups_t.c.id, groups_t.c.name).order_by(groups_t.c.id)
    ).all()

    taken: set[str] = set()
    for group_id, name in rows:
        if name == AUTHENTICATED_USERS_OLD_NAME:
            slug = AUTHENTICATED_USERS_NAME
        else:
            slug = _dedupe(
                _slugify(name, MAX_LENGTH - _SUFFIX_ROOM), taken, group_id, name
            )
        taken.add(slug)
        if slug != name:
            bind.execute(
                sa.update(groups_t).where(groups_t.c.id == group_id).values(name=slug)
            )

    # 4. Only now that every row has one.
    op.alter_column("user_groups", "display_name", nullable=False)


def downgrade() -> None:
    """Fold display_name back into name and drop the column.

    Reversible, unlike a9c3f5e7d2b8: display_name holds what name used to. A
    display_name edited after the upgrade becomes the name again, which is what it
    would have been had the split never happened.

    The values move *before* the column narrows, not after: at this point `name`
    holds slugs of up to 63 characters, and asking Postgres to narrow the column
    under them fails outright.
    """
    bind = op.get_bind()

    rows = bind.execute(
        sa.select(groups_t.c.id, groups_t.c.name, groups_t.c.display_name).order_by(
            groups_t.c.id
        )
    ).all()

    taken: set[str] = set()
    for group_id, name, display_name in rows:
        # Truncated to the width `name` is about to have, less the same room for a
        # suffix the upgrade leaves -- and truncation is itself a way for two distinct
        # display names to collide, hence the same dedupe.
        restored = _dedupe(
            (display_name or name)[: 50 - _SUFFIX_ROOM], taken, group_id, display_name
        )
        taken.add(restored)
        if restored != name:
            bind.execute(
                sa.update(groups_t)
                .where(groups_t.c.id == group_id)
                .values(name=restored)
            )

    op.alter_column(
        "user_groups",
        "name",
        existing_type=sa.String(MAX_LENGTH),
        type_=sa.String(50),
        existing_nullable=False,
    )
    op.drop_column("user_groups", "display_name")
