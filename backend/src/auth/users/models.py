from typing import Optional

from sqlalchemy.orm import Mapped, mapped_column, relationship
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
    groups: Mapped[list["UserGroup"]] = relationship(
        "UserGroup",
        secondary=user_group_association,
        back_populates="users",
    )


class UserGroup(BaseModel):
    __tablename__ = "user_groups"

    name: Mapped[str] = mapped_column(sa.String(50), unique=True)
    description: Mapped[Optional[str]] = mapped_column(sa.Text)
    users: Mapped[list[User]] = relationship(
        "User",
        secondary=user_group_association,
        back_populates="groups",
    )
