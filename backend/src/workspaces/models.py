from __future__ import annotations
from typing import TYPE_CHECKING, Optional

from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import JSONB
import sqlalchemy as sa

from src.core.models import BaseModel

if TYPE_CHECKING:
    from src.auth.users.models import User


class Workspace(BaseModel):
    __tablename__ = "workspaces"

    owner_id: Mapped[int] = mapped_column(
        sa.Integer, sa.ForeignKey("users.id"), index=True
    )
    name: Mapped[str] = mapped_column(sa.String(120))
    display_name: Mapped[str] = mapped_column(sa.String(255))
    description: Mapped[Optional[str]] = mapped_column(sa.Text, nullable=True)
    settings: Mapped[dict] = mapped_column(JSONB, default=dict)

    owner: Mapped["User"] = relationship("User")

    __table_args__ = (
        sa.Index(
            "uq_workspaces_owner_name",
            "owner_id",
            "name",
            unique=True,
            postgresql_where=sa.text("deleted_at IS NULL"),
        ),
    )
