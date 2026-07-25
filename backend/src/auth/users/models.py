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
    groups: Mapped[list["UserGroup"]] = relationship(
        "UserGroup",
        secondary=user_group_association,
        back_populates="users",
    )


# The virtual group every authenticated account implicitly belongs to. Seeded once
# (see auth/seed.py), never given membership rows: the resolver injects it for every
# user (see PermissionService._group_ids), so a grant to it means "everyone".
AUTHENTICATED_USERS_NAME = "Authenticated Users"


class UserGroup(BaseModel):
    __tablename__ = "user_groups"

    name: Mapped[str] = mapped_column(sa.String(50), unique=True)
    description: Mapped[Optional[str]] = mapped_column(sa.Text)
    # System groups are seeded, not user-created: immutable, undeletable, and their
    # membership is implicit (no rows in user_group_association). Currently just
    # "Authenticated Users".
    is_system: Mapped[bool] = mapped_column(
        sa.Boolean, default=False, server_default=sa.false(), nullable=False
    )
    users: Mapped[list[User]] = relationship(
        "User",
        secondary=user_group_association,
        back_populates="groups",
    )
