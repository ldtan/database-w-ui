from typing import Optional

from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import JSONB
import sqlalchemy as sa

from src.core.models import BaseModel
from src.infrastructure.database import Base


user_group_association = sa.Table(
    "user_group_association",
    Base.metadata,
    sa.Column(
        "user_id",
        sa.Integer,
        sa.ForeignKey("users.id"),
        primary_key=True,
    ),
    sa.Column(
        "group_id",
        sa.Integer,
        sa.ForeignKey("user_groups.id"),
        primary_key=True,
    ),
)


class User(BaseModel):
    __tablename__ = "users"

    username: Mapped[str] = mapped_column(sa.String(50), unique=True)
    email: Mapped[str] = mapped_column(sa.String(120), unique=True)
    password: Mapped[str] = mapped_column(sa.String(255))
    is_active: Mapped[bool] = mapped_column(sa.Boolean, default=True)
    # Administers accounts and groups. Orthogonal to the permission model, which
    # governs workspaces/lists/records — a superuser is not automatically granted
    # access to other people's data.
    is_superuser: Mapped[bool] = mapped_column(
        sa.Boolean, default=False, server_default=sa.false(), nullable=False
    )
    # UI preferences that should follow the account rather than the browser — theme
    # and light/dark today. A blob rather than columns because these are read as a
    # unit on every /me and never queried individually; a blob on `users` rather than
    # a side table because a join on the most-requested row in the app buys nothing
    # for a handful of scalars. Written by merge (see UserService.update_settings),
    # so an unrecognised key from a newer build survives a rollback instead of being
    # dropped by the first save from the older one.
    settings: Mapped[dict] = mapped_column(JSONB, default=dict)
    # Deleted groups are filtered out of the join rather than out of each caller.
    # Membership rows survive a delete now — that is what lets a restore bring the
    # group back whole — so "is it still live?" has to be asked somewhere, and asking
    # it here means every read of this relationship gets it for free. The permission
    # resolver asks separately (`PermissionService._group_ids`) because it queries the
    # association table directly, and that one is a security boundary rather than a
    # display detail.
    groups: Mapped[list["UserGroup"]] = relationship(
        "UserGroup",
        secondary=user_group_association,
        back_populates="users",
        secondaryjoin=lambda: sa.and_(
            UserGroup.id == user_group_association.c.group_id,
            UserGroup.deleted_at.is_(None),
        ),
    )


# The virtual group every authenticated account implicitly belongs to. Seeded once
# (see auth/seed.py), never given membership rows: the resolver injects it for every
# user (see PermissionService._group_ids), so a grant to it means "everyone".
#
# Two constants because a group carries two identifiers (see below). The slug is what
# code matches on — the seed, the reserved-name check — and is the one that must never
# drift; the display is only ever shown to a person.
AUTHENTICATED_USERS_NAME = "authenticated_users"
AUTHENTICATED_USERS_DISPLAY_NAME = "Authenticated Users"


class UserGroup(BaseModel):
    __tablename__ = "user_groups"

    # The same split every workspace, list, column, view and form carries: `name` is
    # the code-facing slug, `display_name` is what a person reads. Groups got it late
    # because `name` was doing both jobs *and* serving as a wire identifier — the
    # `groups` column of a batch-user CSV and the exported value of a `group` cell
    # both resolve against it — so a cosmetic rename silently invalidated saved files.
    # Now the rename has somewhere to land that no file refers to.
    name: Mapped[str] = mapped_column(sa.String(63), unique=True)
    # Not unique, matching lists. Two groups may therefore read alike in a picker,
    # which is why SharePanel renders the slug beneath the label: choosing the wrong
    # group there grants access, so the tie-breaker has to be visible.
    display_name: Mapped[str] = mapped_column(sa.String(255))
    description: Mapped[Optional[str]] = mapped_column(sa.Text)
    # System groups are seeded, not user-created: immutable, undeletable, and their
    # membership is implicit (no rows in user_group_association). Currently just
    # "Authenticated Users".
    is_system: Mapped[bool] = mapped_column(
        sa.Boolean, default=False, server_default=sa.false(), nullable=False
    )
    # The mirror of User.groups above: a deleted account stops appearing in its groups'
    # membership without its membership rows being thrown away.
    users: Mapped[list[User]] = relationship(
        "User",
        secondary=user_group_association,
        back_populates="groups",
        secondaryjoin=lambda: sa.and_(
            User.id == user_group_association.c.user_id,
            User.deleted_at.is_(None),
        ),
    )
